from importlib import resources
import asyncio
import hmac
import logging
import csv
import io
import json
from typing import Literal
from contextlib import asynccontextmanager
from datetime import timedelta
from fastapi import FastAPI, Header, HTTPException, Depends, Query
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select, func, desc
from . import __version__
from .db import (
    create_session_factory, make_engine, HistoricalRun, CurrentPlan, CurrentJob,
    Prediction, JobPrediction, ModelCheckpoint, SchedulerPool, SyncState, utcnow
)
from .config import settings
from .service import EngineService
from .demo import seed_demo
from .features import timestamp
from .adaptive import RESIDUAL_MODEL_ID, ObservedScheduler
from .prediction_quality import prediction_evidence
from .job_forecasting import job_forecast, job_projection
from .pooling import pool_of
from .job_monitoring import summarize_jobs, daily_job_errors, job_workload_metrics, SAMPLE_LIMIT
from .metrics import evaluated_plans, summarize, daily_errors, by_workload
from .logging_setup import configure_logging

log = logging.getLogger("vedrfolnir.api")


def to_json(p):
    evidence = prediction_evidence(p.features, river_used=p.used_model,
                                   p90_available=p.p90_minutes is not None)
    return {
        "prediction_method": evidence["method"],
        "scheduler_available": (p.features or {}).get("__scheduler_available"),
        "scheduler_fallback_reasons": (p.features or {}).get("__scheduler_fallback_reasons"),
        "evidence_level": evidence["level"],
        "evidence_warnings": evidence["warnings"],
        "p90_calibration_samples": (p.features or {}).get("__p90_calibration_samples", 0),
        "id": p.id,
        "plan_id": p.plan_id,
        "predicted_at": timestamp(p.predicted_at).isoformat(),
        "predicted_finish_at": (timestamp(p.predicted_at) + timedelta(minutes=p.predicted_minutes)).isoformat(),
        "p90_finish_at": (timestamp(p.predicted_at) + timedelta(minutes=p.p90_minutes)).isoformat() if p.p90_minutes is not None else None,
        "predicted_remaining_minutes": round(p.predicted_minutes, 1),
        "p90_remaining_minutes": round(p.p90_minutes, 1) if p.p90_minutes is not None else None,
        "baseline_minutes": round(p.baseline_minutes, 1),
        "river_used": p.used_model,
        "phase": p.phase,
        "actual_remaining_minutes": round(p.actual_remaining_minutes, 1) if p.actual_remaining_minutes is not None else None,
        "error_minutes": round(p.error_minutes, 1) if p.error_minutes is not None else None,
        "features": p.features,
    }


async def _poll_tick(service):
    """Do not abandon a worker thread when lifespan cancellation arrives."""
    task = asyncio.create_task(asyncio.to_thread(service.tick))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        # shield keeps the poll running; drain it before disposing its engine.
        try:
            await task
        except Exception:
            pass
        raise


async def periodic(service):
    while True:
        try:
            await _poll_tick(service)
        except Exception:
            # The service logs the failure once; the poller must keep running.
            pass
        await asyncio.sleep(settings.poll_seconds)


@asynccontextmanager
async def lifespan(app):
    settings.validate()
    configure_logging(
        level=settings.log_level,
        log_format=settings.log_format,
        config_file=settings.log_config_file,
    )
    log.info("Application starting", extra={"event": "app_start", "source_mode": settings.mode,
                                           "poll_seconds": settings.poll_seconds})
    engine = make_engine()
    factory = create_session_factory(engine)
    service = EngineService(factory)
    if settings.mode == "demo":
        await asyncio.to_thread(seed_demo, factory)
    app.state.service = service
    app.state.Session = factory
    worker = asyncio.create_task(periodic(service))
    try:
        yield
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        engine.dispose()
        log.info("Application stopping", extra={"event": "app_stop"})


app = FastAPI(title="Vedrfolnir: Workload ETA", version=__version__, lifespan=lifespan)


def admin_guard(authorization: str | None = Header(default=None)):
    token = settings.admin_token
    if not token:
        raise HTTPException(503, "ETA_ADMIN_TOKEN is not configured")
    expected = f"Bearer {token}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(401, "Admin bearer token required")


@app.get("/health")
def health():
    return {"status": "up"}


@app.get("/api/status")
def status():
    with app.state.Session() as s:
        checkpoint = s.get(ModelCheckpoint, RESIDUAL_MODEL_ID)
        reconcile_pending = s.get(SyncState, "history_reconcile_cursor") is not None
        return {
            "source_mode": settings.mode,
            "demo_data": settings.mode == "demo",
            "history_count": s.scalar(select(func.count(HistoricalRun.run_id))),
            "active_plan_count": s.scalar(select(func.count(CurrentPlan.plan_id)).where(CurrentPlan.status.in_(["queued", "running"]))),
            "prediction_count": s.scalar(select(func.count(Prediction.id))),
            "river_training_snapshots": checkpoint.seen_count if checkpoint else 0,
            "river_min_training": settings.min_training,
            "river_selection_policy": "completed-build shadow validation; baseline until proven better",
            "data_source_sync": (
                "error" if app.state.service.last_error
                else "not_yet_synced" if not app.state.service.last_success
                else "stale" if (utcnow() - timestamp(app.state.service.last_success))
                > timedelta(seconds=3 * settings.poll_seconds)
                else "recent"
            ),
            "last_success": app.state.service.last_success,
            "last_error": app.state.service.last_error,
            "poll_seconds": settings.poll_seconds,
            "history_reconcile_days": settings.history_reconcile_days,
            "history_reconcile_in_progress": reconcile_pending,
            "history_last_reconciled": app.state.service.last_reconciled,
            "retention_last_pruned": app.state.service.last_retention,
        }


@app.get("/api/pools")
def pools():
    """Current pools, observed lower-bound capacity, and declared limits."""
    with app.state.Session() as s:
        jobs = list(s.scalars(select(CurrentJob)))
        declared = dict(s.execute(select(
            SchedulerPool.sched_pool_id, SchedulerPool.sched_capacity_slots)).all())
        scheduler = ObservedScheduler(
            s, utcnow(), jobs, settings.max_history,
            pool_cache=app.state.service.pool_cache,
            declared_capacities=declared)
        return {
            "capacity_refresh_seconds": app.state.service.pool_cache.refresh_seconds,
            "capacity_last_recalculated_at": (
                app.state.service.pool_cache.refreshed_at.isoformat()
                if app.state.service.pool_cache.refreshed_at else None),
            "pools": [{
                "sched_pool_id": pool,
                "capacity_source": state["source"],
                "capacity_slots": state["capacity_slots"],
                "observed_capacity_slots": state["observed_slots"],
                "declared_capacity_slots": state["declared_slots"],
                "running_jobs": state["running_jobs"],
                "queued_jobs": state["queued_jobs"],
            } for pool, state in sorted(scheduler.pool_snapshots.items())],
            "note": "Observed slots are a lower bound, not the real maximum capacity.",
        }


@app.get("/api/plans")
def plans():
    with app.state.Session() as s:
        active = list(s.scalars(select(CurrentPlan).where(CurrentPlan.status.in_(["queued", "running"]))))
        if not active:
            return []
        ids = [plan.plan_id for plan in active]
        lajob_ids = (select(func.max(Prediction.id).label("id"))
                      .where(Prediction.plan_id.in_(ids))
                      .group_by(Prediction.plan_id).subquery())
        latest = {p.plan_id: p for p in s.scalars(
            select(Prediction).join(lajob_ids, Prediction.id == lajob_ids.c.id))}
        return [{"plan_id": plan.plan_id, "status": plan.status, "priority": plan.priority,
                 "started_at": timestamp(plan.started_at).isoformat(),
                 "prediction": to_json(latest[plan.plan_id]) if plan.plan_id in latest else None}
                for plan in active]


def _job_response(*, run_id=None, plan_id=None, limit=200, offset=0):
    now = utcnow()
    service = app.state.service
    config = service.config
    with app.state.Session() as s:
        # SQLite legacy transaction control doesn't begin a read transaction
        # for SELECT. Keep jobs, capacities and snapshot timestamp coherent.
        if s.bind.dialect.name == "sqlite":
            s.connection().exec_driver_sql("BEGIN")
        all_jobs = list(s.scalars(select(CurrentJob).order_by(CurrentJob.run_id)))
        declared = dict(s.execute(select(
            SchedulerPool.sched_pool_id, SchedulerPool.sched_capacity_slots)).all())
        saved = s.get(SyncState, "live_snapshot_at")
        observed_at = timestamp(saved.value if saved else service.last_success)
        age = (now - observed_at).total_seconds() if observed_at else None
        state = ("error" if service.last_error else "not_yet_synced" if age is None
                 else "stale" if age < 0 or age > 3 * config.poll_seconds else "recent")
        source_reasons = [] if state == "recent" else ["source_" + state]
        # Request-local cache: reading forecasts must not mutate the poller's
        # shared capacity cache or retrain its model.
        scheduler, projections = job_projection(s, now, all_jobs, declared=declared,
                                                max_history=config.max_history)
        unknown_order_pools = scheduler.queue_order_unknown_pools
        affinity_pools = scheduler.pool_affinity_pools
        visible = [j for j in all_jobs if j.run_id == run_id] if run_id is not None else [
            j for j in all_jobs if j.status in ("queued", "running", "pending")
            and (plan_id is None or j.plan_id == plan_id)]
        if run_id is not None and not visible:
            raise HTTPException(404, "Job is not in the current snapshot")
        total = len(visible)
        visible = visible[offset:offset + limit]
        rows = []
        for job in visible:
            prediction = job_forecast(scheduler, job, projections, source_reasons=source_reasons)
            if job.status == "queued" and (pool_of(job) in unknown_order_pools
                                          or (not scheduler.pool_aware and unknown_order_pools)):
                prediction["evidence_warnings"].append("queue_order_partially_unknown")
            if scheduler.pool_aware and job.status == "queued" and pool_of(job) in affinity_pools:
                prediction["evidence_warnings"].append("pool_vm_affinity_not_modeled")
            rows.append({"run_id": job.run_id, "plan_id": job.plan_id, "job_id": job.job_id,
                         "status": job.status, "sched_pool_id": pool_of(job),
                         "queued_at": timestamp(job.queued_at), "started_at": timestamp(job.started_at),
                         "finished_at": timestamp(job.finished_at), "prediction": prediction})
        result = {"generated_at": now, "source_observed_at": observed_at,
                  "source_age_seconds": age, "data_source_sync": state, "source_mode": config.mode,
                  "total_jobs": total,
                  "pool_capacities": [{"sched_pool_id": name, **state}
                                      for name, state in sorted(scheduler.pool_snapshots.items())]}
        result["job" if run_id is not None else "jobs"] = rows[0] if run_id is not None else rows
        return result


@app.get("/api/jobs")
def jobs(plan_id: str | None = None, limit: int = Query(200, ge=1, le=1000),
         offset: int = Query(0, ge=0)):
    """Active job forecasts; filtering never removes competing queue work."""
    return _job_response(plan_id=plan_id, limit=limit, offset=offset)


@app.get("/api/jobs/{run_id:path}")
def job(run_id: str):
    return _job_response(run_id=run_id)


@app.get("/api/plans/{plan_id}/history")
def plan_history(plan_id: str, limit: int = 200):
    with app.state.Session() as s:
        rows = s.scalars(select(Prediction).where(Prediction.plan_id == plan_id)
                         .order_by(desc(Prediction.predicted_at)).limit(min(max(limit, 1), 1000))).all()
        return [to_json(p) for p in rows]


@app.get("/api/metrics")
def metrics():
    with app.state.Session() as s:
        return summarize(evaluated_plans(s), demo=settings.mode == "demo")


@app.get("/api/job-metrics")
def job_metrics():
    with app.state.Session() as s:
        result = summarize_jobs(s, app.state.service.config.job_retention_days)
        result["source_mode"] = app.state.service.config.mode
        return result


@app.get("/api/job-metrics/daily")
def job_daily(days: int = Query(30, ge=1, le=90)):
    with app.state.Session() as s:
        return {"sample_limit_per_status": SAMPLE_LIMIT,
                "rows": daily_job_errors(s, days, utcnow())}


@app.get("/api/job-metrics/by-workload")
def job_workloads(group_by: Literal["pool", "job_type", "load"] = "pool"):
    with app.state.Session() as s:
        return job_workload_metrics(s, group_by)


JOB_EXPORT_COLUMNS = ["id", "run_id", "plan_id", "job_id", "sched_pool_id", "observed_status",
                      "predicted_at", "source_observed_at", "forecast_available", "prediction_method",
                      "predicted_start_at", "predicted_finish_at", "predicted_wait_minutes",
                      "predicted_remaining_minutes", "unavailable_reasons", "evidence_warnings",
                      "capacity_evidence", "actual_started_at", "actual_finished_at", "outcome_status",
                      "wait_error_minutes", "finish_error_minutes", "outcome_issue"]


def stored_job_json(row):
    result = {key: getattr(row, key) for key in JOB_EXPORT_COLUMNS}
    for key in ("predicted_at", "source_observed_at", "predicted_start_at", "predicted_finish_at",
                "actual_started_at", "actual_finished_at"):
        result[key] = timestamp(result[key]).isoformat() if result[key] else None
    return result


def stored_job_rows(session, run_id, limit, completed_only=False):
    stmt = select(JobPrediction)
    if run_id is not None:
        stmt = stmt.where(JobPrediction.run_id == run_id)
    if completed_only:
        stmt = stmt.where(JobPrediction.outcome_status.is_not(None))
    order = JobPrediction.actual_finished_at if completed_only else JobPrediction.predicted_at
    return list(session.scalars(stmt.order_by(order.desc(), JobPrediction.id.desc()).limit(limit)))


@app.get("/api/job-forecasts")
def job_history(run_id: str | None = None, limit: int = Query(200, ge=1, le=1000), completed_only: bool = False):
    with app.state.Session() as s:
        return [stored_job_json(row) for row in stored_job_rows(s, run_id, limit, completed_only)]


@app.get("/api/job-forecasts/export")
def job_export(run_id: str | None = None, limit: int = Query(10000, ge=1, le=10000)):
    with app.state.Session() as s:
        rows = [stored_job_json(row) for row in stored_job_rows(s, run_id, limit)]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=JOB_EXPORT_COLUMNS)
    writer.writeheader()
    for row in rows:
        for key, value in row.items():
            if isinstance(value, (list, dict)):
                row[key] = json.dumps(value)
            elif isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
                row[key] = "'" + value
        writer.writerow(row)
    return Response(output.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="vedrfolnir-job-forecasts.csv"'})


@app.get("/api/metrics/by-workload")
def workload_metrics():
    with app.state.Session() as s:
        return by_workload(evaluated_plans(s))


@app.get("/api/metrics/daily")
def metric_trends(days: int = Query(30, ge=1, le=90)):
    with app.state.Session() as s:
        return daily_errors(evaluated_plans(s), days=days)


@app.post("/api/admin/sync", dependencies=[Depends(admin_guard)])
async def sync_now():
    return await asyncio.to_thread(app.state.service.tick)


@app.get("/", response_class=HTMLResponse)
def home():
    return HTML_PAGE


HTML_PAGE = (resources.files("vedrfolnir") / "static" / "dashboard.html").read_text(encoding="utf-8")
