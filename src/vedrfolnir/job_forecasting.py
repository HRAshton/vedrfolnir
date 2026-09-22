"""Individual job ETA, independent of a whole workflow's runnable status."""
from .features import TERMINAL, timestamp
from .pooling import pool_of
from .adaptive import ObservedScheduler


def job_projection(session, now, jobs, *, declared=None, max_history=500):
    visible = [j for j in jobs if not (
        (j.status == "queued" and j.queued_at and timestamp(j.queued_at) > now)
        or (j.status == "running" and j.started_at and timestamp(j.started_at) > now))]
    scheduler = ObservedScheduler(session, now, visible, max_history,
                                  declared_capacities=declared)
    return scheduler, scheduler.project_jobs(visible)


def job_forecast(scheduler, job, projections, *, source_reasons=()):
    """Use a measured running start or an admitted, observable queue projection.

    No plan residual correction or plan P90 is transferable to this target.
    A pending sibling is not a prerequisite of a job already marked queued.
    """
    now = scheduler.now
    reasons = list(source_reasons)
    if job.status == "pending":
        reasons.append("pending_job")
    elif job.status in TERMINAL:
        reasons.append("job_terminal")
    elif job.status not in ("queued", "running"):
        reasons.append("unsupported_job_status")
    if job.status == "queued":
        admission = timestamp(job.queued_at)
        if admission is None:
            reasons.append("queue_admission_unknown")
        elif admission > now:
            reasons.append("queue_admission_in_future")
        if admission is not None and (pool_of(job) in scheduler.queue_order_unknown_pools
                                     or (not scheduler.pool_aware and scheduler.queue_order_unknown_pools)):
            reasons.append("queue_order_unknown")
        if scheduler.pool_aware:
            cap = scheduler.pool_snapshots.get(pool_of(job), {}).get("capacity_slots")
            if cap is None:
                reasons.append("pool_capacity_unknown")
            elif cap == 0:
                reasons.append("pool_paused")
            if pool_of(job) in scheduler.pool_affinity_pools:
                reasons.append("pool_vm_affinity_unsupported")
        elif scheduler.observed_vm_count == 0:
            reasons.append("vm_inventory_unobserved")
    elif job.status == "running":
        start = timestamp(job.started_at)
        if start is None:
            reasons.append("running_start_unknown")
        elif start > now:
            reasons.append("job_start_in_future")
    projected = projections.get(job.run_id)
    if job.status in ("queued", "running") and projected is None and not reasons:
        reasons.append("projection_incomplete")
    available = not reasons
    source = scheduler.pool_source([job])
    warnings = ["future_job_arrivals_not_modeled", "job_p90_not_calibrated"]
    if source in ("observed", "legacy_vm"):
        warnings.append("capacity_inferred_not_declared")
    if scheduler.duration.remaining(job, now).sample_count == 0:
        warnings.append("job_duration_history_scarce")
    return {
        "forecast_available": available,
        "unavailable_reasons": reasons,
        "prediction_method": ("queue_scheduler" if job.status == "queued" else
                              "historical_duration") if available else None,
        "predicted_start_at": projected.started_at.isoformat() if available else None,
        "start_time_source": ("projected" if job.status == "queued" else "observed") if available else None,
        "predicted_finish_at": projected.finished_at.isoformat() if available else None,
        "predicted_wait_minutes": max(0.0, (projected.started_at - now).total_seconds() / 60)
            if available else None,
        "predicted_remaining_minutes": max(0.0, (projected.finished_at - now).total_seconds() / 60)
            if available else None,
        "capacity_source": source,
        "evidence_warnings": warnings,
        "p90_finish_at": None,
        "river_used": False,
    }
