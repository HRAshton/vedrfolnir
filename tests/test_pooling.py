"""Pool discovery, conservative capacity, non-leakage and schedulability."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from vedrfolnir.adaptive import ObservedScheduler
from vedrfolnir.config import settings
from vedrfolnir.db import (Base, CurrentJob, HistoricalRun, SchedulerPool,
                           create_session_factory)
from vedrfolnir.pooling import (
    PoolCapacityCache, capacity_slots, declared_capacities, pool_features, pool_of,
)

NOW = datetime(2026, 1, 5, tzinfo=timezone.utc)


def completed(name, pool, start, end, *, vm="shared"):
    return SimpleNamespace(
        run_id=name, job_id="test", plan_id="old", status="passed",
        vm_name=vm, sched_pool_id=pool,
        started_at=NOW + timedelta(minutes=start),
        finished_at=NOW + timedelta(minutes=end),
    )


def live(name, pool, status="queued", *, started=None, vm="unknown", priority=0):
    return SimpleNamespace(
        run_id=name, job_id="test", plan_id="new", status=status,
        sched_pool_id=pool, vm_name=vm, priority=priority,
        queued_at=NOW - timedelta(minutes=1),
        started_at=started, finished_at=None,
        version="unknown", db_type="unknown",
    )


def test_capacity_refresh_uses_overlap_not_sum_of_individual_vm_peaks():
    history = [
        completed("a", "mssql", -30, -20),
        completed("b", "mssql", -29, -21),
        completed("c", "mssql", -10, -5),
        completed("d", "android", -10, -5),
    ]
    cache = PoolCapacityCache(refresh_seconds=600)
    first = cache.snapshot(history, [live("now", "mssql", "running",
                                         started=NOW - timedelta(minutes=2))],
                           NOW)
    assert first["mssql"]["observed_slots"] == 2
    assert first["mssql"]["capacity_slots"] == 2
    assert first["mssql"]["source"] == "observed"
    assert first["android"]["capacity_slots"] == 1
    assert cache.recalculations == 1
    # Stale historical cache persists for ten minutes, but live capacity
    # and declared limits are read on every call.
    later = cache.snapshot(
        history + [completed("e", "mssql", -4, -1)] * 3,
        [live(str(i), "mssql", "running", started=NOW) for i in range(4)],
        NOW + timedelta(minutes=2), {"mssql": 1})
    assert later["mssql"]["observed_slots"] == 4
    assert later["mssql"]["capacity_slots"] == 1  # authoritative, even if busy
    assert later["mssql"]["running_jobs"] == 4
    assert cache.recalculations == 1
    assert cache.snapshot(history, [], NOW + timedelta(minutes=10))["mssql"]["source"] == "observed"
    assert cache.recalculations == 2


def test_declared_zero_is_not_missing_and_conflicts_rejected():
    assert capacity_slots(0) == 0
    assert capacity_slots(None) is None
    assert capacity_slots("3") == 3
    with pytest.raises(ValueError):
        capacity_slots(-1)
    with pytest.raises(ValueError):
        capacity_slots("1.5")
    with pytest.raises(ValueError):
        capacity_slots(True)
    assert declared_capacities(
        [{"sched_pool_id": "ios", "sched_capacity_slots": 0}]) == {"ios": 0}
    with pytest.raises(ValueError, match="Conflicting"):
        declared_capacities([{"sched_pool_id": "ios", "sched_capacity_slots": 0}],
                            [{"sched_pool_id": "ios", "sched_capacity_slots": 2}])
    with pytest.raises(ValueError):
        pool_of({"sched_pool_id": "bad space"})


def test_pool_scheduler_never_borrows_capacity_from_other_pool():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            rows = [
                completed("old-a", "android", -40, -30, vm="vm-a"),
                completed("old-b", "mssql", -40, -30, vm="vm-s"),
            ]
            for row in rows:
                db.add(HistoricalRun(
                    run_id=row.run_id, plan_id="old", job_id="test",
                    vm_name=row.vm_name, sched_pool_id=row.sched_pool_id,
                    status="passed", started_at=row.started_at,
                    finished_at=row.finished_at, duration_seconds=600,
                    version="unknown", db_type="unknown",
                ))
            db.flush()
            pending = [live("android-1", "android"),
                       live("mssql-1", "mssql"),
                       live("android-2", "android")]
            solver = ObservedScheduler(
                db, NOW, pending, declared_capacities={"android": 1, "mssql": 3})
            assert solver.pool_aware
            projected = solver.project(pending)
            assert len(projected) == 3
            assert projected["mssql-1"] <= projected["android-2"]
            assert projected["android-2"] > projected["android-1"]
            flags = solver.pool_features(pending)
            assert flags["pool_count"] == 2.0
            assert flags["pool_declared_capacity_count"] == 2.0
            assert flags["pool_capacity_slots"] == 4.0
            assert solver.can_estimate_plan(pending, projected)
    finally:
        engine.dispose()


def test_disabled_pool_forces_fallback_even_when_other_pool_has_slots():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            waiting = [live("ios", "ios"), live("linux", "linux")]
            solver = ObservedScheduler(
                db, NOW, waiting, declared_capacities={"ios": 0, "linux": 20})
            projected = solver.project(waiting)
            assert "linux" in projected
            assert "ios" not in projected
            assert not solver.can_estimate_plan(waiting, projected)
            assert solver.estimate_plan(NOW, waiting, projected, 91.0) == 91.0
    finally:
        engine.dispose()


def test_sqlite_existing_schema_gains_optional_pool_columns_without_data_loss(tmp_path):
    database = tmp_path / "old.sqlite"
    from sqlalchemy import create_engine as engine_for
    eng = engine_for(f"sqlite+pysqlite:///{database}")
    with eng.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE historical_runs (run_id VARCHAR PRIMARY KEY)")
        conn.exec_driver_sql(
            "CREATE TABLE current_jobs (run_id VARCHAR PRIMARY KEY)")
    eng.dispose()
    # A full legacy schema exists in actual deployments; use the minimal
    # tables only to validate additive-column migration behavior.
    eng = engine_for(f"sqlite+pysqlite:///{database}")
    try:
        create_session_factory(eng)
        with eng.connect() as conn:
            for table in ("historical_runs", "current_jobs"):
                assert "sched_pool_id" in {
                    x[1] for x in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            assert "scheduler_pools" in {
                x[0] for x in conn.exec_driver_sql("SELECT name FROM sqlite_master "
                                                   "WHERE type='table'")}
    finally:
        eng.dispose()


def test_source_mode_pool_capacity_feature_is_numeric_not_per_pool_model():
    snapshots = {
        "mssql": {"capacity_slots": 8, "running_jobs": 3,
                  "queued_jobs": 9, "source": "observed"},
        "android": {"capacity_slots": 2, "running_jobs": 2,
                    "queued_jobs": 5, "source": "declared"},
    }
    jobs = [live("a", "android"), live("b", "mssql")]
    features = pool_features(snapshots, jobs)
    assert features["pool_capacity_slots"] == 10
    assert features["pool_queue_pressure"] == 1.4
    assert features["pool_declared_capacity_count"] == 1
    assert all(isinstance(x, float) for x in features.values())
