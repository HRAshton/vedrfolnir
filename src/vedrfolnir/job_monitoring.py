"""Prospective first-job observations, delayed labels and bounded monitoring."""
from collections import defaultdict
from datetime import timedelta
from math import isfinite

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.sqlite import insert

from .db import CurrentJob, HistoricalRun, JobPrediction, SchedulerPool, SyncState
from .features import TERMINAL, quantile, timestamp
from .job_forecasting import job_forecast, job_projection
from .pooling import pool_of

SAMPLE_LIMIT = 2000


def record_and_match(session, config, now):
    """Called only after a successful source poll. Never learn a job ML model."""
    saved = session.get(SyncState, "live_snapshot_at")
    observed = timestamp(saved.value) if saved else now if config.mode == "demo" else None
    fresh = observed is not None and 0 <= (now - observed).total_seconds() <= 3 * config.poll_seconds
    # Outcomes arrive from either completed history or the current terminal
    # snapshot. Inner joins avoid one lookup per stored forecast.
    matched = 0
    for model in (HistoricalRun, CurrentJob):
        pairs = session.execute(select(JobPrediction, model).join(
            model, model.run_id == JobPrediction.run_id).where(
                JobPrediction.outcome_status.is_(None), model.status.in_(list(TERMINAL)),
                model.finished_at.is_not(None), model.finished_at <= now)).all()
        for prediction, outcome in pairs:
            start, finish = timestamp(outcome.started_at), timestamp(outcome.finished_at)
            at = timestamp(prediction.predicted_at)
            if outcome.job_id != prediction.job_id:
                prediction.outcome_issue = "job_identity_mismatch"
                continue
            prediction.actual_started_at = start
            prediction.actual_finished_at = finish
            prediction.outcome_status = outcome.status
            prediction.outcome_issue = ("completion_before_forecast" if finish < at else
                                        "invalid_start_time" if outcome.status not in ("cancelled", "skipped")
                                        and (start is None or start > finish) else
                                        "start_before_queued_forecast" if prediction.observed_status == "queued"
                                        and outcome.status not in ("cancelled", "skipped")
                                        and start is not None and start < at else None)
            if (prediction.forecast_available and outcome.status not in ("cancelled", "skipped")
                    and prediction.outcome_issue is None):
                actual_remaining = (finish - at).total_seconds() / 60
                prediction.finish_error_minutes = prediction.predicted_remaining_minutes - actual_remaining
                if prediction.observed_status == "queued":
                    prediction.wait_error_minutes = prediction.predicted_wait_minutes - max(0.0, (start - at).total_seconds() / 60)
            matched += 1
        session.flush()
    cutoff = now - timedelta(days=config.job_retention_days)
    active = select(CurrentJob.run_id).where(CurrentJob.status.in_(["queued", "running", "pending"]))
    pruned = session.execute(delete(JobPrediction).where(
        or_(JobPrediction.actual_finished_at < cutoff,
            and_(JobPrediction.outcome_status.is_(None), JobPrediction.predicted_at < cutoff)),
        JobPrediction.run_id.not_in(active))).rowcount
    created = 0
    if fresh:
        jobs = list(session.scalars(select(CurrentJob).order_by(CurrentJob.run_id)))
        declared = dict(session.execute(select(SchedulerPool.sched_pool_id, SchedulerPool.sched_capacity_slots)).all())
        scheduler, projections = job_projection(session, now, jobs, declared=declared,
                                                max_history=config.max_history)
        existing = set(session.execute(select(JobPrediction.run_id, JobPrediction.observed_status).join(
            CurrentJob, CurrentJob.run_id == JobPrediction.run_id)).all())
        known_finished = set(session.scalars(select(HistoricalRun.run_id).join(
            CurrentJob, CurrentJob.run_id == HistoricalRun.run_id).where(HistoricalRun.finished_at <= now)))
        for job in jobs:
            if (job.status not in ("queued", "running", "pending")
                    or (job.run_id, job.status) in existing or job.run_id in known_finished):
                continue
            forecast = job_forecast(scheduler, job, projections)
            payload = {
                "run_id": job.run_id, "plan_id": job.plan_id, "job_id": job.job_id,
                "sched_pool_id": pool_of(job), "observed_status": job.status,
                "predicted_at": now, "source_observed_at": observed,
                "forecast_available": forecast["forecast_available"],
                "prediction_method": forecast["prediction_method"],
                "predicted_start_at": timestamp(forecast["predicted_start_at"]),
                "predicted_finish_at": timestamp(forecast["predicted_finish_at"]),
                "predicted_wait_minutes": forecast["predicted_wait_minutes"],
                "predicted_remaining_minutes": forecast["predicted_remaining_minutes"],
                "unavailable_reasons": forecast["unavailable_reasons"],
                "evidence_warnings": forecast["evidence_warnings"],
                "capacity_evidence": scheduler.pool_snapshots.get(pool_of(job), {}),
            }
            result = session.execute(insert(JobPrediction).values(payload).on_conflict_do_nothing(
                index_elements=["run_id", "observed_status"]))
            created += result.rowcount
    return {"recorded": created, "matched": matched, "pruned": pruned}


def recent_outcomes(session):
    """Include unavailable first observations, not just successful forecasts."""
    result = {}
    for status in ("queued", "running", "pending"):
        result[status] = list(session.scalars(select(JobPrediction).where(
            JobPrediction.observed_status == status,
            JobPrediction.outcome_status.is_not(None),
        ).order_by(JobPrediction.actual_finished_at.desc(), JobPrediction.id.desc()).limit(SAMPLE_LIMIT)))
    return result


def error_stats(values):
    absolute = [abs(v) for v in values]
    return {"count": len(values),
            "mae_minutes": round(sum(absolute) / len(values), 3) if values else None,
            "p90_absolute_error_minutes": round(quantile(absolute, .9), 3) if values else None,
            "signed_bias_minutes": round(sum(values) / len(values), 3) if values else None}


def summarize_cohort(status, rows):
    valid = [r for r in rows if r.outcome_status not in ("cancelled", "skipped") and r.outcome_issue is None]
    reasons = defaultdict(int)
    for row in valid:
        for reason in row.unavailable_reasons:
            reasons[reason] += 1
    return {"observed_status": status, "completed_observations": len(rows),
            "evaluated_observations": len(valid),
            "available_forecasts": sum(r.forecast_available for r in valid),
            "excluded_outcomes": len(rows) - len(valid),
            "unavailable_reasons": dict(sorted(reasons.items())),
            "wait_error": error_stats([r.wait_error_minutes for r in valid if r.wait_error_minutes is not None]),
            "finish_error": error_stats([r.finish_error_minutes for r in valid if r.finish_error_minutes is not None])}


def summarize_jobs(session, retention_days):
    cohorts = [summarize_cohort(status, rows) for status, rows in recent_outcomes(session).items()]
    return {"cohorts": cohorts, "sample_limit_per_status": SAMPLE_LIMIT, "retention_days": retention_days,
            "stored_observations": session.scalar(select(func.count(JobPrediction.id))),
            "awaiting_outcomes": session.scalar(select(func.count(JobPrediction.id)).where(JobPrediction.outcome_status.is_(None))),
            "oldest_unmatched_at": session.scalar(select(func.min(JobPrediction.predicted_at)).where(JobPrediction.outcome_status.is_(None))),
            "label_issues": session.scalar(select(func.count(JobPrediction.id)).where(JobPrediction.outcome_issue.is_not(None))),
            "job_p90_coverage": None}


def observed_pool_load(evidence):
    """Local pool queue pressure at capture, never today's reconstructed load."""
    if not isinstance(evidence, dict):
        return "unknown"
    slots, queued = evidence.get("capacity_slots"), evidence.get("queued_jobs")
    def nonnegative(value):
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and isfinite(value) and value >= 0)
    if not nonnegative(slots):
        return "unknown"
    if slots == 0:
        return "paused"
    if not nonnegative(queued):
        return "unknown"
    pressure = queued / slots
    return "saturated" if pressure >= 2 else "busy" if pressure >= .5 else "idle"


def job_workload_metrics(session, group_by):
    """Break down the same bounded sample as headline job metrics."""
    if group_by not in ("pool", "job_type", "load"):
        raise ValueError("Unsupported job cohort dimension")
    cohorts = []
    for status, rows in recent_outcomes(session).items():
        grouped = defaultdict(list)
        for row in rows:
            group = (row.sched_pool_id if group_by == "pool" else row.job_id if group_by == "job_type"
                     else observed_pool_load(row.capacity_evidence))
            grouped[group].append(row)
        for group, sample in sorted(grouped.items()):
            cohorts.append({"group": group, **summarize_cohort(status, sample)})
    return {"group_by": group_by, "sample_limit_per_status": SAMPLE_LIMIT,
            "sample_scope": "latest_completed_per_status", "cohorts": cohorts}


def daily_job_errors(session, days, now):
    first = now.date() - timedelta(days=days - 1)
    rows = [row for group in recent_outcomes(session).values() for row in group
            if row.outcome_status not in ("cancelled", "skipped") and row.outcome_issue is None
            and first <= timestamp(row.actual_finished_at).date() <= now.date()]
    buckets = defaultdict(list)
    for row in rows:
        buckets[(timestamp(row.actual_finished_at).date(), row.observed_status)].append(row)
    result = []
    for i in range(days):
        day = first + timedelta(days=i)
        group = {}
        for status in ("queued", "running"):
            sample = buckets[(day, status)]
            group[status] = {
                "completed_observations": len(sample),
                "wait_error": error_stats([r.wait_error_minutes for r in sample if r.wait_error_minutes is not None]),
                "finish_error": error_stats([r.finish_error_minutes for r in sample if r.finish_error_minutes is not None]),
            }
        result.append({"day": day.isoformat(), **group})
    return result
