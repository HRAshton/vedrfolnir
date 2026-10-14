"""Synthetic release fixtures shared by native/container smoke and tests."""
import csv
from datetime import datetime, timedelta, timezone


def write_csv(path, columns, rows):
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def make_csv_fixture(directory, now=None):
    """Small demo/CSV lifecycle input; pass an aware clock for deterministic tests."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Fixture clock must be timezone-aware")
    now = now.astimezone(timezone.utc).replace(microsecond=0)
    stamp = lambda x: (now + timedelta(minutes=x)).isoformat()
    history = [
        {"run_id": f"h-{i}", "plan_id": f"finished-{i}", "job_id": "compile",
         "status": "passed", "started_at": stamp(-100 - i * 10),
         "finished_at": stamp(-90 - i * 10), "sched_pool_id": "builders",
         "vm_name": f"vm-{i % 2}"}
        for i in range(6)
    ]
    write_csv(
        directory / "historical_jobs.csv",
        ["run_id", "plan_id", "job_id", "status", "started_at",
         "finished_at", "sched_pool_id", "vm_name"],
        history,
    )
    write_csv(
        directory / "plans.csv",
        ["plan_id", "status", "started_at", "finished_at"],
        [{"plan_id": "active-1", "status": "running",
          "started_at": stamp(-8), "finished_at": ""}],
    )
    write_csv(
        directory / "live_jobs.csv",
        ["run_id", "plan_id", "job_id", "status", "started_at",
         "finished_at", "queued_at", "vm_name", "sched_pool_id", "priority"],
        [
            {"run_id": "live-running", "plan_id": "active-1",
             "job_id": "compile", "status": "running",
             "started_at": stamp(-4), "queued_at": stamp(-6),
             "vm_name": "vm-0", "sched_pool_id": "builders", "priority": 1},
            {"run_id": "live-queued", "plan_id": "active-1",
             "job_id": "compile", "status": "queued",
             "queued_at": stamp(-2), "vm_name": "unknown",
             "sched_pool_id": "builders", "priority": 1},
        ],
    )
    write_csv(
        directory / "scheduler_pools.csv",
        ["sched_pool_id", "sched_capacity_slots"],
        [{"sched_pool_id": "builders", "sched_capacity_slots": 2}],
    )


