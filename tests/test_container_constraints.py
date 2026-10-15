"""Container constraints must be complete, normalized, and deterministic."""
import pytest
from scripts.generate_container_constraints import format_lock


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
