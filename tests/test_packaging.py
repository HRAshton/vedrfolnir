"""Packaging and pre-startup safety invariants."""
from importlib import resources
from pathlib import Path


def test_dashboard_resource_installed():
    content = (resources.files("vedrfolnir") / "static" / "dashboard.html").read_text(encoding="utf-8")
    assert "Vedrfolnir" in content
    assert "</html>" in content


def test_sql_examples_present():
    root = Path(__file__).resolve().parents[1]
    assert (root / "queries" / "historical_jobs.sql").exists()
    assert (root / "queries" / "live_jobs.sql").exists()
    assert not (root / "queries" / "historical_tests.sql").exists()
