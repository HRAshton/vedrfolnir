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


def verified_release_protections():
    branch = [{"type": rule} for rule in
              ["pull_request", "required_status_checks", "deletion", "non_fast_forward"]]
    branch[1]["parameters"] = {"required_status_checks": [{"context": name} for name in [
        "Python and package validation", "Shell and workflow validation",
        "Dependency review", "Python vulnerability audit", "Container qualification",
    ]]}
    tags = [{
        "target": "tag", "enforcement": "active", "bypass_actors": [],
        "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
        "rules": [{"type": "update"}, {"type": "deletion"}],
    }]
    environment = {"protection_rules": [{"type": "required_reviewers", "reviewers": [{"id": 1}]}]}
    return branch, tags, environment


@pytest.mark.parametrize("redacted", [
    "missing", None, False, "", {}, 0,
])
def test_tag_ruleset_bypass_must_be_explicitly_verified(redacted):
    branch, tags, environment = verified_release_protections()
    if redacted == "missing":
        del tags[0]["bypass_actors"]
    else:
        tags[0]["bypass_actors"] = redacted
    with pytest.raises(ValueError, match="Cannot verify tag ruleset bypass_actors"):
        validate_protections(branch, tags, environment)


def test_explicit_empty_bypass_list_allows_verified_tag_ruleset():
    validate_protections(*verified_release_protections())


def test_nonempty_bypass_list_cannot_authorize_release():
    branch, tags, environment = verified_release_protections()
    tags[0]["bypass_actors"] = [{"actor_id": 1, "actor_type": "User"}]
    with pytest.raises(ValueError, match="without bypasses"):
        validate_protections(branch, tags, environment)


def test_second_matching_redacted_ruleset_cannot_be_ignored():
    from copy import deepcopy

    branch, tags, environment = verified_release_protections()
    unknown = deepcopy(tags[0])
    del unknown["bypass_actors"]
    tags.append(unknown)
    with pytest.raises(ValueError, match="Cannot verify tag ruleset bypass_actors"):
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
