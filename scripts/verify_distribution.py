"""Verify an installed artifact outside the checkout, including real HTTP serving."""
from importlib.metadata import entry_points, version
from importlib.resources import files
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen


def verify(expected):
    assert sys.version_info[:2] == (3, 15), sys.version
    from vedrfolnir import __version__
    from vedrfolnir.api import app

    assert version("vedrfolnir") == __version__ == app.version == expected
    assert (files("vedrfolnir") / "static" / "dashboard.html").is_file()
    scripts = {ep.name: ep for ep in entry_points(group="console_scripts")}
    required = {
        "vedrfolnir", "vedrfolnir-benchmark", "vedrfolnir-travistorrent",
        "vedrfolnir-wfcommons", "vedrfolnir-check-source",
    }
    assert required <= scripts.keys()
    for name in required:
        assert callable(scripts[name].load()), name
    executable = str(Path(sys.executable).with_name("vedrfolnir"))
    with tempfile.TemporaryDirectory(prefix="installed-smoke-") as folder:
        env = dict(os.environ, SOURCE_MODE="demo", ETA_ADMIN_TOKEN=secrets.token_urlsafe(32),
                   ETA_DB_URL=f"sqlite:///{Path(folder) / 'state.sqlite3'}", LOG_CONFIG_FILE="")
        env.pop("PYTHONPATH", None)
        subprocess.run([executable, "doctor"], env=env, cwd=folder, check=True)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        with (Path(folder) / "server.log").open("w+") as log:
            process = subprocess.Popen(
                [executable, "serve", "--host", "127.0.0.1", "--port", str(port)],
                env=env, cwd=folder, stdout=log, stderr=log,
            )
            try:
                deadline = time.monotonic() + 30
                while True:
                    if process.poll() is not None:
                        log.seek(0)
                        raise AssertionError(log.read())
                    try:
                        with urlopen(f"http://127.0.0.1:{port}/openapi.json", timeout=2) as response:
                            assert json.load(response)["info"]["version"] == expected
                        break
                    except (URLError, TimeoutError):
                        if time.monotonic() >= deadline:
                            log.seek(0)
                            raise AssertionError("API startup timeout: " + log.read())
                        time.sleep(.1)
                for path in ("/health", "/api/status", "/"):
                    with urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
                        assert response.status == 200
                        if path == "/":
                            assert b"Vedrfolnir" in response.read()
            finally:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    raise AssertionError("Service did not shut down gracefully")
            assert process.returncode in (0, -15), process.returncode
    print("Installed artifact verified:", expected)


if __name__ == "__main__":
    verify(sys.argv[1])
