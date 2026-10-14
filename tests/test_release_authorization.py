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



def verified_release_protections():
    branch = [{"type": kind} for kind in
              ["pull_request", "required_status_checks", "deletion", "non_fast_forward"]]
    branch[1]["parameters"] = {"required_status_checks": [{"context": name} for name in [
        "Python and package validation", "Shell and workflow validation",
        "Dependency review", "Python vulnerability audit", "Container qualification",
    ]]}
    tags = [{
        "id": 42, "target": "tag", "enforcement": "active", "bypass_actors": [],
        "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
        "rules": [{"type": "update"}, {"type": "deletion"}],
    }]
    environment = {"protection_rules": [{"type": "required_reviewers", "reviewers": [{"id": 1}]}]}
    return branch, tags, environment


def load_auditor():
    """Test the exact inline verifier without running a secret-bearing job."""
    import textwrap
    from pathlib import Path
    workflow = (Path(__file__).resolve().parents[1] /
                ".github/workflows/release.yml").read_text(encoding="utf-8")
    marker = "          python - <<'RULESET_AUDIT'\n"
    end = "\n          RULESET_AUDIT\n"
    assert workflow.count(marker) == 1
    source = textwrap.dedent(workflow.split(marker, 1)[1].split(end, 1)[0])
    namespace = {"__name__": "_auditor_under_test"}
    exec(compile(source, "release.yml:verify-tag-rules", "exec"), namespace)
    return namespace["verify_rulesets"]


def test_read_only_authorization_checks_structure_with_redacted_bypass():
    branch, tags, environment = verified_release_protections()
    del tags[0]["bypass_actors"]
    validate_protections(branch, tags, environment)
    for case in [(branch, [], environment), ([], tags, environment), (branch, tags, {})]:
        with pytest.raises(ValueError):
            validate_protections(*case)


def test_privileged_auditor_accepts_verified_empty_bypasses():
    _, tags, _ = verified_release_protections()
    assert load_auditor()(tags, lambda identifier: tags[0]) == 1


@pytest.mark.parametrize("redacted", [
    "missing", None, False, "", {}, 0, [{"actor_id": 1}],
])
def test_privileged_auditor_rejects_unknown_or_nonempty_bypasses(redacted):
    from copy import deepcopy
    _, tags, _ = verified_release_protections()
    detail = deepcopy(tags[0])
    if redacted == "missing":
        del detail["bypass_actors"]
    else:
        detail["bypass_actors"] = redacted
    with pytest.raises(ValueError, match="bypass"):
        load_auditor()(tags, lambda identifier: detail)


def test_privileged_auditor_rejects_second_redacted_ruleset():
    from copy import deepcopy
    _, tags, _ = verified_release_protections()
    hidden = deepcopy(tags[0])
    hidden["id"] = 43
    del hidden["bypass_actors"]
    tags.append(hidden)
    with pytest.raises(ValueError, match="bypass"):
        load_auditor()(tags, lambda identifier: tags[identifier - 42])


def test_privileged_auditor_rejects_incomplete_rule_or_listing():
    from copy import deepcopy
    _, tags, _ = verified_release_protections()
    detail = deepcopy(tags[0])
    detail["rules"] = [{"type": "deletion"}]
    with pytest.raises(ValueError, match="No verified"):
        load_auditor()(tags, lambda identifier: detail)
    with pytest.raises(ValueError, match="pagination"):
        load_auditor()(tags * 100, lambda identifier: tags[0])


def test_sensitive_job_is_environment_gated_and_blocks_qualification():
    from pathlib import Path
    workflow = (Path(__file__).resolve().parents[1] /
                ".github/workflows/release.yml").read_text(encoding="utf-8")
    job = workflow.split("  verify-tag-rules:\n", 1)[1].split("  qualify:\n", 1)[0]
    assert "    needs: authorize\n" in job
    assert "    environment: release-ruleset-audit\n" in job
    assert "    permissions: {}\n" in job
    assert "actions/checkout" not in job and "uses:" not in job
    assert "secrets.RELEASE_RULESET_AUDIT_TOKEN" in job
    assert workflow.count("secrets.RELEASE_RULESET_AUDIT_TOKEN") == 1
    assert "  qualify:\n    needs: [authorize, verify-tag-rules]\n" in workflow

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
