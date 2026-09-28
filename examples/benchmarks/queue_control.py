"""Generate a SYNTHETIC FIFO two-slot control; never real-world evidence.

All jobs are admitted with their plan. Starts obey a shared finite-capacity
queue, so replay can discover capacity from running/prior completed jobs.
This complements the existing staggered-admission fixture without changing it.
"""
import argparse
import csv
from datetime import datetime, timedelta, timezone
import heapq
from pathlib import Path
import random


def generate(destination, count=80, seed=77125):
    if count < 1:
        raise ValueError("count must be positive")
    folder = Path(destination)
    folder.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    slots = [(base, "worker-1"), (base, "worker-2")]
    plans, jobs = [], []
    for i in range(count):
        name = f"queue-control-{i:04d}"
        admission = base + timedelta(minutes=3 * i)
        ends = []
        for k, kind in enumerate(("compile", "test", "package")):
            free_at, vm = heapq.heappop(slots)
            start = max(admission, free_at)
            end = start + timedelta(minutes=rng.randint(4, 12))
            heapq.heappush(slots, (end, vm))
            ends.append(end)
            jobs.append({
                "run_id": f"{name}-{k}", "plan_id": name, "job_id": kind,
                "queued_at": admission.isoformat(), "started_at": start.isoformat(),
                "finished_at": end.isoformat(), "status": "passed", "vm_name": vm,
                "sched_pool_id": "builders", "priority": 0,
            })
        plans.append({"plan_id": name, "started_at": admission.isoformat(),
                      "finished_at": max(ends).isoformat(), "status": "passed", "priority": 0})
    for filename, rows in (("plans.csv", plans), ("jobs.csv", jobs)):
        with (folder / filename).open("w", newline="", encoding="utf-8") as out:
            writer = csv.DictWriter(out, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--count", type=int, default=80)
    parser.add_argument("--seed", type=int, default=77125)
    args = parser.parse_args()
    generate(args.output, args.count, args.seed)
