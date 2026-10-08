"""Release smoke: run the actual Docker image in demo and CSV source modes.

Uses only Python stdlib and Docker CLI. Each run gets an isolated temporary
source and SQLite volume; restarts verify persisted state. No public CI data.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
PORT = 18761
BASE = f"http://127.0.0.1:{PORT}"
ADMIN_TOKEN = "release-smoke-local-only-not-a-secret"


def command(*args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL)


def request(path, *, token=None, method="GET"):
    headers = {"Authorization": "Bearer " + token} if token else {}
    with urlopen(Request(BASE + path, headers=headers, method=method), timeout=4) as reply:
        assert reply.status == 200
        if path == "/":
            return reply.read().decode("utf-8")
        return json.loads(reply.read())


def write_csv(path, columns, rows):
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def make_csv_fixture(directory):
    now = datetime.now(timezone.utc).replace(microsecond=0)
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


def await_snapshot(mode):
    last = "not started"
    for _ in range(90):
        try:
            status = request("/api/status")
            if (status["source_mode"] == mode
                    and status["history_count"] >= (6 if mode == "files" else 1000)
                    and status["data_source_sync"] == "recent"
                    and status["prediction_count"] > 0):
                return status
            last = repr(status)
        except (ConnectionError, TimeoutError, HTTPError, URLError, OSError) as error:
            last = f"{type(error).__name__}: {error}"
        time.sleep(2)
    raise AssertionError(f"{mode} did not become healthy: {last}")


def validate_surface(mode):
    # Wait for the first completed poll before checking the API.
    status = await_snapshot(mode)
    assert request("/health") == {"status": "up"}
    api = request("/openapi.json")
    from vedrfolnir import __version__
    assert api["info"]["version"] == __version__
    assert "Vedrfolnir" in request("/")
    plans = request("/api/plans")
    assert plans and any(row.get("prediction") for row in plans)
    assert isinstance(request("/api/metrics"), dict)
    assert isinstance(request("/api/metrics/by-workload"), dict)
    jobs = request("/api/jobs")
    assert jobs["data_source_sync"] == "recent" and jobs["jobs"]
    observations = request("/api/job-forecasts?limit=1000")
    assert observations
    metrics = request("/api/job-metrics")
    assert metrics["stored_observations"] > 0
    assert metrics["job_p90_coverage"] is None
    for dimension in ("pool", "job_type", "load"):
        assert request("/api/job-metrics/by-workload?group_by=" + dimension)["group_by"] == dimension
    assert len(request("/api/job-metrics/daily?days=7")["rows"]) == 7
    pools = request("/api/pools")["pools"]
    if mode == "files":
        assert status["history_count"] == 6
        assert any(p["sched_pool_id"] == "builders"
                   and p["capacity_source"] == "declared"
                   and p["declared_capacity_slots"] == 2 for p in pools)
        assert any(p["plan_id"] == "active-1" and
                   p["prediction"]["predicted_remaining_minutes"] > 0
                   for p in plans)
        queued = next(j for j in jobs["jobs"] if j["run_id"] == "live-queued")
        assert queued["prediction"]["forecast_available"]
        assert {r["run_id"] for r in observations} == {"live-running", "live-queued"}
    try:
        request("/api/admin/sync", method="POST")
    except HTTPError as error:
        assert error.code == 401
    else:
        raise AssertionError("Unauthenticated sync was accepted")
    result = request("/api/admin/sync", token=ADMIN_TOKEN, method="POST")
    assert result["status"] in ("ok", "already_running")
    return status, [(r["id"], r["run_id"], r["observed_status"], r["predicted_at"])
                    for r in observations]


def smoke_mode(image, mode, fixture_dir, volume, run_id):
    name = f"vedrfolnir-preview-{run_id}-{mode}"
    args = [
        "docker", "run", "-d", "--name", name,
        "--publish", f"127.0.0.1:{PORT}:8080",
        "--volume", f"{volume}:/data",
        "--env", "SOURCE_MODE=" + mode,
        "--env", "ETA_DB_URL=sqlite:////data/eta.sqlite3",
        "--env", "ETA_ADMIN_TOKEN=" + ADMIN_TOKEN,
        "--env", "ETA_POLL_SECONDS=2",
        "--env", "ETA_PREDICTION_INTERVAL_SECONDS=2",
        "--env", "ETA_POOL_CAPACITY_REFRESH_SECONDS=600",
    ]
    if mode == "files":
        args.extend(["--volume", f"{fixture_dir}:/srv/examples/data:ro"])
    args.append(image)
    command(*args)
    try:
        first, first_jobs = validate_surface(mode)
        command("docker", "stop", "--time", "15", name)
        stopped = json.loads(subprocess.check_output(
            ["docker", "inspect", "--format", "{{json .State}}", name], text=True))
        # Uvicorn re-raises SIGTERM after its lifespan cleanup: Docker reports
        # 143 for that normal exit. A forced SIGKILL (137) must fail this gate.
        assert stopped["ExitCode"] in (0, 143) and not stopped["OOMKilled"], stopped
        shutdown_log = subprocess.check_output(["docker", "logs", name], stderr=subprocess.STDOUT, text=True)
        assert "Application shutdown complete" in shutdown_log, shutdown_log
        command("docker", "start", name)
        second, second_jobs = validate_surface(mode)
        assert second["history_count"] == first["history_count"]
        assert second["prediction_count"] >= first["prediction_count"]
        assert second_jobs == first_jobs
        print(json.dumps({
            "mode": mode, "first_history_count": first["history_count"],
            "after_restart_history_count": second["history_count"],
            "prediction_count": second["prediction_count"],
            "persisted_job_observations": len(second_jobs),
            "result": "passed",
        }), flush=True)
    except Exception:
        subprocess.run(["docker", "logs", "--tail", "120", name], check=False)
        raise
    finally:
        subprocess.run(["docker", "rm", "-f", name],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="vedrfolnir:preview-smoke")
    options = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="vedrfolnir-preview-") as folder:
        source_dir = Path(folder)
        os.chmod(source_dir, 0o755)  # Container runs as UID 10001.
        make_csv_fixture(source_dir)
        # Verify sample data contract without opening/mutating the app SQLite DB.
        from vedrfolnir.source import CSVSource
        from vedrfolnir.source_validation import validate_source
        assert validate_source(CSVSource(source_dir))["ok"]
        run_id = uuid.uuid4().hex[:12]
        for mode in ("demo", "files"):
            volume = f"vedrfolnir-smoke-{run_id}-{mode}"
            command("docker", "volume", "create", volume)
            try:
                smoke_mode(options.image, mode, source_dir, volume, run_id)
            finally:
                subprocess.run(["docker", "volume", "rm", volume], check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
