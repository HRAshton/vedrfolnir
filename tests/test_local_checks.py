"""Local validation remains available alongside hosted CI."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_local_check_script():
    script = ROOT / "scripts" / "local_check.sh"
    assert script.is_file()
    source = script.read_text(encoding="utf-8")
    assert "python3.15" in source
    assert "-m pytest -q" in source
    assert "workflow_dispatch" not in source
    if shutil.which("bash"):
        subprocess.run(["bash", "-n", str(script)], check=True)


def test_readme_points_to_development_guide():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "(docs/development.md)" in readme


def test_docker_mode_invokes_external_engine(tmp_path):
    import os

    log = tmp_path / "commands.log"
    for name in ("docker", "python3.15"):
        executable = tmp_path / name
        executable.write_text(
            '#!/bin/sh\nprintf "%s %s\\n" "$(basename "$0")" "$*" >> "$CHECK_LOG"\n'
        )
        executable.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}", CHECK_LOG=str(log))
    env.pop("VIRTUAL_ENV", None)
    subprocess.run(["bash", str(ROOT / "scripts/local_check.sh"), "docker"],
                   env=env, check=True, timeout=5)
    commands = log.read_text()
    assert "docker info" in commands
    assert "docker build --tag vedrfolnir:local-smoke ." in commands
    assert "scripts/release_smoke.py --image vedrfolnir:local-smoke" in commands
