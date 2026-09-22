"""SQL-schema-independent, point-in-time plan feature extraction."""
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from statistics import median
import math
import re
from sqlalchemy import select
from .db import HistoricalRun

TERMINAL = {"passed", "failed", "timed_out", "cancelled", "skipped"}
ACTIVE = {"running", "queued"}


def timestamp(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def elapsed_minutes(started, now):
    return max(0.0, (timestamp(now) - timestamp(started)).total_seconds() / 60) if started else 0.0


def quantile(values, fraction):
    """Interpolated empirical quantile, no numpy dependency."""
    data = sorted(values)
    if not data:
        return None
    index = (len(data) - 1) * fraction
    left = math.floor(index)
    right = math.ceil(index)
    return data[left] + (data[right] - data[left]) * (index - left)


def major(version):
    match = re.match(r"^(\d+)", str(version or ""))
    return match.group(1) if match else None


@dataclass(frozen=True)
class Estimate:
    p50: float
    p90: float
    sample_count: int
    group: str


class DurationEstimator:
    """Small bounded recent sample for each job, with broad fallback.

    Sampling is by most recently finished runs, not arbitrary SQL order.
    Historical durations represent time to terminal outcome, including failure.
    Cancelled runs are excluded because their endpoint is administrative.
    """
    def __init__(self, session, max_samples=500, *, finished_by=None):
        self.session = session
        self.max_samples = max_samples
        self.finished_by = finished_by
        self.cache = {}

    def _samples(self, job_id, version=None, db_type=None, major_only=False):
        key = (job_id, version, db_type, major_only)
        if key not in self.cache:
            stmt = select(HistoricalRun.duration_seconds).where(
                HistoricalRun.job_id == job_id,
                HistoricalRun.status.in_(["passed", "failed", "timed_out"]),
                HistoricalRun.duration_seconds > 0,
            )
            if self.finished_by is not None:
                stmt = stmt.where(HistoricalRun.finished_at <= self.finished_by)
            if version and major_only:
                stmt = stmt.where(HistoricalRun.version.like(f"{version}.%"))
            elif version:
                stmt = stmt.where(HistoricalRun.version == version)
            if db_type:
                stmt = stmt.where(HistoricalRun.db_type == db_type)
            stmt = stmt.order_by(HistoricalRun.finished_at.desc()).limit(self.max_samples)
            self.cache[key] = [float(x) / 60 for x in self.session.scalars(stmt)]
        return self.cache[key]

    def remaining(self, job, now):
        elapsed = elapsed_minutes(job.started_at, now) if job.status == "running" else 0.0
        version = str(job.version or "unknown")
        db_type = str(job.db_type or "unknown")
        job_id = str(job.job_id)
        candidates = [
            ("job_version_db", self._samples(job_id, version, db_type)),
            ("job_version", self._samples(job_id, version)),
        ]
        ver_major = major(version)
        if ver_major:
            candidates.append(("job_major", self._samples(job_id, ver_major, major_only=True)))
        candidates.append(("job", self._samples(job_id)))
        for group, values in candidates:
            # Conditional survival: use only runs that lasted longer than elapsed.
            surviving = [duration - elapsed for duration in values if duration > elapsed]
            if len(surviving) >= 5:
                return Estimate(quantile(surviving, .5), quantile(surviving, .9), len(surviving), group)
        # All historical runs shorter than the running job: tail extrapolation.
        broad = candidates[-1][1]
        if broad:
            longest = quantile(broad, .95)
            extra = max(1.0, 0.25 * longest)
            return Estimate(extra, max(extra * 2, 1.0), 0, "extrapolated")
        return Estimate(30.0, 90.0, 0, "global_fallback")


def make_plan_features(plan, jobs, all_jobs, duration_estimator, now, recent_completions=0):
    plan_jobs = [t for t in jobs if t.plan_id == plan.plan_id]
    global_active = [t for t in all_jobs if t.status in ACTIVE]
    global_queue = [t for t in global_active if t.status == "queued"]
    global_running = [t for t in global_active if t.status == "running"]
    estimates = {t.run_id: duration_estimator.remaining(t, now) for t in global_active}
    # Reuse estimates for a plan's active jobs not in global list (defensive).
    for t in plan_jobs:
        if t.status in ACTIVE | {"pending"} and t.run_id not in estimates:
            estimates[t.run_id] = duration_estimator.remaining(t, now)
    plan_queued = [t for t in plan_jobs if t.status == "queued"]
    plan_pending = [t for t in plan_jobs if t.status == "pending"]
    plan_running = [t for t in plan_jobs if t.status == "running"]
    completed = sum(t.status in TERMINAL for t in plan_jobs)
    count = len(plan_jobs)
    queued_work = sum(estimates[t.run_id].p50 for t in plan_queued)
    pending_work = sum(estimates[t.run_id].p50 for t in plan_pending)
    running_work = sum(estimates[t.run_id].p50 for t in plan_running)
    global_work = sum(estimates[t.run_id].p50 for t in global_queue)
    global_running_work = sum(estimates[t.run_id].p50 for t in global_running)
    longest = max([estimates[t.run_id].p50 for t in plan_queued + plan_pending + plan_running] or [0.0])
    active_vms = {t.vm_name for t in global_running if t.vm_name and t.vm_name != "unknown"}
    ahead_work = sum(estimates[t.run_id].p50 for t in global_queue if t.priority > plan.priority)
    max_wait = max([elapsed_minutes(t.queued_at, now) for t in global_queue if t.queued_at] or [0.0])
    missing_plan_history = sum(estimates[t.run_id].sample_count == 0 for t in plan_queued + plan_pending + plan_running)
    features = {
        "plan_elapsed_hours": elapsed_minutes(plan.started_at, now) / 60,
        "plan_total_jobs": float(count),
        "plan_completed_count": float(completed),
        "plan_running_count": float(len(plan_running)),
        "plan_queued_count": float(len(plan_queued)),
        "plan_pending_count": float(len(plan_pending)),
        "plan_completed_fraction": completed / max(1, count),
        "plan_queued_work_hours": queued_work / 60,
        "plan_pending_work_hours": pending_work / 60,
        "plan_running_work_hours": running_work / 60,
        "plan_longest_remaining_hours": longest / 60,
        "global_queue_count": float(len(global_queue)),
        "global_running_count": float(len(global_running)),
        "global_queue_work_hours": global_work / 60,
        "global_running_work_hours": global_running_work / 60,
        "higher_priority_queued_work_hours": ahead_work / 60,
        "global_vm_count": float(len(active_vms)),
        "global_oldest_wait_hours": max_wait / 60,
        "recent_finished_jobs_hour": float(recent_completions),
        "plan_priority": float(plan.priority),
        "plan_active_jobs_without_matching_history": float(missing_plan_history),
    }
    # Initial hand-built baseline; not a scheduler, and not guaranteed calibrated.
    capacity = max(1.0, min(200.0, len(global_running) * 0.30 + recent_completions * 0.15))
    available_plan_parallelism = max(1.0, min(capacity, max(1, len(plan_queued) + len(plan_pending) + len(plan_running))))
    shared_queue_penalty = 0.20 * max(0, global_work - queued_work) / capacity
    baseline_minutes = max(longest, (queued_work + pending_work + running_work) / available_plan_parallelism)
    baseline_minutes += shared_queue_penalty + ahead_work / capacity * 0.3
    baseline_minutes = max(1.0, min(60 * 24 * 30, baseline_minutes))
    return features, baseline_minutes


def progress_phase(features):
    fraction = features["plan_completed_fraction"]
    return "start" if fraction < .25 else "quarter" if fraction < .50 else "half" if fraction < .75 else "late"
