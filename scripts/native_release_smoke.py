"""Isolated native CSV startup/API/restart/shutdown check, not a Docker gate.

Run from the repository root: python -m scripts.native_release_smoke
Uses the current Python interpreter, a fresh temporary SQLite database and
synthetic CSVs. Requires the application and its dependencies installed.
"""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time

from scripts import release_smoke as smoke


def stop(process):
    if process.poll() is None:
        process.terminate()
    deadline = time.monotonic() + 15
    # poll() works even in runtimes without select.poll/pidfd timed waits.
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(.05)
    if process.poll() is None:
        process.kill()
        process.wait()
        raise AssertionError("Native service did not shut down gracefully")


def main():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        smoke.PORT = reservation.getsockname()[1]
    smoke.BASE = f"http://127.0.0.1:{smoke.PORT}"
    smoke.ADMIN_TOKEN = secrets.token_urlsafe(32)
    with tempfile.TemporaryDirectory(prefix="native-release-") as folder:
        root = Path(folder)
        source = root / "source"
        source.mkdir()
        smoke.make_csv_fixture(source)
        env = dict(os.environ, SOURCE_MODE="files", CSV_DATA_DIR=str(source),
                   ETA_DB_URL=f"sqlite:///{root / 'state.sqlite'}", ETA_ADMIN_TOKEN=smoke.ADMIN_TOKEN,
                   ETA_POLL_SECONDS="2", ETA_PREDICTION_INTERVAL_SECONDS="2",
                   LOG_CONFIG_FILE="", LOG_LEVEL="INFO", LOG_FORMAT="json")
        observations = []
        for cycle in range(2):
            log_path = root / f"server-{cycle}.log"
            with log_path.open("w") as log:
                process = subprocess.Popen([
                    sys.executable, "-m", "uvicorn", "vedrfolnir.api:app",
                    "--host", "127.0.0.1", "--port", str(smoke.PORT),
                ], env=env, stdout=log, stderr=log)
                try:
                    status, jobs = smoke.validate_surface("files")
                    observations.append((status, jobs))
                finally:
                    stop(process)
            # Uvicorn re-raises SIGTERM after graceful lifespan cleanup.
            assert process.returncode in (0, -15), log_path.read_text()
            assert "Application shutdown complete" in log_path.read_text()
        assert observations[0][1] == observations[1][1]
        assert observations[0][0]["history_count"] == observations[1][0]["history_count"] == 6
        print(json.dumps({"native_csv_startup_api_restart_shutdown": "passed",
                          "history_count": 6, "preserved_job_observations": len(observations[1][1]),
                          "docker_executed": False}))


if __name__ == "__main__":
    main()
