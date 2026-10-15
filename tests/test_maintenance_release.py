"""Automatic releases fail closed for unexpected changes or consumed tags."""
import pytest
from scripts.plan_maintenance_release import assess_changes, next_candidate


def test_next_rc_is_strict_and_never_reused():
    assert next_candidate("v0.3.0rc1", {"v0.3.0rc1"}) == "v0.3.0rc2"
    for tag in ["v0.3.0", "v0.3.0rc0", "v01.2.3rc1", "evil", "v0.3.0rc1/extra"]:
        with pytest.raises(ValueError):
            next_candidate(tag, set())
    with pytest.raises(ValueError, match="already exists"):
        next_candidate("v0.3.0rc1", {"v0.3.0rc2"})


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
