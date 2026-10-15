"""Container constraints must be complete, normalized, and deterministic."""
from pathlib import Path
import tomllib

import pytest
from scripts.generate_container_constraints import format_lock, normalize


def test_complete_and_stable_lock_output():
    rows = [{"name": "uvicorn", "version": "0.35.0"},
            {"name": "FastAPI", "version": "0.116.0"},
            {"name": "pip", "version": "25.0"}]
    result = format_lock(rows, {"uvicorn", "fastapi"})
    assert result.endswith("fastapi==0.116.0\nuvicorn==0.35.0\n")
    assert "pip==" not in result


@pytest.mark.parametrize("rows", [
    [{"name": "FastAPI", "version": "0.116.0"}],
    [{"name": "FastAPI", "version": "0.116.0"},
     {"name": "fastapi", "version": "0.116.0"}],
    [{"name": "FastAPI", "version": "0.116.0"},
     {"name": "bad/name", "version": "1"}],
])
def test_rejects_missing_duplicate_or_unsafe_inventory(rows):
    with pytest.raises(ValueError):
        format_lock(rows, {"fastapi", "uvicorn"})


def test_committed_lock_is_canonical_and_enforced():
    root = Path(__file__).resolve().parents[1]
    lock = (root / "requirements/container-constraints.txt").read_text(encoding="utf-8")
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    rows = []
    for line in lock.splitlines():
        if not line.startswith("#"):
            name, delimiter, version = line.partition("==")
            assert delimiter and name and version
            rows.append({"name": name, "version": version})
    required = {normalize(item.split(">=")[0]) for item in project["project"]["dependencies"]}
    assert format_lock(rows, required) == lock
    assert len(rows) >= len(required)
    assert format_lock([{**r, "version": "0.0"} if r["name"] == "fastapi" else r
                        for r in rows], required) != lock

    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    ci = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "COPY requirements/container-constraints.txt" in dockerfile
    assert "--constraint requirements/container-constraints.txt" in dockerfile
    assert "generate_container_constraints.py --check" in ci
    assert "generate_container_constraints.py --verify-inventory" in ci
