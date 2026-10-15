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


def test_missing_edge_bootstraps_but_registry_error_fails_closed(monkeypatch):
    from types import SimpleNamespace
    from scripts import maintenance_changes as maintenance

    def missing(*args, **kwargs):
        assert args[0] == ["docker", "manifest", "inspect", maintenance.EDGE]
        return SimpleNamespace(returncode=1, stderr="manifest unknown", stdout="")

    monkeypatch.setattr(maintenance.subprocess, "run", missing)
    assert maintenance.prior_edge() == (None, "absent")

    def denied(*args, **kwargs):
        return SimpleNamespace(returncode=1, stderr="unauthorized: denied", stdout="")

    monkeypatch.setattr(maintenance.subprocess, "run", denied)
    with pytest.raises(RuntimeError, match="GHCR lookup failed"):
        maintenance.prior_edge()


def test_previous_successful_edge_identity_is_taken_from_image_label(monkeypatch):
    import json
    from types import SimpleNamespace
    from scripts import maintenance_changes as maintenance

    old = "a" * 64
    monkeypatch.setattr(
        maintenance.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr="", stdout="")
    )
    monkeypatch.setattr(
        maintenance.subprocess, "check_output",
        lambda *args, **kwargs: json.dumps([{
            "Config": {"Labels": {maintenance.LABEL: old}},
            "RepoDigests": ["ghcr.io/hrashton/vedrfolnir@sha256:" + "b" * 64]
        }])
    )
    previous, ref = maintenance.prior_edge()
    assert previous == old
    assert ref.endswith("sha256:" + "b" * 64)
