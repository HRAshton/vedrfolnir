"""Shared smoke fixtures are reproducible and valid at an explicit UTC clock."""
from datetime import datetime, timezone
import pytest
from scripts.fixtures import make_csv_fixture
from vedrfolnir.source import CSVSource
from vedrfolnir.source_validation import validate_source


def test_fixture_reproducible_at_fixed_clock(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir()
    right.mkdir()
    now = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
    make_csv_fixture(left, now=now)
    make_csv_fixture(right, now=now)
    assert {p.name: p.read_bytes() for p in left.iterdir()} == {
        p.name: p.read_bytes() for p in right.iterdir()}
    report = validate_source(CSVSource(left))
    assert report["ok"] and report["warnings"] == []
    assert report["datasets"]["historical_jobs"]["rows_checked"] == 6


def test_naive_clock_rejected(tmp_path):
    with pytest.raises(ValueError, match="timezone-aware"):
        make_csv_fixture(tmp_path, now=datetime(2026, 10, 13))
