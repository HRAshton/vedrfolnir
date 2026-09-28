"""Deterministic small SYNTHETIC multi-job plan fixture. Not a real benchmark."""
import csv
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path


def generate(destination, count=110):
    rng = random.Random(77124)
    folder = Path(destination)
    folder.mkdir(parents=True, exist_ok=True)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    plans, jobs = [], []
    types = {"checkout": 4, "compile": 10, "package": 22, "deploy": 15, "analyze": 7}
    for i in range(count):
        plan_id = f"fixture-{i:04d}"
        submit = base + timedelta(minutes=i * 35)
        ends = []
        version = "2.1" if i < 55 else "2.2"
        priority = int(i % 9 == 0)
        for k in range(rng.randint(5, 11)):
            job = rng.choice(list(types))
            db = rng.choice(["postgres", "oracle"])
            queued = submit + timedelta(seconds=rng.randint(0, 5) * 60)
            # True queue wait and completion times are part of the fixture.
            wait = rng.randint(1, 10) * 60 + ((i // 8) % 3) * 240
            start = queued + timedelta(seconds=wait)
            baseline = types[job] * (1.25 if db == "oracle" else 1.0)
            if job == "package" and version == "2.2":
                baseline *= 1.5
            duration = max(60, rng.lognormvariate(0, .3) * baseline * 60)
            end = start + timedelta(seconds=duration)
            ends.append(end)
            jobs.append({
                "run_id": f"{plan_id}-job-{k:02d}", "plan_id": plan_id,
                "job_id": job, "queued_at": queued.isoformat(),
                "started_at": start.isoformat(), "finished_at": end.isoformat(),
                "status": "failed" if rng.random() < .05 else "passed",
                "version": version, "db_type": db,
                "vm_name": f"vm-{rng.randint(1, 10):02d}", "priority": priority,
            })
        plans.append({"plan_id": plan_id, "started_at": submit.isoformat(),
                      "finished_at": max(ends).isoformat(), "priority": priority})
    for name, rows in (("plans.csv", plans), ("jobs.csv", jobs)):
        with (folder / name).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    generate(Path(__file__).parent)
