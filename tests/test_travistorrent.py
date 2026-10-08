"""TravisTorrent dataset contract and chronological benchmark regression tests.

The canonical schema is documented at:
https://testroots.github.io/travistorrent-site/page_dataformat/
The first test includes a published build ID and timestamp from the paper,
but it is NOT a claim to have downloaded the full source archive.
"""
import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import zipfile

import pytest

from vedrfolnir.travistorrent import _candidate_file, _job_count, load_builds
from vedrfolnir.benchmark_travistorrent import main as quickstart

COLUMNS = ["tr_build_id", "tr_job_id", "tr_started_at", "tr_duration",
           "tr_jobs", "tr_status", "gh_project_name", "gh_lang", "gh_is_pr"]


def write_zip(path, rows, nested="final-2017-01-25.csv"):
    data = io.StringIO()
    writer = csv.DictWriter(data, COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("readme.txt", "TravisTorrent test archive")
        archive.writestr(nested, data.getvalue())


def row(build, job, at, duration, *, jobs="", project="rails/rails", status="passed"):
    return dict(tr_build_id=str(build), tr_job_id=str(job), tr_started_at=at,
                tr_duration=str(duration), tr_jobs=jobs, tr_status=status,
                gh_project_name=project, gh_lang="Ruby", gh_is_pr="false")


def test_published_schema_deduplicates_job_rows_and_uses_build_duration(tmp_path):
    path = tmp_path / "real-schema.csv.zip"
    # Published paper's example tr_build_id, tr_job_id, tr_started_at, duration:
    rows = [row(23298954, 23298981, "2014-04-18 19:12:32", 23389,
                jobs="[23298955,23298981]"),
            row(23298954, 23298955, "2014-04-18 19:12:32", 23389,
                jobs="[23298955,23298981]"),
            row(999, 11, "2014-04-20 10:00:00", 30, project="x/y")]
    write_zip(path, rows, nested="data/final.csv")
    diag = {}
    builds = load_builds(path, limit=10, stats=diag)
    assert len(builds) == 2
    first = builds[0]
    assert first.plan_id == "23298954"
    assert first.task_count == 2
    assert first.features["job_count"] == 2
    assert (first.finished_at - first.started_at).total_seconds() == 23389
    assert first.family == "rails/rails"
    assert diag["duplicate_job_rows"] == 1
    assert diag["rows_scanned"] == 3


def test_selects_earliest_distinct_builds_independent_of_csv_order(tmp_path):
    path = tmp_path / "source.zip"
    rows = []
    for x in range(12, -1, -1):
        at = f"2016-01-{1+x:02d}T12:00:00Z"
        rows += [row(x, x*10, at, 60+x), row(x, x*10+1, at, 60+x)]
    rows += [row(44, "bad", "2016-01-14T12:00:00Z", -10)]
    write_zip(path, rows)
    builds = load_builds(path, limit=5)
    assert [x.plan_id for x in builds] == ["0", "1", "2", "3", "4"]
    assert all(b.task_count == 2 for b in builds)
    assert len({b.plan_id for b in builds}) == 5


def test_published_2017_csv_schema_uses_gh_build_started_at(tmp_path):
    """The canonical public export has no tr_started_at column."""
    path = tmp_path / "final-2017-01-25.csv.gz"
    import gzip
    with gzip.open(path, "wt", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=[
            "row", "tr_build_id", "tr_job_id", "gh_project_name",
            "gh_build_started_at", "tr_duration", "tr_status", "tr_jobs",
            "gh_lang", "gh_is_pr",
        ])
        writer.writeheader()
        writer.writerow({
            "row": 1, "tr_build_id": "145", "tr_job_id": "999",
            "gh_project_name": "org/repo", "gh_build_started_at": "2016-01-01 13:20:00",
            "tr_duration": "600", "tr_status": "passed", "tr_jobs": "999#1000",
            "gh_lang": "Ruby", "gh_is_pr": "false",
        })
        writer.writerow({
            "row": 2, "tr_build_id": "145", "tr_job_id": "1000",
            "gh_project_name": "org/repo", "gh_build_started_at": "2016-01-01 13:20:00",
            "tr_duration": "600", "tr_status": "passed", "tr_jobs": "999#1000",
        })
    builds = load_builds(path, limit=10)
    assert len(builds) == 1
    assert builds[0].plan_id == "145"
    assert builds[0].task_count == 2
    assert (builds[0].finished_at - builds[0].started_at).total_seconds() == 600


def test_official_2017_schema_runs_one_click_benchmark(tmp_path):
    """Exercise CSV.GZ → loader → online evaluation → HTML report end-to-end."""
    import gzip
    path = tmp_path / "final-2017-01-25.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=[
            "tr_build_id", "tr_job_id", "gh_project_name", "gh_build_started_at",
            "tr_duration", "tr_status", "tr_jobs", "gh_lang", "gh_is_pr",
        ])
        writer.writeheader()
        for i in range(24):
            writer.writerow({
                "tr_build_id": str(i + 1), "tr_job_id": str(i * 10),
                "gh_project_name": "org/repo", "gh_build_started_at":
                    datetime(2016, 1, 1, 0, i, tzinfo=timezone.utc).isoformat(),
                "tr_duration": str(120 + i * 3), "tr_status": "passed",
                "tr_jobs": str(i * 10), "gh_lang": "ruby", "gh_is_pr": "false",
            })
    out = tmp_path / "results"
    assert quickstart(["--data", str(path), "--max-builds", "24", "--warmup", "5",
                       "--min-training", "5", "--output", str(out), "--offline", "--no-open"]) == 0
    summary = json.loads((out / "summary.json").read_text())
    assert summary["plans_total"] == 24
    assert summary["plans_evaluated"] == 19
    assert (out / "report.html").is_file()
    assert (out / "predictions.csv").is_file()


def test_wrong_csv_schema_reports_observed_headers(tmp_path):
    path = tmp_path / "wrong.csv"
    path.write_text("tr_build_id,gh_project_name,tr_duration,tr_status,other\n")
    with pytest.raises(ValueError, match="gh_build_started_at") as exc:
        load_builds(path, limit=10)
    assert "Observed columns" in str(exc.value)
    assert "other" in str(exc.value)


def test_missing_schema_rejected(tmp_path):
    path = tmp_path / "unknown.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("unknown.csv", "foo,bar\n1,2\n")
    with pytest.raises(ValueError, match="No usable TravisTorrent CSV"):
        load_builds(path, limit=5)


def test_jobs_list_formats():
    assert _job_count("[1,2,3]") == 3
    assert _job_count("123#456#789") == 3
    assert _job_count("['1', '2']") == 2
    assert _job_count("") == 0


def test_figshare_file_selection():
    data = {"files": [
        {"name": "notes.txt", "size": 100, "id": 1},
        {"name": "old.csv.zip", "size": 10000, "id": 2},
        {"name": "final-2017-01-25.csv.zip", "size": 9000, "id": 3},
    ]}
    assert _candidate_file(data)["id"] == 3


def test_one_click_offline_local_archive_generates_report(tmp_path):
    path = tmp_path / "travis.zip"
    rows = []
    for i in range(30):
        at = datetime(2016, 1, 1, 0, i, tzinfo=timezone.utc).isoformat()
        project = "rails/rails" if i % 2 == 0 else "example/repo"
        rows.append(row(100 + i, i * 10, at, 100 + i * 3, jobs=f"{i*10}#{i*10+1}", project=project))
        rows.append(row(100 + i, i * 10 + 1, at, 100 + i * 3, project=project))
    write_zip(path, rows)
    out = tmp_path / "results"
    assert quickstart(["--data", str(path), "--max-builds", "30", "--warmup", "5",
                       "--min-training", "5", "--output", str(out), "--offline", "--no-open"]) == 0
    summary = json.loads((out / "summary.json").read_text())
    assert summary["plans_total"] == 30
    assert summary["plans_evaluated"] == 25
    assert summary["format"] == "travistorrent"
    assert summary["river_used_plans"] == 0  # 30 builds < 40 completed-build validation cohort
    assert summary["river_shadow_ready_plans"] > 0
    assert summary["river_only"]["count"] == summary["baseline_on_river_subset"]["count"]
    assert summary["dataset_stats"]["duplicate_job_rows"] == 30
    assert "build-start ETA" in summary["benchmark_scope"]
    with (out / "predictions.csv").open(newline="") as stream:
        scored = list(csv.DictReader(stream))
    assert len(scored) == 30
    assert all(item["phase"] == "start" for item in scored)
    html = (out / "report.html").read_text()
    assert "Absolute error distribution" in html
    assert "20 worst plan-start errors" in html
    assert "no per-job runtimes" in html


def test_figshare_metadata_download_checksum_and_offline_cache(tmp_path, monkeypatch):
    import hashlib
    import io
    from vedrfolnir import travistorrent as source

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("final.csv", "tr_build_id,tr_started_at,tr_duration,gh_project_name,tr_status\n"
                      "1,2016-01-01T00:00:00Z,99,repo/x,passed\n")
    archive_bytes = archive.getvalue()
    meta = {"files": [{"id": 4, "name": "final-2017-01-25.csv.zip",
                       "size": len(archive_bytes), "computed_md5": hashlib.md5(archive_bytes).hexdigest(),
                       "download_url": "https://example.test/files/4"}]}
    urls = []

    def fake_open(request, timeout):
        urls.append(request.full_url)
        if request.full_url.endswith("/metadata"):
            return io.BytesIO(json.dumps(meta).encode())
        if request.full_url.endswith("/files/4"):
            return io.BytesIO(archive_bytes)
        raise AssertionError(f"unexpected URL {request.full_url}")

    monkeypatch.setattr(source, "urlopen", fake_open)
    folder = tmp_path / "download"
    path = source.download_dataset(folder, metadata_url="https://example.test/metadata")
    assert path.read_bytes() == archive_bytes
    assert len(urls) == 2
    assert source.download_dataset(folder, offline=True) == path
    assert len(urls) == 2
    assert len(load_builds(path)) == 1


def test_figshare_rejects_corrupt_download(tmp_path, monkeypatch):
    import io
    from vedrfolnir import travistorrent as source

    meta = {"files": [{"id": 4, "name": "final.csv.zip", "size": 3,
                       "computed_md5": "wrong", "download_url": "https://example.test/files/4"}]}

    def fake_open(request, timeout):
        return io.BytesIO(json.dumps(meta).encode() if request.full_url.endswith("metadata") else b"abc")

    monkeypatch.setattr(source, "urlopen", fake_open)
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        source.download_dataset(tmp_path, metadata_url="https://example.test/metadata")
    assert not list(tmp_path.glob("*.zip"))
    assert not list(tmp_path.glob("*.partial"))
