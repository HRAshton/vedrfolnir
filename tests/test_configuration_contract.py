"""Numeric operational defaults remain aligned across source, Compose and examples."""
import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def numeric_defaults(source):
    defaults = {}
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "integer" and len(node.args) == 2
                and all(isinstance(arg, ast.Constant) for arg in node.args)):
            name, default = (arg.value for arg in node.args)
            defaults[name] = default
    return defaults


def test_numeric_defaults_match_example_and_source_compose():
    defaults = numeric_defaults((ROOT / "src/vedrfolnir/config.py").read_text())
    example = dict(re.findall(r"^(ETA_[A-Z0-9_]+)=(\d+)\s*$", (ROOT / ".env.example").read_text(), re.M))
    compose = {key: (variable, value) for key, variable, value in re.findall(
        r"^\s+(ETA_[A-Z0-9_]+): \$\{(ETA_[A-Z0-9_]+):-(\d+)\}",
        (ROOT / "compose.yaml").read_text(), re.M)}
    assert defaults, "No authoritative numeric defaults found"
    for name, value in defaults.items():
        assert int(example[name]) == value, name
        assert compose[name] == (name, str(value)), name

