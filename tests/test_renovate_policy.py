"""Only narrow development dependency bumps can receive bot approval."""
from scripts.approve_renovate import safe_pyproject_change


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
