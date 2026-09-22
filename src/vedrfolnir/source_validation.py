"""Read-only source preflight and normalized-row checks used by ingestion.

The preflight reads source data but does not modify the app's database.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone

from .features import TERMINAL, timestamp
from .pooling import pool_of, capacity_slots, declared_capacities

ALIASES = {
    "success": "passed", "succeeded": "passed", "completed": "passed",
    "finished": "passed", "done": "passed", "error": "failed",
    "failure": "failed", "timeout": "timed_out", "timedout": "timed_out",
    "waiting": "queued", "blocked": "pending", "created": "queued",
    "scheduled": "queued", "in_progress": "running", "started": "running",
    "executing": "running", "aborted": "cancelled", "canceled": "cancelled",
}
ALLOWED = TERMINAL | {"running", "queued", "pending"}
REQUIRED_IDS = {
    "historical_jobs": ("run_id", "job_id"),
    "live_jobs": ("run_id", "job_id"),
    "plans": ("plan_id",),
}


def normalize_status(value):
    key = str(value or "unknown").lower().strip()
    return ALIASES.get(key, key)


def validate_records(kind, rows, *, error_limit=20):
    """Report non-sensitive issues (row number and code, never row values)."""
    if kind not in REQUIRED_IDS:
        raise ValueError("Unsupported normalized dataset")
    errors, warnings = [], []
    seen = set()
    statuses = Counter()
    unknown_vms = 0
    count = 0
    for number, row in enumerate(rows, 1):
        count += 1

        def issue(code):
            if len(errors) < error_limit:
                errors.append({"row": number, "code": code})

        if not isinstance(row, dict):
            issue("not_a_mapping")
            continue
        for field in REQUIRED_IDS[kind]:
            if not str(row.get(field) or "").strip():
                issue("missing_" + field)
        identifier = row.get("plan_id" if kind == "plans" else "run_id")
        if identifier is not None and str(identifier).strip():
            if str(identifier) in seen:
                issue("duplicate_id")
            seen.add(str(identifier))
        status = normalize_status(row.get("status"))
        statuses[status] += 1
        if status not in ALLOWED:
            issue("unknown_status")
        if kind == "historical_jobs" and status not in TERMINAL:
            issue("historical_status_not_terminal")
        fields = (("started_at", "finished_at") if kind == "historical_jobs" else
                  ("started_at", "finished_at", "queued_at") if kind == "live_jobs"
                  else ("started_at", "created_at", "finished_at"))
        dates = {}
        for field in fields:
            raw = row.get(field)
            if raw is None or raw == "":
                dates[field] = None
                continue
            try:
                dates[field] = timestamp(raw)
            except (TypeError, ValueError, OverflowError):
                dates[field] = None
                issue("invalid_" + field)
            if isinstance(raw, str) and not (raw.endswith("Z") or
                    "+" in raw[10:] or "-" in raw[10:]):
                if "naive_timestamps_interpreted_as_utc" not in warnings:
                    warnings.append("naive_timestamps_interpreted_as_utc")
        start = dates.get("started_at") or dates.get("created_at")
        finish = dates.get("finished_at")
        queued = dates.get("queued_at")
        if kind == "historical_jobs":
            if start is None:
                issue("missing_started_at")
            if finish is None:
                issue("missing_finished_at")
        elif kind == "plans":
            if start is None:
                issue("missing_started_at")
            if status in TERMINAL and finish is None:
                issue("terminal_plan_missing_finished_at")
        else:
            if status == "running" and start is None:
                issue("running_job_missing_started_at")
            if status in {"passed", "failed", "timed_out"} and finish is None:
                issue("finished_job_missing_finished_at")
        if start and finish and (finish <= start if kind == "historical_jobs"
                                  else finish < start):
            issue("finished_before_started")
        if queued and start and queued > start:
            issue("queued_after_started")
        if queued and finish and queued > finish:
            issue("queued_after_finished")
        if kind in ("plans", "live_jobs") and row.get("priority") not in (None, ""):
            try:
                int(row["priority"])
            except (ValueError, TypeError, OverflowError):
                issue("invalid_priority")
        if kind in ("historical_jobs", "live_jobs"):
            try:
                pool_of(row)
                if kind == "live_jobs":
                    capacity_slots(row.get("sched_capacity_slots"))
            except (TypeError, ValueError, OverflowError):
                issue("invalid_pool_contract")
            if str(row.get("vm_name") or "unknown") == "unknown":
                unknown_vms += 1
    if not count:
        warnings.append("no_rows_in_dataset")
    if kind == "historical_jobs" and count and unknown_vms == count:
        warnings.append("no_vm_inventory_in_history")
    return {
        "dataset": kind, "rows_checked": count,
        "status_counts": dict(sorted(statuses.items())),
        "missing_vm_name_rows": unknown_vms,
        "errors": errors, "warnings": warnings, "ok": not errors,
    }


def validate_history_page(rows, cursor, batch_limit, now):
    """A cursor may advance only over an ordered, bounded, completed page."""
    report = validate_records("historical_jobs", rows)
    if not report["ok"]:
        return report
    errors = report["errors"]
    if len(rows) > batch_limit:
        errors.append({"row": 0, "code": "historical_batch_limit_exceeded"})
    previous = (timestamp(cursor[0]), str(cursor[1]))
    for number, row in enumerate(rows, 1):
        key = (timestamp(row["finished_at"]), str(row["run_id"]))
        if len(errors) < 20 and key <= previous:
            errors.append({"row": number, "code": "historical_cursor_order_invalid"})
        if len(errors) < 20 and key[0] > timestamp(now):
            errors.append({"row": number, "code": "historical_finish_in_future"})
        previous = key
    report["ok"] = not errors
    return report


def checked_report(report):
    if not report["ok"]:
        example = ", ".join(f'row {e["row"]}: {e["code"]}'
                            for e in report["errors"][:5])
        raise ValueError(f'Invalid {report["dataset"]} input ({example}); fix the source mapping')
    return report


def require_valid(kind, rows):
    """Reject invalid pages/snapshots before persisting any of their rows."""
    return checked_report(validate_records(kind, rows))


def require_history_page(rows, cursor, batch_limit, now):
    return checked_report(validate_history_page(rows, cursor, batch_limit, now))


def validate_source(source, *, sample_history=200, finished_since_days=60):
    """Fetch source snapshots with read-only operations, no model or app DB."""
    cursor = datetime(2000, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None)
    history = source.historical(cursor, "", sample_history)
    since = (datetime.now(timezone.utc) -
             timedelta(days=finished_since_days)).replace(tzinfo=None)
    plans, jobs = source.live(since)
    sections = {
        "historical_jobs": validate_history_page(history, (cursor, ""), sample_history,
                                                 datetime.now(timezone.utc)),
        "plans": validate_records("plans", plans),
        "live_jobs": validate_records("live_jobs", jobs),
    }
    pools = source.pools() if hasattr(source, "pools") else []
    pool_errors = []
    for index, row in enumerate(pools, 1):
        try:
            pool_of(row)
            capacity_slots(row.get("sched_capacity_slots"))
        except (TypeError, ValueError, OverflowError):
            pool_errors.append({"row": index, "code": "invalid_pool_contract"})
    try:
        declared_capacities(pools, jobs)
    except (TypeError, ValueError, OverflowError):
        pool_errors.append({"row": 0, "code": "conflicting_pool_capacity"})
    sections["scheduler_pools"] = {
        "dataset": "scheduler_pools", "rows_checked": len(pools),
        "errors": pool_errors[:20], "warnings": [],
        "ok": not pool_errors,
    }
    warnings = []
    if not plans and not jobs:
        warnings.append("no_live_activity_observed")
    active = {str(row["plan_id"]) for row in plans if row.get("plan_id") and
              normalize_status(row.get("status")) not in TERMINAL}
    visible = {str(row["plan_id"]) for row in jobs if row.get("plan_id")}
    if active - visible:
        warnings.append("active_plans_without_visible_jobs")
    if any(normalize_status(r.get("status")) in ("queued", "pending")
           for r in jobs) and not any(r.get("queued_at") for r in jobs):
        warnings.append("queue_timestamps_unavailable")
    return {"ok": all(part["ok"] for part in sections.values()),
            "read_only": True, "historical_sample_limit": sample_history,
            "datasets": sections, "warnings": warnings}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only source contract check")
    parser.add_argument("--mode", choices=("files", "mssql"), required=True)
    parser.add_argument("--csv-dir", default="examples/data")
    parser.add_argument("--query-dir", default="queries")
    parser.add_argument("--history-sample", type=int, default=200)
    args = parser.parse_args(argv)
    if not 1 <= args.history_sample <= 10000:
        parser.error("--history-sample must be between 1 and 10000")
    from .source import CSVSource, MSSQLSource
    source = CSVSource(args.csv_dir) if args.mode == "files" else MSSQLSource(args.query_dir)
    try:
        report = validate_source(source, sample_history=args.history_sample)
    except Exception as exc:
        # MSSQL failures can contain SQL/credentials; do not print exception text.
        print(json.dumps({"ok": False, "error": "source_read_failed",
                          "exception_type": type(exc).__name__,
                          "advice": "Check source files, query mapping and read-only credentials."}))
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
