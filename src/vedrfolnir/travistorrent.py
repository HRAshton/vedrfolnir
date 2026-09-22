"""TravisTorrent whole-build benchmark ingestion (never invents job timestamps).

Official format: each CSV row describes a job, but ``tr_duration`` and
``tr_started_at`` describe its parent *build*. See the public schema:
https://testroots.github.io/travistorrent-site/page_dataformat/
"""
from __future__ import annotations

import csv
from datetime import timedelta
import gzip
import hashlib
import heapq
import io
import json
import math
from pathlib import Path
import re
from urllib.request import Request, urlopen
import zipfile

from .benchmarking.models import WorkflowSummary, _date

ARTICLE_ID = 19314170
API_URL = f"https://api.figshare.com/v2/articles/{ARTICLE_ID}"
SOURCE_PAGE = f"https://figshare.com/articles/dataset/TravisTorrent/{ARTICLE_ID}"
HEADERS = {"User-Agent": "Vedrfolnir/0.1 (open-source workload ETA benchmark)"}
REQUIRED = {"tr_build_id", "tr_duration", "gh_project_name", "tr_status"}
# The published 2017 TravisTorrent dump uses gh_build_started_at. Earlier
# PoC fixtures used tr_started_at, which is not part of that published schema.
START_COLUMNS = ("gh_build_started_at", "tr_started_at")
TERMINAL = {"passed", "failed", "errored"}


def _safe_file_name(name):
    name = Path(name).name
    if not name or name in (".", "..") or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise ValueError("Unsafe dataset filename returned by Figshare")
    return name


def _candidate_file(metadata):
    files = metadata.get("files") or []
    choices = [f for f in files if (f.get("name") or "").lower().endswith((".zip", ".gz", ".csv"))]
    if not choices:
        raise ValueError(f"Figshare article {ARTICLE_ID} lists no CSV/ZIP/GZ files; see {SOURCE_PAGE}")
    choices.sort(key=lambda f: ("final" in f.get("name", "").lower(), f.get("size", 0)), reverse=True)
    return choices[0]


def download_dataset(folder: Path, *, offline=False, metadata_url=API_URL):
    """Download the public Figshare archive to a local cache, with MD5 verification.

    Network is used only to download the public trace, never for model training.
    """
    folder = Path(folder)
    local = sorted(f for f in folder.glob("*") if f.is_file() and f.name.lower().endswith((".zip", ".gz", ".csv")))
    if local:
        print(f"Using cached TravisTorrent data: {local[0]}", flush=True)
        return local[0]
    if offline:
        raise RuntimeError(f"No local TravisTorrent dataset under {folder}; run once online or supply --data PATH")
    try:
        with urlopen(Request(metadata_url, headers=HEADERS), timeout=30) as response:
            metadata = json.load(response)
        file = _candidate_file(metadata)
        name = _safe_file_name(file["name"])
        url = file.get("download_url") or f"https://ndownloader.figshare.com/files/{int(file['id'])}"
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / name
        temporary = folder / (name + ".partial")
        expected_md5 = file.get("computed_md5") or file.get("supplied_md5")
        checksum = hashlib.md5()
        total = 0
        print(f"Downloading {name} ({file.get('size', 0)/1_000_000:.0f} MB) from Figshare...", flush=True)
        with urlopen(Request(url, headers=HEADERS), timeout=120) as response, temporary.open("wb") as stream:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                stream.write(chunk)
                checksum.update(chunk)
                total += len(chunk)
                if total % (32 * 1024 * 1024) < len(chunk):
                    print(f"Downloaded {total / 1_000_000:.0f} MB", flush=True)
        if not total or (file.get("size") and total != int(file["size"])):
            raise ValueError(f"Incomplete download ({total} bytes)")
        if expected_md5 and checksum.hexdigest().lower() != expected_md5.lower():
            raise ValueError("Figshare MD5 checksum mismatch")
        temporary.replace(destination)
        return destination
    except (OSError, ValueError, KeyError) as exc:
        if 'temporary' in locals():
            temporary.unlink(missing_ok=True)
        raise RuntimeError(f"TravisTorrent download failed: {exc}. Manual download: {SOURCE_PAGE}") from exc


def _csv_members(path):
    """Iterate CSVs in a local CSV, CSV.GZ or ZIP archive; streams without unpacking."""
    path = Path(path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                if member.is_dir() or not member.filename.lower().endswith((".csv", ".csv.gz")):
                    continue
                with archive.open(member) as binary:
                    if member.filename.lower().endswith(".csv.gz"):
                        with gzip.GzipFile(fileobj=binary) as decompressed:
                            with io.TextIOWrapper(decompressed, encoding="utf-8-sig", errors="replace", newline="") as txt:
                                yield member.filename, csv.DictReader(txt)
                    else:
                        with io.TextIOWrapper(binary, encoding="utf-8-sig", errors="replace", newline="") as txt:
                            yield member.filename, csv.DictReader(txt)
    elif path.name.lower().endswith(".csv.gz"):
        with gzip.open(path, "rt", encoding="utf-8-sig", errors="replace", newline="") as txt:
            yield path.name, csv.DictReader(txt)
    elif path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as txt:
            yield path.name, csv.DictReader(txt)
    else:
        raise ValueError("Expected .csv, .csv.gz or .zip containing CSV")


def _job_count(raw):
    """Travis may serialize tr_jobs as an array or # separated IDs."""
    raw = (raw or "").strip()
    if not raw:
        return 0
    if raw.startswith("["):
        try:
            value = json.loads(raw)
            if isinstance(value, list):
                return len(set(map(str, value)))
        except (ValueError, TypeError):
            pass
    return len(set(re.findall(r"[A-Za-z0-9._:-]+", raw)))


def _bool(raw):
    return str(raw or "").strip().lower() in ("true", "yes", "1", "t")


def load_builds(path: Path, *, limit=10_000, stats=None):
    """Select earliest `limit` *distinct builds*, not arbitrary job rows.

    Scans the full source so row ordering cannot select later builds over
    earlier ones. Holds at most `limit` records in memory and streams the ZIP.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    records = {}
    latest = []  # (-epoch, build_id); root is the latest selected build
    scan = {"rows_scanned": 0, "rows_invalid": 0, "duplicate_job_rows": 0,
            "inconsistent_build_rows": 0, "csv_members_scanned": 0}
    found_schema = False
    rejected_headers = []
    start_column = None
    csv.field_size_limit(32 * 1024 * 1024)
    for member_name, reader in _csv_members(path):
        # Normalize field names once. Real dump headers can contain BOM or
        # whitespace; data rows remain streaming and are never materialized.
        reader.fieldnames = [str(name).strip().lstrip("\ufeff") for name in (reader.fieldnames or [])]
        cols = set(reader.fieldnames)
        start_column = next((name for name in START_COLUMNS if name in cols), None)
        if not REQUIRED.issubset(cols) or start_column is None:
            rejected_headers.append((member_name, sorted(cols)[:25]))
            continue  # Metadata CSVs can accompany the main dump.
        found_schema = True
        scan["csv_members_scanned"] += 1
        print(f"Reading {member_name}; retaining earliest {limit:,} unique builds...", flush=True)
        for row in reader:
            scan["rows_scanned"] += 1
            if scan["rows_scanned"] % 250_000 == 0:
                print(f"  {scan['rows_scanned']:,} rows · {len(records):,} selected builds", flush=True)
            build_id = str(row.get("tr_build_id") or "").strip()
            try:
                duration = float(row.get("tr_duration") or 0)
                started = _date((row.get(start_column) or "").strip())
                if not build_id or not math.isfinite(duration) or duration <= 0:
                    raise ValueError("invalid build")
            except (ValueError, TypeError, OverflowError):
                scan["rows_invalid"] += 1
                continue
            if str(row.get("tr_status") or "").strip().lower() not in TERMINAL:
                scan["rows_invalid"] += 1
                continue
            # No job runtime is available; tr_duration is BUILD duration.
            at = started.timestamp()
            row_key = build_id  # Travis build IDs are globally unique.
            previous = records.get(row_key)
            if previous is not None:
                scan["duplicate_job_rows"] += 1
                if abs(previous["duration"] - duration) > 1 or previous["started"] != started:
                    scan["inconsistent_build_rows"] += 1
                    continue
                job_id = str(row.get("tr_job_id") or "").strip()
                if job_id:
                    previous["jobs"].add(job_id)
                previous["declared_count"] = max(previous["declared_count"], _job_count(row.get("tr_jobs")))
                continue
            if len(records) >= limit and at >= -latest[0][0]:
                continue
            if len(records) >= limit:
                _, evicted = heapq.heappop(latest)
                del records[evicted]
            entry = {
                "started": started, "duration": duration,
                "project": str(row.get("gh_project_name") or "unknown").strip() or "unknown",
                "language": str(row.get("gh_lang") or "unknown").strip().lower() or "unknown",
                "is_pr": _bool(row.get("gh_is_pr")),
                "status": str(row.get("tr_status") or "unknown").strip().lower(),
                "declared_count": _job_count(row.get("tr_jobs")),
                "jobs": {str(row["tr_job_id"]).strip()} if row.get("tr_job_id") else set(),
            }
            records[row_key] = entry
            heapq.heappush(latest, (-at, row_key))
        print(f"  {scan['rows_scanned']:,} rows scanned, {len(records):,} builds selected", flush=True)
    if not found_schema:
        samples = "; ".join(f"{name}: {columns}" for name, columns in rejected_headers[:2])
        raise ValueError(
            f"No usable TravisTorrent CSV in {path}. Required: {sorted(REQUIRED)} "
            f"and one of {START_COLUMNS}. Observed columns: {samples or 'no CSV members'}"
        )
    if not records:
        raise ValueError(
            f"No eligible finished TravisTorrent builds in {path}; "
            f"scanned {scan['rows_scanned']:,} rows, rejected {scan['rows_invalid']:,}. "
            "Check tr_status, tr_duration and gh_build_started_at values."
        )
    result = []
    for build_id, item in records.items():
        count = max(1, item["declared_count"], len(item["jobs"]))
        started = item["started"]
        project = item["project"]
        lang = item["language"]
        # Only features observable at (or before) build submission; do not use
        # final status, log-extracted timing statistics, or job outcomes.
        features = {
            "plan_total_jobs": float(count),
            "log_plan_total_jobs": math.log1p(count),
            "job_count": float(count),
            "is_pull_request": float(item["is_pr"]),
            f"repository__{project}": 1.0,
            f"language__{lang}": 1.0,
        }
        result.append(WorkflowSummary(
            plan_id=build_id, family=project, started_at=started,
            finished_at=started + timedelta(seconds=item["duration"]),
            task_count=count, features=features))
    result.sort(key=lambda p: (p.started_at, p.plan_id))
    scan["builds_selected"] = len(result)
    scan["projects_selected"] = len(set(p.family for p in result))
    if stats is not None:
        stats.update(scan)
    return result
