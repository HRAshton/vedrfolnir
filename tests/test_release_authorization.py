"""Release authorization rejects changed versions and incomplete protection."""
import pytest
from scripts.validate_release import validate_protections, validate_tag


@pytest.mark.parametrize("tag,version", [("v0.3.0rc1", "0.3.0rc1"), ("v1.2.3", "1.2.3")])
def test_approved_tag_identity(tag, version):
    validate_tag(tag, version)


@pytest.mark.parametrize("tag,version", [("v0.3.0", "0.3.0rc1"), ("v01.2.3", "01.2.3"),
                                         ("v1.2.3rc0", "1.2.3rc0"), ("v1.2.3-dev", "1.2.3-dev")])
def test_invalid_tag_identity(tag, version):
    with pytest.raises(ValueError):
        validate_tag(tag, version)


def test_protection_requires_actual_enforcement_and_reviewers():
    branch = [{"type": t} for t in ["pull_request", "required_status_checks", "deletion", "non_fast_forward"]]
    next(rule for rule in branch if rule["type"] == "required_status_checks")["parameters"] = {
        "required_status_checks": [{"context": name} for name in [
            "Python and package validation", "Shell and workflow validation", "Dependency review",
            "Python vulnerability audit", "Container qualification"]]}
    tags = [{"target": "tag", "enforcement": "active", "bypass_actors": [],
             "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
             "rules": [{"type": "update"}, {"type": "deletion"}]}]
    environment = {"protection_rules": [{"type": "required_reviewers", "reviewers": [{"id": 1}]}]}
    validate_protections(branch, tags, environment)
    for values in [(branch, [], environment), ([], tags, environment), (branch, tags, {})]:
        with pytest.raises(ValueError):
            validate_protections(*values)
    tags[0]["bypass_actors"] = [{"actor_id": 1}]
    with pytest.raises(ValueError):
        validate_protections(branch, tags, environment)


def test_annotated_and_lightweight_event_shas_resolve_same_commit(tmp_path, monkeypatch):
    import subprocess
    from scripts.validate_release import resolve_event_commit

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-b", "master")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    git("commit", "--allow-empty", "-m", "chore: initial")
    sha = git("rev-parse", "HEAD")
    git("tag", "-a", "v0.3.0rc1", "-m", "candidate")
    tag_object = git("rev-parse", "v0.3.0rc1")
    monkeypatch.chdir(tmp_path)
    assert tag_object != sha
    assert resolve_event_commit(tag_object) == resolve_event_commit(sha) == sha
    with pytest.raises(ValueError):
        resolve_event_commit("--all")
