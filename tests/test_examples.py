"""Repository examples are valid normalized inputs, not just documentation."""
from pathlib import Path

from vedrfolnir.source import CSVSource, MSSQLSource
from vedrfolnir.source_validation import validate_source

ROOT = Path(__file__).resolve().parents[1]


def test_csv_quickstart_example_matches_source_contract():
    example = ROOT / "examples" / "quickstart" / "csv"
    report = validate_source(CSVSource(example))
    assert report["ok"], report
    assert report["datasets"]["historical_jobs"]["rows_checked"] == 4
    assert report["datasets"]["live_jobs"]["rows_checked"] == 3
    assert report["datasets"]["plans"]["rows_checked"] == 1
    assert report["datasets"]["scheduler_pools"]["rows_checked"] == 1


def test_sqlserver_mapping_example_passes_read_only_query_preflight():
    example = ROOT / "examples" / "quickstart" / "sqlserver"
    source = MSSQLSource(example / "queries")
    for name in ("historical_jobs", "live_jobs", "plans", "scheduler_pools"):
        sql = source._query(name)
        assert "dbo.EtaDemo" in sql
        assert "REPLACE_ME" not in sql
    assert "CREATE TABLE" in (example / "sample_schema.sql").read_text(encoding="utf-8")
