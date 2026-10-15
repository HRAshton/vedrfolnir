"""Image fingerprint and release-target classification regression checks."""
from pathlib import Path
import subprocess

import pytest

from scripts.maintenance_changes import (
    classify_distribution_paths, decide_build, fingerprint_tree, image_fingerprint,
)


def test_fingerprint_ignores_unshipped_files_and_tracks_shipped_inputs(tmp_path, monkeypatch):
    def run(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, stdout=subprocess.DEVNULL)
    run("init", "-b", "master")
    run("config", "user.name", "Test")
    run("config", "user.email", "test@example.invalid")
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    run("add", ".")
    run("commit", "-m", "test: initial")
    monkeypatch.chdir(tmp_path)
    first = image_fingerprint()
    assert len(first) == 64
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("docs\n")
    run("add", ".")
    run("commit", "-m", "docs: update")
    assert image_fingerprint() == first
    (tmp_path / "Dockerfile").write_text("FROM scratch\n# changed\n")
    run("add", ".")
    run("commit", "-m", "build: update")
    assert image_fingerprint() != first


@pytest.mark.parametrize(("paths", "expected"), [
    ([], "no-distribution"),
    (["docs/operations.md", ".github/workflows/ci.yml", "tests/test_cli.py"], "no-distribution"),
    (["requirements/container-constraints.txt"], "image-only"),
    (["Dockerfile", "examples/data/plans.csv"], "image-only"),
    (["queries/live_jobs.sql"], "image-only"),
    (["src/vedrfolnir/service.py"], "package-and-image"),
    (["README.md"], "package-and-image"),
    (["MANIFEST.in", "Dockerfile"], "package-and-image"),
    (["pyproject.toml"], "package-and-image"),
])
def test_distribution_classification(paths, expected):
    assert classify_distribution_paths(paths) == expected


def test_first_build_match_change_force_and_invalid_fingerprint():
    a = "a" * 64
    b = "b" * 64
    assert decide_build(None, a)[0]
    assert decide_build("legacy", a)[0]
    assert decide_build(b, a)[0]
    assert decide_build(a, a)[0] is False
    assert decide_build(a, a, force=True)[0]
    with pytest.raises(ValueError):
        decide_build(a, "not-sha")


def test_malformed_tree_rejected():
    with pytest.raises(ValueError):
        fingerprint_tree(b"")
    with pytest.raises(ValueError):
        fingerprint_tree(b"not-null-terminated")
