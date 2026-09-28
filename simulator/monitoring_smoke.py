"""Synthetic CSV lifecycle check, not independent accuracy evidence.

Run: python -m simulator.monitoring_smoke --output benchmark-results/monitoring-smoke
Only creates a new output directory; never operates on an existing database.
"""
import argparse
import csv
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import random
import sqlite3
from unittest.mock import patch

from sqlalchemy import select, func

from vedrfolnir.config import settings
from vedrfolnir.db import JobPrediction, create_session_factory, make_engine
from vedrfolnir.job_monitoring import summarize_jobs
from vedrfolnir.service import EngineService

JOB_FIELDS = ["run_id", "plan_id", "job_id", "status", "started_at", "finished_at",
              "queued_at", "vm_name", "sched_pool_id"]
PLAN_FIELDS = ["plan_id", "status", "started_at", "finished_at"]


def run_smoke(output, days=8, seed=20261013, now=None):
    if not 1 <= days <= 60:
        raise ValueError("days must be 1..60")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = output / "source"
    source.mkdir()
    database = output / "monitor.sqlite"
    engine = make_engine(f"sqlite:///{database}")
    factory = create_session_factory(engine)
    config = replace(settings, mode="files", csv_dir=str(source), poll_seconds=120)
    service = EngineService(factory, config)
    now = now or datetime.now(timezone.utc)
    rng = random.Random(seed)
    history = []
    counters = {"recorded": 0, "matched": 0, "pruned": 0}
    failures = 0

    def write(name, fields, rows):
        with (source / f"{name}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def snapshot(jobs):
        write("historical_jobs", JOB_FIELDS, history)
        write("live_jobs", JOB_FIELDS, jobs)
        # Standalone jobs exercise the complete job pipeline without silently
        # importing workflow-target training into job error measurements.
        write("plans", PLAN_FIELDS, [])
        write("scheduler_pools", ["sched_pool_id", "sched_capacity_slots"],
              [{"sched_pool_id": "builders", "sched_capacity_slots": 1}])

    def tick(at):
        with patch("vedrfolnir.service.utcnow", return_value=at):
            result = service.tick()
        for key, count in result["job_observations"].items():
            counters[key] += count
        return result

    def job(run, kind, status, *, start=None, finish=None, queued=None):
        return {"run_id": run, "plan_id": None, "job_id": kind, "status": status,
                "started_at": start, "finished_at": finish, "queued_at": queued,
                "vm_name": "worker-1" if start else "unknown", "sched_pool_id": "builders"}

    try:
        for day in range(days):
            at = (now - timedelta(days=days-day)).replace(hour=12, minute=0, second=0, microsecond=0)
            start = at - timedelta(minutes=4)
            dispatch = at + timedelta(minutes=6)
            finish = dispatch + timedelta(minutes=rng.randint(8, 12))
            competitor = job(f"compile-{day}", "compile", "running", start=start)
            queued = job(f"package-{day}", "package", "queued", queued=at)
            snapshot([competitor, queued])
            tick(at)
            assert tick(at)["job_observations"]["recorded"] == 0
            # Recreate service and database connections, preserving first ETAs.
            engine.dispose()
            service = EngineService(factory, config)
            assert tick(at)["job_observations"]["recorded"] == 0
            if day == 0:
                path = source / "live_jobs.csv"
                path.rename(source / "live_jobs.unavailable")
                try:
                    with patch("vedrfolnir.service.utcnow", return_value=at):
                        service.tick()
                except FileNotFoundError:
                    failures += 1
                    assert service.last_error is not None
                else:
                    raise AssertionError("Missing source was not detected")
                finally:
                    (source / "live_jobs.unavailable").rename(path)
                tick(at)
                assert service.last_error is None
            competitor = job(f"compile-{day}", "compile", "passed", start=start, finish=dispatch)
            history.append(competitor)
            running = job(f"package-{day}", "package", "running", start=dispatch, queued=at)
            snapshot([competitor, running])
            tick(dispatch)
            completed = job(f"package-{day}", "package", "passed", start=dispatch, finish=finish, queued=at)
            history.append(completed)
            snapshot([competitor, completed])
            tick(finish)
        assert counters["recorded"] == counters["matched"] == 3 * days
        # SQLite online backup includes WAL state and the new monitoring table.
        with sqlite3.connect(database) as origin, sqlite3.connect(output / "backup.sqlite") as backup:
            origin.backup(backup)
            assert backup.execute("select count(*) from job_predictions").fetchone()[0] == 3 * days
        restored_engine = make_engine(f"sqlite:///{output / 'backup.sqlite'}")
        try:
            restored_factory = create_session_factory(restored_engine)
            restored_service = EngineService(restored_factory, config)
            with patch("vedrfolnir.service.utcnow", return_value=finish):
                restored = restored_service.tick()
            assert restored["job_observations"] == {"recorded": 0, "matched": 0, "pruned": 0}
            with restored_factory() as session:
                assert session.scalar(select(func.count(JobPrediction.id))) == 3 * days
        finally:
            restored_engine.dispose()
        with factory() as session:
            metrics = summarize_jobs(session, config.job_retention_days)
        active_start = now - timedelta(minutes=2)
        snapshot([job("live-compile", "compile", "running", start=active_start),
                  job("live-package", "package", "queued", queued=now-timedelta(minutes=1))])
        tick(now)
        with factory() as session:
            assert session.scalar(select(func.count(JobPrediction.id))) == 3 * days + 2
        summary = {"fixture": "synthetic FIFO, one declared slot, standalone jobs",
                   "accuracy_evidence": False, "days": days, "seed": seed,
                   "completed_observations": 3 * days, "source_failures_recovered": failures,
                   "restart_duplicate_observations": 0, "backup_observations": 3 * days,
                   "restore_duplicate_observations": 0,
                   "metrics_before_live_fixture": metrics}
        (output / "summary.json").write_text(json.dumps(summary, indent=2, default=str)+"\n")
        return summary
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--days", type=int, default=8)
    args = parser.parse_args()
    print(json.dumps(run_smoke(args.output, args.days), indent=2, default=str))


if __name__ == "__main__":
    main()
