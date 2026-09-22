"""Point-in-time evaluation of the *production* observed scheduler.

This is deliberately separate from the legacy duration-only public evaluator.
Queue admission, VM assignment and completion are visible only when recorded
by their respective event times. Missing task timestamps reject an instance.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from datetime import timedelta
from types import SimpleNamespace
import heapq

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from vedrfolnir.adaptive import (
    ObservedScheduler, ShadowResidualRegressor, load_regime,
    scheduler_bias_correction, shadow_promotion,
)
from vedrfolnir.db import Base, HistoricalRun
from vedrfolnir.features import DurationEstimator, make_plan_features, progress_phase
from vedrfolnir.pooling import PoolCapacityCache
from vedrfolnir.job_forecasting import job_forecast

from .evaluation import _make_job, _stats


def _fallback_counts(rows):
    # One count per reason per plan snapshot; blockers are not exclusive.
    return dict(sorted(Counter(
        reason for row in rows for reason in row["scheduler_fallback_reasons"]
    ).items()))


def _job_metrics(records, warmup, plans):
    first = {}
    for row in records:
        if (row["plan_index"] >= warmup and plans[row["plan_id"]].status not in ("cancelled", "skipped")
                and row.get("outcome_status") not in (None, "cancelled", "skipped")):
            first.setdefault((row["run_id"], row["status"]), row)
    result = {}
    for status in ("queued", "running", "pending"):
        rows = [r for r in first.values() if r["status"] == status]
        eligible = [r for r in rows if r["forecast_available"]]
        def errors(predicted, actual):
            return _stats([{"predicted_minutes": r[predicted], "actual_minutes": r[actual]}
                           for r in eligible], "predicted_minutes")
        result[status] = {
            "jobs_observed": len(rows), "forecast_available_jobs": len(eligible),
            "unavailable_reason_counts": dict(sorted(Counter(
                reason for r in rows for reason in r["unavailable_reasons"]).items())),
            "finish_error": errors("predicted_remaining_minutes", "actual_remaining_minutes"),
        }
        if status == "queued":
            result[status]["wait_error"] = errors("predicted_wait_minutes", "actual_wait_minutes")
    return result


def evaluate_scheduler_replay(
    plans, *, warmup=20, min_training=30,
    offsets=(0, 60, 300, 900, 1800, 3600), max_history=500,
    capacity_refresh_seconds=600, progress=False,
):
    """Forecast each real plan using observable state, label *after* completion.

    Queued timestamps are never fabricated. For task-level WfCommons data with
    only execution timestamps, unstarted tasks remain PENDING and the
    scheduler honestly falls back. No hidden machine placement, task duration,
    future job arrival or future outcome is passed to the predictor.
    """
    plans = sorted(plans, key=lambda x: (x.started_at, x.plan_id))
    if (warmup < 0 or min_training < 0 or not offsets or min(offsets) < 0
            or max_history < 1 or capacity_refresh_seconds <= 0):
        raise ValueError("Invalid replay options")
    if len({p.plan_id for p in plans}) != len(plans):
        raise ValueError("Duplicate plan_id in benchmark")
    order = {p.plan_id: idx for idx, p in enumerate(plans)}
    by_id = {p.plan_id: p for p in plans}
    events = []
    seq = 0
    for plan in plans:
        for job in plan.jobs:
            heapq.heappush(events, (job.finished_at, 0, seq, "job", job))
            seq += 1
        heapq.heappush(events, (plan.finished_at, 1, seq, "finish", plan))
        seq += 1
        heapq.heappush(events, (plan.started_at, 2, seq, "activate", plan))
        seq += 1
        for offset in offsets:
            at = plan.started_at + timedelta(seconds=offset)
            if at < plan.finished_at:
                heapq.heappush(events, (at, 3, seq, "forecast", plan))
                seq += 1
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    cache = PoolCapacityCache(capacity_refresh_seconds)
    active = {}
    finished_job_times = deque()
    forecasts, snapshots, calibration, completed_shadows = [], defaultdict(list), [], []
    job_records, pending_job_labels = [], defaultdict(list)
    completed = 0
    try:
        with Session(engine) as session:
            model = ShadowResidualRegressor(session)
            while events:
                at, _, _, kind, obj = heapq.heappop(events)
                if kind == "job":
                    job = obj
                    for row in pending_job_labels.pop(job.run_id, []):
                        forecast_at = row["predicted_at"]
                        row["actual_wait_minutes"] = max(0.0, (job.started_at - forecast_at).total_seconds() / 60)
                        row["actual_remaining_minutes"] = (job.finished_at - forecast_at).total_seconds() / 60
                        row["outcome_status"] = job.status
                    session.add(HistoricalRun(
                        run_id=job.run_id, plan_id=job.plan_id, job_id=job.job_id,
                        status=job.status, started_at=job.started_at,
                        finished_at=job.finished_at, vm_name=job.vm_name,
                        version=job.version, db_type=job.db_type,
                        sched_pool_id=job.sched_pool_id,
                        duration_seconds=(job.finished_at - job.started_at).total_seconds(),
                    ))
                    session.flush()
                    finished_job_times.append(at)
                elif kind == "activate":
                    active[obj.plan_id] = obj
                elif kind == "finish":
                    active.pop(obj.plan_id, None)
                    if obj.status in ("cancelled", "skipped"):
                        snapshots.pop(obj.plan_id, None)
                        continue
                    first_per_phase = {}
                    for row in snapshots.pop(obj.plan_id, []):
                        first_per_phase.setdefault(row["phase"], row)
                    for row in first_per_phase.values():
                        if row["scheduler_available"]:
                            calibration.append({
                                "phase": row["phase"], "regime": row["regime"],
                                "raw_scheduler": row["scheduler_minutes"],
                                "actual": row["actual_minutes"],
                            })
                            if row["shadow_ready"]:
                                completed_shadows.append({
                                    "phase": row["phase"], "regime": row["regime"],
                                    "baseline": row["calibrated_minutes"],
                                    "shadow": row["shadow_minutes"],
                                    "actual": row["actual_minutes"],
                                })
                            # Same eligibility as the runtime: only learn residual
                            # correction when the queue was fully observable.
                            model.learn(
                                row["features"], row["calibrated_minutes"],
                                row["actual_minutes"],
                            )
                    completed += 1
                    if progress and completed % 25 == 0:
                        print(f"Completed {completed}/{len(plans)} plans", flush=True)
                elif kind == "forecast":
                    plan = obj
                    live = [_make_job(job, at) for p in active.values() for job in p.jobs]
                    own = [j for j in live if j.plan_id == plan.plan_id]
                    proxy = SimpleNamespace(
                        plan_id=plan.plan_id, started_at=plan.started_at,
                        priority=plan.priority,
                    )
                    while finished_job_times and finished_job_times[0] <= at - timedelta(hours=1):
                        finished_job_times.popleft()
                    features, legacy = make_plan_features(
                        proxy, own, live, DurationEstimator(session, max_history),
                        at, len(finished_job_times),
                    )
                    scheduler = ObservedScheduler(
                        session, at, live, max_history, pool_cache=cache,
                    )
                    job_projections = scheduler.project_jobs(live)
                    projected = {run_id: p.finished_at for run_id, p in job_projections.items()}
                    for job in own:
                        if job.status not in ("queued", "running", "pending"):
                            continue
                        job_row = {"run_id": job.run_id, "plan_id": plan.plan_id,
                                   "plan_index": order[plan.plan_id], "status": job.status,
                                   "predicted_at": at,
                                   **job_forecast(scheduler, job, job_projections)}
                        job_records.append(job_row)
                        pending_job_labels[job.run_id].append(job_row)
                    fallback_reasons = scheduler.plan_fallback_reasons(own, projected)
                    available = not fallback_reasons
                    raw = scheduler.estimate_plan(at, own, projected, legacy)
                    if scheduler.pool_aware:
                        features.update(scheduler.pool_features(own))
                    features["observed_vm_count"] = float(scheduler.observed_vm_count)
                    features["observed_vm_slots"] = float(scheduler.observed_slot_count)
                    phase = progress_phase(features)
                    regime = load_regime(features)
                    features["phase"] = phase
                    calibrated = (scheduler_bias_correction(raw, features, calibration)
                                  if available else raw)
                    features["scheduler_baseline_hours"] = calibrated / 60.0
                    shadow, ready = (model.estimate(features, calibrated, min_training)
                                     if available else (calibrated, False))
                    river_used = bool(ready and shadow_promotion(
                        completed_shadows, phase=phase, regime=regime,
                    ))
                    selected = shadow if river_used else calibrated
                    record = {
                        "plan_id": plan.plan_id, "plan_index": order[plan.plan_id],
                        "predicted_at": at.isoformat(), "phase": phase,
                        "regime": regime,
                        "actual_minutes": (plan.finished_at - at).total_seconds() / 60,
                        "baseline_minutes": legacy,
                        "scheduler_minutes": raw,
                        "calibrated_minutes": calibrated,
                        "predicted_minutes": selected,
                        "shadow_minutes": shadow if ready else None,
                        "shadow_ready": ready, "river_used": river_used,
                        "scheduler_available": available,
                        "scheduler_fallback_reasons": fallback_reasons,
                        "prediction_method": (
                            "river_residual_correction" if river_used else
                            "queue_scheduler" if available else "historical_baseline"),
                        "pending_jobs": sum(j.status == "pending" for j in own),
                        "queue_admission_observed": all(
                            job.queued_at is not None for job in plan.jobs),
                        "pool_capacity_source": scheduler.pool_source(own),
                        "features": features,
                    }
                    forecasts.append(record)
                    snapshots[plan.plan_id].append(record)
    finally:
        engine.dispose()

    first = {}
    for record in forecasts:
        first.setdefault(record["plan_id"], record)
    scored = [row for row in first.values()
              if row["plan_index"] >= warmup
              and by_id[row["plan_id"]].status not in ("cancelled", "skipped")]
    eligible = [row for row in scored if row["scheduler_available"]]
    shadowed = [row for row in eligible if row["shadow_ready"]]
    phases = {}
    for phase in ("start", "quarter", "half", "late"):
        first_by_phase = {}
        for row in forecasts:
            if row["phase"] == phase and row["plan_index"] >= warmup:
                first_by_phase.setdefault(row["plan_id"], row)
        rows = [r for r in first_by_phase.values()
                if by_id[r["plan_id"]].status not in ("cancelled", "skipped")]
        if rows:
            phases[phase] = {
                "forecasts": len(rows), "scheduler_available": sum(
                    x["scheduler_available"] for x in rows),
                "scheduler_fallback_reason_counts": _fallback_counts(rows),
                "selected": _stats(rows, "predicted_minutes"),
                "legacy": _stats(rows, "baseline_minutes"),
            }
    complete_queue = sum(all(job.queued_at is not None for job in p.jobs) for p in plans)
    return {
        "summary": {
            "plans_total": len(plans), "plans_evaluated": len(scored),
            "warmup_plans_excluded": min(warmup, len(plans)),
            "snapshots_generated": len(forecasts),
            "evaluated_at": "plan_start (first prediction)",
            "baseline": _stats(scored, "baseline_minutes"),
            "raw_scheduler": _stats(scored, "scheduler_minutes"),
            "calibrated_scheduler": _stats(scored, "calibrated_minutes"),
            "selected_predictor": _stats(scored, "predicted_minutes"),
            "raw_on_eligible_subset": _stats(eligible, "scheduler_minutes"),
            "selected_on_eligible_subset": _stats(eligible, "predicted_minutes"),
            "river_only": _stats(shadowed, "shadow_minutes"),
            "baseline_on_river_subset": _stats(shadowed, "calibrated_minutes"),
            "river_shadow_ready_plans": len(shadowed),
            "river_used_plans": sum(x["river_used"] for x in scored),
            "scheduler_eligible_plans": len(eligible),
            "fallback_plans": len(scored) - len(eligible),
            "scheduler_fallback_reason_counts": _fallback_counts(scored),
            "job_metrics": _job_metrics(job_records, warmup, by_id),
            "job_scoring_policy": "First snapshot per job and observed status; labels only after job completion; no plan ML/P90 transfer",
            "plans_with_complete_queue_admission": complete_queue,
            "queue_admission_coverage": round(
                complete_queue / max(1, len(plans)), 4),
            "queue_observability": (
                "complete" if complete_queue == len(plans) and plans else
                "partial_or_absent; unstarted tasks without queued_at are pending"),
            "phases": phases,
            "capacity_refresh_recalculations": cache.recalculations,
            "selection_policy": (
                "Phase/regime-matched completed forecasts; River shadow gate "
                "requires 40 comparable observations and tail non-regression"),
        },
        "forecasts": forecasts,
        "job_forecasts": [{**r, "predicted_at": r["predicted_at"].isoformat()} for r in job_records],
        "scored_plan_ids": [row["plan_id"] for row in scored],
    }
