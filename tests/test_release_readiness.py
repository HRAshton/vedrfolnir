"""Prerelease contract: version consistency, CSV, SQLite upgrade and recovery."""
from __future__ import annotations

import sqlite3
import tomllib
import pytest
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
from pathlib import Path

from vedrfolnir import __version__
from vedrfolnir.api import app
from vedrfolnir.adaptive import RESIDUAL_MODEL_ID
from vedrfolnir.db import (
    CurrentJob, HistoricalRun, ModelCheckpoint, SchedulerPool,
    SCHEMA_VERSION, create_session_factory, make_engine,
)
from vedrfolnir.source import CSVSource
from vedrfolnir.source_validation import validate_source

ROOT = Path(__file__).resolve().parents[1]


def test_release_version_is_consistent():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "version" in project["project"]["dynamic"]
    assert "version" not in project["project"]
    assert project["tool"]["setuptools"]["dynamic"]["version"]["attr"] == "vedrfolnir.__version__"
    assert __version__ == app.version == app.openapi()["info"]["version"] == version("vedrfolnir")


def test_release_csv_fixture_matches_source_contract(tmp_path):
    from scripts.release_smoke import make_csv_fixture
    make_csv_fixture(tmp_path)
    report = validate_source(CSVSource(tmp_path))
    assert report["ok"], report
    assert report["datasets"]["historical_jobs"]["rows_checked"] == 6
    assert report["datasets"]["scheduler_pools"]["rows_checked"] == 1
    assert report["warnings"] == []


def test_upgrade_backup_restore_preserves_history_and_checkpoint(tmp_path):
    database = tmp_path / "old-user.sqlite3"
    url = f"sqlite+pysqlite:///{database}"
    engine = make_engine(url)
    factory = create_session_factory(engine)
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    with factory.begin() as session:
        session.add(HistoricalRun(
            run_id="keep-run", plan_id="old-build", job_id="test",
            status="passed", started_at=now - timedelta(minutes=5),
            finished_at=now, duration_seconds=300, vm_name="vm-old",
        ))
        session.add(CurrentJob(
            run_id="keep-live", plan_id="active", job_id="test",
            status="queued", priority=0,
        ))
        session.add(ModelCheckpoint(
            name=RESIDUAL_MODEL_ID, payload=b"existing-bytes", seen_count=27,
        ))
    engine.dispose()

    # Simulate an existing full pre-pool database; preserve its real rows.
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE historical_runs DROP COLUMN sched_pool_id")
        connection.execute("ALTER TABLE current_jobs DROP COLUMN sched_pool_id")
        connection.execute("DROP TABLE scheduler_pools")
        connection.execute("PRAGMA user_version = 0")
        connection.commit()

    engine = make_engine(url)
    factory = create_session_factory(engine)
    with factory() as session:
        assert session.get(HistoricalRun, "keep-run").sched_pool_id == "general"
        assert session.get(CurrentJob, "keep-live").sched_pool_id == "general"
        checkpoint = session.get(ModelCheckpoint, RESIDUAL_MODEL_ID)
        assert checkpoint.payload == b"existing-bytes"
        assert checkpoint.seen_count == 27
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    # Startup is idempotent, including after upgrades.
    create_session_factory(engine)
    with factory.begin() as session:
        session.add(SchedulerPool(sched_pool_id="android", sched_capacity_slots=0))
    engine.dispose()

    backup = tmp_path / "backup.sqlite3"
    with sqlite3.connect(database) as original, sqlite3.connect(backup) as target:
        original.backup(target)
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute(
            "SELECT seen_count FROM model_checkpoints WHERE name = ?",
            (RESIDUAL_MODEL_ID,),
        ).fetchone()[0] == 27
    restored = make_engine(f"sqlite+pysqlite:///{backup}")
    restored_factory = create_session_factory(restored)
    with restored_factory() as session:
        assert session.get(HistoricalRun, "keep-run") is not None
        assert session.get(CurrentJob, "keep-live") is not None
        assert session.get(ModelCheckpoint, RESIDUAL_MODEL_ID).seen_count == 27
        assert session.get(SchedulerPool, "android").sched_capacity_slots == 0
    restored.dispose()


def test_newer_sqlite_schema_is_rejected_before_mutation(tmp_path):
    database = tmp_path / "future.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 999")
    engine = make_engine(f"sqlite+pysqlite:///{database}")
    try:
        with pytest.raises(RuntimeError, match="newer than supported"):
            create_session_factory(engine)
    finally:
        engine.dispose()
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == 0
