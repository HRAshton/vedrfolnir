"""Read-only source validation and importer fail-safe regression tests."""
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select

from vedrfolnir.config import settings
from vedrfolnir.db import (CurrentJob, CurrentPlan, HistoricalRun,
                           SyncState, create_session_factory)
from vedrfolnir.service import EngineService
from vedrfolnir.source import CSVSource
from vedrfolnir.source_validation import (
    main, require_valid, validate_records, validate_source,
)


def test_preflight_files_reports_good_contract_without_any_database(tmp_path, capsys):
    root = Path(__file__).resolve().parents[1] / "examples" / "data"
    report = validate_source(CSVSource(root), sample_history=15)
    assert report["ok"]
    assert report["read_only"]
    assert report["datasets"]["historical_jobs"]["rows_checked"] == 15
    assert "no_live_activity_observed" in report["warnings"]
    assert main(["--mode", "files", "--csv-dir", str(root),
                 "--history-sample", "15"]) == 0
    emitted = json.loads(capsys.readouterr().out)
    assert emitted["ok"]
    assert not list(tmp_path.iterdir())


def test_validation_rejects_invalid_timestamps_and_duplicate_rows():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    valid = {
        "run_id": "r1", "job_id": "compile", "plan_id": "p",
        "status": "passed", "started_at": now,
        "finished_at": now + timedelta(minutes=2),
    }
    report = validate_records("historical_jobs", [
        valid, {**valid, "finished_at": "not-a-date"},
        {**valid, "run_id": "r2", "finished_at": now - timedelta(minutes=1)},
    ])
    codes = {issue["code"] for issue in report["errors"]}
    assert {"duplicate_id", "invalid_finished_at", "finished_before_started"} <= codes
    assert not report["ok"]
    with pytest.raises(ValueError, match="Invalid historical_jobs"):
        require_valid("historical_jobs", [{**valid, "status": "queued"}])


def test_preflight_detects_missing_running_start_and_unobserved_queue():
    data = [
        {"run_id": "run-1", "job_id": "compile", "plan_id": "a",
         "status": "running", "started_at": None, "priority": "bad"},
        {"run_id": "run-2", "job_id": "compile", "plan_id": "a",
         "status": "waiting", "started_at": None, "queued_at": None},
    ]
    result = validate_records("live_jobs", data)
    assert {error["code"] for error in result["errors"]} == {
        "running_job_missing_started_at", "invalid_priority",
    }
    assert not result["ok"]


def test_invalid_live_rows_do_not_replace_previous_snapshots(tmp_path):
    eng = create_engine(f"sqlite+pysqlite:///{tmp_path/'state.sqlite'}")
    factory = create_session_factory(eng)
    service = EngineService(factory, replace(settings, mode="files"))
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    service.update_live(
        [{"plan_id": "a", "status": "running", "started_at": now}],
        [{"run_id": "r1", "plan_id": "a", "job_id": "test",
          "status": "running", "started_at": now}],
    )
    with pytest.raises(ValueError, match="Invalid live_jobs"):
        service.update_live(
            [{"plan_id": "b", "status": "running", "started_at": now}],
            [{"run_id": "r2", "plan_id": "b", "job_id": "test",
              "status": "running", "started_at": None}],
        )
    with factory() as db:
        assert db.get(CurrentPlan, "a") is not None
        assert db.get(CurrentPlan, "b") is None
        assert db.get(CurrentJob, "r1") is not None
        assert db.get(CurrentJob, "r2") is None
    eng.dispose()


def test_invalid_historical_page_does_not_advance_cursor(tmp_path):
    eng = create_engine(f"sqlite+pysqlite:///{tmp_path/'bad.sqlite'}")
    factory = create_session_factory(eng)
    service = EngineService(factory, replace(settings, mode="files"))
    class BrokenSource:
        def historical(self, *args):
            return [{"run_id": "r", "job_id": "compile", "plan_id": "p",
                     "status": "passed",
                     "started_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                     "finished_at": "bad-time"}]
    with pytest.raises(ValueError, match="Invalid historical_jobs"):
        service.import_history(BrokenSource())
    with factory() as db:
        assert db.get(SyncState, "history_cursor") is None
        assert list(db.scalars(select(HistoricalRun))) == []
    eng.dispose()


def test_failed_preflight_exits_without_echoing_sensitive_source_values(tmp_path, capsys):
    folder = tmp_path / "files"
    folder.mkdir()
    (folder / "historical_jobs.csv").write_text(
        "run_id,job_id,plan_id,status,started_at,finished_at\n"
        "secret-id,job,p,passed,not-a-time,2026-01-01T00:00:00Z\n")
    (folder / "plans.csv").write_text("plan_id,status,started_at,finished_at\n")
    (folder / "live_jobs.csv").write_text(
        "run_id,plan_id,job_id,status,started_at,finished_at\n")
    assert main(["--mode", "files", "--csv-dir", str(folder)]) == 2
    output = capsys.readouterr().out
    assert "secret-id" not in output
    assert "invalid_started_at" in output
