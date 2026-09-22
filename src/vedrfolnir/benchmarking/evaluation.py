"""Chronological, delayed-label forecast evaluation."""
from __future__ import annotations
from collections import defaultdict, deque
from datetime import timedelta
from statistics import median
from types import SimpleNamespace
import heapq
import math
from sqlalchemy import create_engine
from vedrfolnir.db import Base, HistoricalRun
from vedrfolnir.features import DurationEstimator, make_plan_features, progress_phase, quantile
from vedrfolnir.learning import PlanRegressor
from vedrfolnir.adaptive import shadow_promotion

def evaluate_planstart(plans, *, warmup=10, min_training=10, progress=False):
    """Predict at workflow starts and learn after actual completion only."""
    if warmup < 0 or min_training < 0:
        raise ValueError("Invalid warmup or min_training")
    plans = sorted(plans, key=lambda p: (p.started_at, p.plan_id))
    if len({p.plan_id for p in plans}) != len(plans):
        raise ValueError("Duplicate plan_id in benchmark")
    events = []
    for index, plan in enumerate(plans):
        # Finished workflows become training data before same-time starts.
        events.append((plan.started_at, 1, index, plan))
        events.append((plan.finished_at, 0, index, plan))
    events.sort(key=lambda x: (x[0], x[1], x[2]))
    history = defaultdict(list)
    all_history = []
    records = []
    pending_shadow = {}
    completed_shadow = []
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    from sqlalchemy.orm import Session
    try:
        with Session(engine) as session:
            model = PlanRegressor(session)
            for idx, (_, _, order, plan) in enumerate(events):
                if progress and (idx + 1) % max(1, len(events) // 5) == 0:
                    print(f"Evaluation: {100 * (idx + 1) // len(events)}%", flush=True)
                if events[idx][1] == 0:
                    previous = pending_shadow.pop(plan.plan_id, None)
                    if previous is not None:
                        completed_shadow.append({
                            "baseline": previous["baseline_minutes"],
                            "shadow": previous["shadow_minutes"],
                            "actual": (plan.finished_at - plan.started_at).total_seconds() / 60,
                        })
                    history[plan.family].append((plan.task_count,
                        (plan.finished_at - plan.started_at).total_seconds() / 60))
                    all_history.append((plan.task_count,
                        (plan.finished_at - plan.started_at).total_seconds() / 60))
                    # Training is delayed until after the measured completion.
                    model.learn(plan.features, (plan.finished_at - plan.started_at).total_seconds() / 60)
                else:
                    # Median of prior completed family instances, scaled mildly
                    # for relative task count; otherwise use all prior workflows.
                    examples = history[plan.family] or all_history
                    scaled = [minutes * math.sqrt(plan.task_count / size)
                              for size, minutes in examples if size > 0]
                    baseline = max(1.0, median(scaled[-50:])) if scaled else 30.0
                    shadow, ready = model.estimate(plan.features, baseline, min_training)
                    # Only outcomes of *already finished* workflows can
                    # validate a previous point-in-time River prediction.
                    used = bool(ready and shadow_promotion(completed_shadow))
                    prediction = shadow if used else baseline
                    if ready:
                        pending_shadow[plan.plan_id] = {
                            "baseline_minutes": baseline, "shadow_minutes": shadow,
                        }
                    records.append({
                        "plan_id": plan.plan_id,
                        "predicted_at": plan.started_at.isoformat(),
                        "phase": "start", "plan_index": order,
                        "actual_minutes": (plan.finished_at - plan.started_at).total_seconds() / 60,
                        "baseline_minutes": baseline,
                        "predicted_minutes": prediction,
                        "river_used": used,
                        "shadow_minutes": shadow if ready else None,
                        "shadow_ready": ready,
                        "family": plan.family,
                        "task_count": plan.task_count,
                    })
    finally:
        engine.dispose()
    scored = [r for r in records if r["plan_index"] >= warmup]
    shadowed = [r for r in scored if r["shadow_ready"]]
    used = [r for r in scored if r["river_used"]]
    return {"summary": {
        "plans_total": len(plans), "plans_evaluated": len(scored),
        "warmup_plans_excluded": min(warmup, len(plans)),
        "snapshots_generated": len(records),
        "evaluated_at": "workflow_start (one prediction per workflow)",
        "baseline": _stats(scored, "baseline_minutes"),
        "selected_predictor": _stats(scored, "predicted_minutes"),
        "river_only": _stats(shadowed, "shadow_minutes"),
        "baseline_on_river_subset": _stats(shadowed, "baseline_minutes"),
        "river_shadow_ready_plans": len(shadowed),
        "river_used_plans": len(used),
        "selection_policy": "prequential shadow gate: >=40 completed validation builds, >=8% MAE improvement, no P90 regression, latest 20 hold",
    }, "forecasts": records, "scored_plan_ids": [r["plan_id"] for r in scored]}


def _make_job(job, at):
    status = (job.status if job.finished_at <= at else
              "running" if job.started_at <= at else
              "queued" if job.queued_at is not None and job.queued_at <= at else "pending")
    return SimpleNamespace(
        run_id=job.run_id, plan_id=job.plan_id, job_id=job.job_id,
        status=status, started_at=job.started_at if job.started_at <= at else None,
        queued_at=job.queued_at if job.queued_at is not None and job.queued_at <= at else None,
        finished_at=job.finished_at if job.finished_at <= at else None,
        version=job.version, db_type=job.db_type, vm_name=job.vm_name if job.started_at <= at else "unknown",
        priority=job.priority, sched_pool_id=job.sched_pool_id,
    )


def _stats(rows, key):
    errors = [float(p[key]) - p["actual_minutes"] for p in rows]
    if not errors:
        return {"count": 0, "mae_minutes": None, "median_abs_error_minutes": None,
                "p90_abs_error_minutes": None, "bias_minutes": None}
    abs_errors = [abs(x) for x in errors]
    return {"count": len(errors),
            "mae_minutes": round(sum(abs_errors) / len(errors), 3),
            "median_abs_error_minutes": round(quantile(abs_errors, .5), 3),
            "p90_abs_error_minutes": round(quantile(abs_errors, .9), 3),
            "bias_minutes": round(sum(errors) / len(errors), 3)}


def evaluate(plans, *, warmup=20, min_training=30, offsets=(0, 60, 300, 900, 1800, 3600), max_history=500, progress=False):
    """Prequential evaluation; no label is available before its actual finish.

    Models learn once per plan and progress phase when that plan finishes.
    Predictions are scheduled at fixed elapsed offsets, never at percentages of
    an outcome that would leak future duration.
    """
    plans = sorted(plans, key=lambda x: (x.started_at, x.plan_id))
    if len({p.plan_id for p in plans}) != len(plans):
        raise ValueError("Duplicate plan_id in benchmark")
    if warmup < 0 or min_training < 0 or not offsets or min(offsets) < 0:
        raise ValueError("Invalid warmup, min_training, or offsets")
    order = {p.plan_id: i for i, p in enumerate(plans)}
    by_id = {p.plan_id: p for p in plans}
    events = []
    seq = 0
    for p in plans:
        for j in p.jobs:
            heapq.heappush(events, (j.finished_at, 0, seq, "job", j))
            seq += 1
        heapq.heappush(events, (p.finished_at, 1, seq, "finish", p))
        seq += 1
        heapq.heappush(events, (p.started_at, 2, seq, "activate", p))
        seq += 1
        for offset in offsets:
            at = p.started_at + timedelta(seconds=offset)
            if at < p.finished_at:
                heapq.heappush(events, (at, 3, seq, "forecast", p))
                seq += 1

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    from sqlalchemy.orm import Session
    forecasts = []
    pending = {}
    completed_shadow_by_phase = defaultdict(list)
    finished_job_times = deque()
    active_plans = {}
    event_total = len(events)
    event_done = 0
    next_progress = 20
    try:
        with Session(engine) as session:
            regressor = PlanRegressor(session)
            while events:
                at, _, _, kind, obj = heapq.heappop(events)
                event_done += 1
                if progress and event_total and event_done * 100 >= next_progress * event_total:
                    print(f"Evaluation: {next_progress}% ({event_done:,}/{event_total:,} events)", flush=True)
                    next_progress += 20
                if kind == "job":
                    j = obj
                    session.add(HistoricalRun(run_id=j.run_id, plan_id=j.plan_id,
                        job_id=j.job_id, version=j.version, db_type=j.db_type,
                        vm_name=j.vm_name, status=j.status, started_at=j.started_at,
                        finished_at=j.finished_at,
                        duration_seconds=(j.finished_at-j.started_at).total_seconds()))
                    session.flush()
                    finished_job_times.append(at)
                elif kind == "activate":
                    active_plans[obj.plan_id] = obj
                elif kind == "finish":
                    active_plans.pop(obj.plan_id, None)
                    first_per_phase = {}
                    for p in pending.pop(obj.plan_id, ()):
                        first_per_phase.setdefault(p["phase"], p)
                    if obj.status not in ("cancelled", "skipped"):
                        for sample in first_per_phase.values():
                            if sample["shadow_ready"]:
                                completed_shadow_by_phase[sample["phase"]].append({
                                    "baseline": sample["baseline_minutes"],
                                    "shadow": sample["shadow_minutes"],
                                    "actual": sample["actual_minutes"],
                                })
                            regressor.learn(sample["features"], sample["actual_minutes"])
                else:
                    plan = obj
                    all_jobs = [_make_job(j, at) for p in active_plans.values() for j in p.jobs]
                    plan_jobs = [t for t in all_jobs if t.plan_id == plan.plan_id]
                    proxy = SimpleNamespace(plan_id=plan.plan_id, started_at=plan.started_at,
                                            priority=plan.priority)
                    while finished_job_times and finished_job_times[0] <= at-timedelta(hours=1):
                        finished_job_times.popleft()
                    recent = len(finished_job_times)
                    features, baseline = make_plan_features(proxy, plan_jobs, all_jobs,
                        DurationEstimator(session, max_history), at, recent)
                    shadow, ready = regressor.estimate(features, baseline, min_training)
                    phase = progress_phase(features)
                    used_river = bool(ready and shadow_promotion(
                        completed_shadow_by_phase[phase]))
                    prediction = shadow if used_river else baseline
                    actual = (plan.finished_at-at).total_seconds()/60
                    record = {
                        "plan_id": plan.plan_id, "predicted_at": at.isoformat(),
                        "phase": phase, "plan_index": order[plan.plan_id],
                        "actual_minutes": actual, "baseline_minutes": baseline,
                        "predicted_minutes": prediction, "river_used": used_river,
                        "shadow_minutes": shadow if ready else None,
                        "shadow_ready": ready,
                    }
                    forecasts.append(record)
                    pending.setdefault(plan.plan_id, []).append({**record, "features": features})
    finally:
        engine.dispose()

    # Evaluate at the first (submission-time) prediction for each plan. This is
    # the hardest/most useful horizon and avoids overweighting long plans.
    first = {}
    for row in forecasts:
        first.setdefault(row["plan_id"], row)
    rows = [r for r in first.values() if r["plan_index"] >= warmup and
            by_id[r["plan_id"]].status not in ("cancelled", "skipped")]
    used = [r for r in rows if r["river_used"]]
    shadowed = [r for r in rows if r["shadow_ready"]]
    return {
        "summary": {
            "plans_total": len(plans), "plans_evaluated": len(rows),
            "warmup_plans_excluded": min(warmup, len(plans)),
            "snapshots_generated": len(forecasts),
            "evaluated_at": "plan_start (first prediction)",
            "baseline": _stats(rows, "baseline_minutes"),
            "selected_predictor": _stats(rows, "predicted_minutes"),
            "river_only": _stats(shadowed, "shadow_minutes"),
            "baseline_on_river_subset": _stats(shadowed, "baseline_minutes"),
            "river_shadow_ready_plans": len(shadowed),
            "river_used_plans": len(used),
            "selection_policy": "prequential phase-specific shadow gate: >=40 completed validation builds, >=8% MAE improvement, no P90 regression, latest 20 hold",
        },
        "forecasts": forecasts,
        "scored_plan_ids": [r["plan_id"] for r in rows],
    }


