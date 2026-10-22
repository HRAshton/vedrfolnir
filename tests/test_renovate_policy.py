"""Enforce explicitly allowlisted Renovate auto-merge scopes and safe bot approval."""
import json
from pathlib import Path
import tomllib

from scripts.approve_renovate import APPROVED, safe_pyproject_change


BASE = """[project]
name = "vedrfolnir"
dependencies = ["river>=0.26.1,<0.27"]
[project.optional-dependencies]
dev = ["pytest>=8,<10", "ruff>=0.17.1,<1", "httpx>=0.28,<1"]
"""


def test_allows_only_dev_patch_updates():
    assert safe_pyproject_change(BASE, BASE.replace("ruff>=0.17.1", "ruff>=0.17.2"))
    assert safe_pyproject_change(BASE, BASE.replace("ruff>=0.17.1", "ruff>=0.17.3").replace("httpx>=0.28", "httpx>=0.28.1"))
    for changed in [
        BASE, BASE.replace("river>=0.26.1", "river>=0.26.2"),
        BASE.replace("ruff>=0.17.1", "ruff>=0.18.0"),
        BASE.replace("ruff>=0.17.1", "ruff>=0.17.0"),
        BASE.replace("ruff>=0.17.1", "ruff>=0.17.2") + "\n[tool.fake]\nflag = true\n",
        BASE.replace("pytest>=8", "pytest>=9"),
        BASE.replace("ruff>=0.17.1", "ruff==0.17.2"),
    ]:
        assert not safe_pyproject_change(BASE, changed)


def test_renovate_automerge_rules_match_approved_scopes():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "renovate.json").read_text(encoding="utf-8"))
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    # Fail closed on any future auto-merge scope not explicitly reviewed here.
    assert config["automerge"] is False
    enabled = [rule for rule in config["packageRules"] if rule.get("automerge") is True]
    scopes = {}
    for rule in enabled:
        managers = tuple(rule.get("matchManagers", []))
        assert managers in {("pep621",), ("github-actions",)}
        assert managers not in scopes
        scopes[managers] = rule
    assert set(scopes) == {("pep621",), ("github-actions",)}

    dev_rule = scopes[("pep621",)]
    assert dev_rule["matchDepTypes"] == ["project.optional-dependencies"]
    assert dev_rule["matchJsonata"] == ["managerData.depGroup = 'dev'"]
    assert dev_rule["matchUpdateTypes"] == ["patch"]
    assert dev_rule["minimumReleaseAge"] == "3 days"
    assert set(dev_rule["matchPackageNames"]) == APPROVED
    dev_names = {
        requirement.partition(">=")[0]
        for requirement in pyproject["project"]["optional-dependencies"]["dev"]
    }
    assert set(dev_rule["matchPackageNames"]) <= dev_names

    actions_rule = scopes[("github-actions",)]
    assert actions_rule["groupName"] == "Weekly GitHub Actions"
    assert set(actions_rule) == {"matchManagers", "groupName", "automerge"}

    # Major updates remain opt-in and never auto-merge, even for GitHub Actions.
    major_rule = config["packageRules"][-1]
    assert major_rule["matchUpdateTypes"] == ["major"]
    assert major_rule["dependencyDashboardApproval"] is True
    assert major_rule["automerge"] is False
