"""Automatic releases fail closed for unexpected changes or consumed tags."""
import subprocess

import pytest
from scripts.plan_maintenance_release import (
    assess_changes, latest_release_tag, next_candidate, plan,
)


def test_next_rc_is_strict_and_never_reused():
    assert next_candidate("v0.3.0rc1", {"v0.3.0rc1"}) == "v0.3.0rc2"
    for tag in ["v0.3.0", "v0.3.0rc0", "v01.2.3rc1", "evil", "v0.3.0rc1/extra"]:
        with pytest.raises(ValueError):
            next_candidate(tag, set())
    with pytest.raises(ValueError, match="already exists"):
        next_candidate("v0.3.0rc1", {"v0.3.0rc2"})


def test_release_baselines_sort_numerically_and_prefer_final():
    assert latest_release_tag(["v0.3.0rc9", "v0.3.0rc10", "v0.2.0rc50"]) == "v0.3.0rc10"
    assert latest_release_tag({"v0.3.0", "v0.3.0rc12"}) == "v0.3.0"
    assert latest_release_tag(["v1.0.0rc1", "v0.99.0rc40"]) == "v1.0.0rc1"
    assert latest_release_tag(["v0.3.0rc0", "unexpected"]) is None


def test_auto_release_requires_only_runtime_lock_change():
    assert assess_changes(["requirements/container-constraints.txt"])[0]
    for paths in [
        [],
        ["src/vedrfolnir/adaptive.py"],
        ["pyproject.toml"],
        ["Dockerfile"],
        [".github/workflows/release.yml"],
        ["requirements/container-constraints.txt", "renovate.json"],
    ]:
        assert not assess_changes(paths)[0]


def test_planner_selects_latest_reachable_rc_and_never_reuses_a_tag(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def git(*args):
        return subprocess.check_output(["git", *args], text=True, cwd=tmp_path).strip()

    def run(*args):
        subprocess.run(["git", *args], check=True, stdout=subprocess.DEVNULL, cwd=tmp_path)

    run("init", "-b", "master")
    run("config", "user.name", "Test")
    run("config", "user.email", "test@example.invalid")
    lock = tmp_path / "requirements" / "container-constraints.txt"
    lock.parent.mkdir()
    lock.write_text("river==0.26.1\\n", encoding="utf-8")
    run("add", ".")
    run("commit", "-m", "base")
    assert not plan()["eligible"]
    run("tag", "v0.3.0rc1")

    lock.write_text("river==0.26.2\\n", encoding="utf-8")
    run("add", ".")
    run("commit", "-m", "lock update")
    result = plan()
    assert result["eligible"] and result["last_tag"] == "v0.3.0rc1"
    assert result["candidate_tag"] == "v0.3.0rc2"
    with pytest.raises(ValueError, match="latest release"):
        plan("v0.2.0rc1")

    run("tag", "v0.3.0rc2")
    assert plan()["last_tag"] == "v0.3.0rc2"
    assert not plan()["eligible"] and plan()["candidate_tag"] is None
    lock.write_text("river==0.26.3\\n", encoding="utf-8")
    run("add", ".")
    run("commit", "-m", "another lock update")
    result = plan()
    assert result["eligible"] and result["candidate_tag"] == "v0.3.0rc3"
    assert result["changed_paths"] == ["requirements/container-constraints.txt"]

    run("tag", "v0.3.0rc3")
    run("tag", "v0.3.0")
    assert not plan()["eligible"]
    assert "final" in plan()["reason"]


def test_newer_unreachable_tag_and_unrelated_changes_fail_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def run(*args):
        subprocess.run(["git", *args], check=True, stdout=subprocess.DEVNULL, cwd=tmp_path)

    run("init", "-b", "master")
    run("config", "user.name", "Test")
    run("config", "user.email", "test@example.invalid")
    lock = tmp_path / "requirements" / "container-constraints.txt"
    lock.parent.mkdir()
    lock.write_text("river==0.26.1\\n", encoding="utf-8")
    run("add", ".")
    run("commit", "-m", "baseline")
    run("tag", "v0.3.0rc1")
    (tmp_path / "README.md").write_text("unrelated", encoding="utf-8")
    run("add", ".")
    run("commit", "-m", "unrelated code")
    result = plan()
    assert not result["eligible"] and "README.md" in result["changed_paths"]

    run("switch", "-c", "another-release")
    run("tag", "v0.3.0rc2")
    run("switch", "master")
    assert not plan()["eligible"]
    assert "not reachable" in plan()["reason"]
