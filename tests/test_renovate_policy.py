"""Only narrow development dependency bumps can receive bot approval."""
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


def test_renovate_automerge_rule_matches_only_dev_extra():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "renovate.json").read_text(encoding="utf-8"))
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    rules = [rule for rule in config["packageRules"] if rule.get("automerge") is True]

    assert config["automerge"] is False
    assert len(rules) == 1
    rule = rules[0]
    assert rule["matchManagers"] == ["pep621"]
    assert rule["matchDepTypes"] == ["project.optional-dependencies"]
    assert rule["matchJsonata"] == ["managerData.depGroup = 'dev'"]
    assert rule["matchUpdateTypes"] == ["patch"]
    assert set(rule["matchPackageNames"]) == APPROVED
    dev_names = {
        requirement.partition(">=")[0]
        for requirement in pyproject["project"]["optional-dependencies"]["dev"]
    }
    assert set(rule["matchPackageNames"]) <= dev_names
