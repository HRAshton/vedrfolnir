"""Pure offline smoke test for the MSSQL adapter boundary and cursor."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine, select, func
from vedrfolnir.config import settings
from vedrfolnir.db import create_session_factory, HistoricalRun, CurrentPlan, CurrentJob

# Do not require a live MSSQL instance in unit tests.

class FakeSource:
    def __init__(self):
        self.end = datetime(2026, 10, 10, 10, tzinfo=timezone.utc)
        self.calls = []

    def historical(self, cursor_finished_at, cursor_run_id, batch_limit):
        self.calls.append((cursor_finished_at, cursor_run_id))
        data = [
            {"run_id": str(i), "plan_id": "past-1", "job_id": "install",
             "status": "success", "started_at": self.end-timedelta(minutes=i*5),
             "finished_at": self.end, "version": "4.3", "db_type": "mssql", "vm_name": "vm1"}
            for i in (1, 2, 3)
        ]
        return [r for r in data if (r["finished_at"].replace(tzinfo=None), r["run_id"])
                > (cursor_finished_at, cursor_run_id)][:batch_limit]


def test_history_cursor_and_live_swap(tmp_path):
    # The production package contains River; this test only exercises importer.
    try:
        from vedrfolnir.service import EngineService
    except ModuleNotFoundError as e:
        if e.name == "river":
            import pytest
            pytest.skip("River not installed in source-authoring environment")
        raise
    db = create_engine(f"sqlite+pysqlite:///{tmp_path/'import.sqlite'}")
    session_factory = create_session_factory(db)
    service = EngineService(session_factory, replace(settings, mode="mssql", import_batch=2, import_limit_per_poll=10))
    source = FakeSource()
    assert service.import_history(source) == 3
    assert service.import_history(source) == 0
    with session_factory() as s:
        assert s.scalar(select(func.count(HistoricalRun.run_id))) == 3
        assert set(s.scalars(select(HistoricalRun.status))) == {"passed"}
    service.update_live(
        [{"plan_id": "live", "status": "queued", "created_at": datetime.now(timezone.utc)}],
        [{"run_id": "a", "plan_id": "live", "job_id": "install", "status": "waiting",
          "started_at": None, "finished_at": None}],
    )
    with session_factory() as s:
        assert s.get(CurrentPlan, "live").status == "queued"
        assert s.get(CurrentJob, "a").status == "queued"
    db.dispose()


def test_files_mode_imports_csv_without_mssql(tmp_path):
    from pathlib import Path
    from vedrfolnir.service import EngineService
    root = Path(__file__).resolve().parents[1]
    database = create_engine(f"sqlite+pysqlite:///{tmp_path/'files.sqlite'}")
    session_factory = create_session_factory(database)
    config = replace(settings, mode="files", csv_dir=str(root/'examples'/'data'),
                     import_batch=50, import_limit_per_poll=200)
    service = EngineService(session_factory, config)
    first = service.tick()
    second = service.tick()
    assert first["imported"] > 0
    assert second["imported"] == 0
    with session_factory() as s:
        assert s.scalar(select(func.count(HistoricalRun.run_id))) == first["imported"]
    database.dispose()



def test_pool_contract_is_imported_in_file_mode_and_refreshes_declared_limits(tmp_path):
    from vedrfolnir.db import SchedulerPool
    from vedrfolnir.source import CSVSource
    from vedrfolnir.service import EngineService
    now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "scheduler_pools.csv").write_text(
        "sched_pool_id,sched_capacity_slots\nmssql,3\nmobile,0\n")
    (source_root / "historical_jobs.csv").write_text(
        "run_id,plan_id,job_id,status,started_at,finished_at,sched_pool_id\n"
        "old,past,unit,passed,2026-10-10T09:00:00Z,"
        "2026-10-10T09:05:00Z,mssql\n")
    (source_root / "live_jobs.csv").write_text(
        "run_id,plan_id,job_id,status,started_at,finished_at,queued_at,sched_pool_id\n"
        "active-job,current,unit,queued,,,2026-10-10T12:00:00Z,mssql\n")
    (source_root / "plans.csv").write_text(
        "plan_id,status,started_at,finished_at\n"
        "current,running,2026-10-10T12:00:00Z,\n")
    source = CSVSource(source_root)
    db = create_engine(f"sqlite+pysqlite:///{tmp_path/'pools.sqlite'}")
    factory = create_session_factory(db)
    service = EngineService(factory, replace(settings, mode="files",
        csv_dir=str(source_root)))
    service.import_history(source)
    plans, jobs = source.live(now.replace(tzinfo=None))
    service.update_live(plans, jobs, source.pools())
    with factory() as s:
        assert s.get(HistoricalRun, "old").sched_pool_id == "mssql"
        assert s.get(CurrentJob, "active-job").sched_pool_id == "mssql"
        assert s.get(SchedulerPool, "mssql").sched_capacity_slots == 3
        assert s.get(SchedulerPool, "mobile").sched_capacity_slots == 0
    (source_root / "scheduler_pools.csv").write_text(
        "sched_pool_id,sched_capacity_slots\nmssql,5\n")
    service.update_live(plans, jobs, source.pools())
    with factory() as s:
        assert s.get(SchedulerPool, "mssql").sched_capacity_slots == 5
        assert s.get(SchedulerPool, "mobile") is None
    db.dispose()
