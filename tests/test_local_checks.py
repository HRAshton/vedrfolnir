"""Local validation tools must not depend on hosted workflows."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_no_github_actions_workflow_files():
    workflows = ROOT / ".github" / "workflows"
    assert not list(workflows.glob("*.yml"))
    assert not list(workflows.glob("*.yaml"))


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
