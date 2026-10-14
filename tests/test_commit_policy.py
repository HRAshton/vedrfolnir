"""Commit policy accepts GitHub squash subjects and rejects untrusted inputs."""
import pytest
from scripts.check_commit_policy import check, revision_range, valid_subject


@pytest.mark.parametrize("subject", [
    "ci: add Python and package validation", "fix(api)!: reject stale requests",
    "build: define release metadata and support contract (#1)",
])
def test_conventional_subjects(subject):
    assert valid_subject(subject)


@pytest.mark.parametrize("subject", ["fix", "Add CI", "feat: ", "ci: valid\nsecond line", "fix(): empty scope"])
def test_invalid_subjects(subject):
    assert not valid_subject(subject)


def test_revisions_reject_option_injection():
    with pytest.raises(ValueError, match="full commit SHAs"):
        revision_range("push", {"before": "0" * 40, "after": "--all"})


def test_initial_push_checks_full_history():
    sha = "a" * 40
    assert revision_range("push", {"before": "0" * 40, "after": sha}) == sha


def test_merge_commit_is_allowed_but_bad_feature_commit_fails(tmp_path, monkeypatch):
    import subprocess

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-b", "master")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    git("commit", "--allow-empty", "-m", "chore: initial")
    base = git("rev-parse", "HEAD")
    git("switch", "-c", "feature")
    git("commit", "--allow-empty", "-m", "bad feature subject")
    git("switch", "master")
    git("commit", "--allow-empty", "-m", "docs: update")
    git("merge", "--no-ff", "feature", "-m", "Merge pull request #1 from test/feature")
    head = git("rev-parse", "HEAD")
    monkeypatch.chdir(tmp_path)
    assert check("push", {"before": base, "after": head}) == [
        "Invalid commit subject: bad feature subject",
    ]


@pytest.mark.parametrize(("title", "commits", "expected"), [
    ("fix: valid PR title", "fix: valid commit", []),
    ("Invalid PR title", "fix: valid commit",
     ["PR title must be a Conventional Commit subject"]),
    ("fix: valid PR title", "invalid commit",
     ["Invalid commit subject: invalid commit"]),
    ("Invalid PR title", "invalid commit",
     ["PR title must be a Conventional Commit subject",
      "Invalid commit subject: invalid commit"]),
])
def test_edited_pr_validates_title_and_commits(monkeypatch, title, commits, expected):
    import subprocess

    before, after = "a" * 40, "b" * 40
    inspected = []

    def fake_git(argv, **kwargs):
        inspected.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=commits + "\n")

    monkeypatch.setattr("scripts.check_commit_policy.subprocess.run", fake_git)
    event = {
        "action": "edited",
        "pull_request": {
            "title": title, "base": {"sha": before}, "head": {"sha": after},
        },
    }
    assert check("pull_request", event) == expected
    assert inspected == [["git", "log", "--no-merges", "--format=%s", f"{before}..{after}"]]


def test_pr_policy_is_required_and_metadata_does_not_start_full_ci():
    """Title edits use a short, separately required check, not full CI."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    fast = (root / ".github/workflows/pr-policy.yml").read_text()
    full = (root / ".github/workflows/ci.yml").read_text()
    config = json.loads((root / ".github/rulesets/master.json").read_text())
    required = next(rule for rule in config["rules"] if rule["type"] == "required_status_checks")
    contexts = {item["context"] for item in required["parameters"]["required_status_checks"]}
    assert "PR title and commit policy" in contexts
    assert "types: [opened, synchronize, reopened, edited]" in fast
    assert "types: [opened, synchronize, reopened]" in full
    assert "edited" not in full
    assert "name: PR title and commit policy" in fast
    assert "name: PR title and commit policy" not in full
    assert "run: python scripts/check_commit_policy.py" in fast
