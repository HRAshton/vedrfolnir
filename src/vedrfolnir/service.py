"""Polling, snapshot forecasts, delayed labels and idempotent training."""
from datetime import datetime, timedelta, timezone
import logging
import time
from threading import Lock
from sqlalchemy import delete, func, select
from .db import (
    HistoricalRun, CurrentJob, CurrentPlan, SchedulerPool, Prediction, SyncState,
    TrainingMarker, utcnow,
)
from .config import settings
from .features import DurationEstimator, make_plan_features, progress_phase, timestamp, TERMINAL
from .adaptive import (
    ObservedScheduler, ShadowResidualRegressor, RESIDUAL_MODEL_ID,
    load_regime, scheduler_bias_correction, shadow_promotion,
)
from .source import MSSQLSource, CSVSource
from .pooling import PoolCapacityCache, pool_of, declared_capacities
from .source_validation import normalize_status, require_valid, require_history_page
from .uncertainty import upper_estimate
from .job_monitoring import record_and_match
from .retention import prune_storage

log = logging.getLogger("vedrfolnir.service")

class EngineService:
    def __init__(self, session_factory, config=settings):
        self.Session = session_factory
        self.config = config
        self.lock = Lock()
        self.last_error = None
        self.last_success = None
        self.last_reconciled = 0
        self.last_retention = {"historical_runs": 0, "plan_predictions": 0}
        self.pool_cache = PoolCapacityCache(config.pool_capacity_refresh_seconds)
        # Never mix synthetic and real data in the same storage volume.
        with self.Session.begin() as s:
            mode = s.get(SyncState, "source_mode")
            if mode is None:
                s.add(SyncState(key="source_mode", value=self.config.mode))
            elif mode.value != self.config.mode:
                raise RuntimeError("Storage belongs to SOURCE_MODE=" + mode.value + "; use a fresh SQLite data volume")

    def _cursor(self, s, key="history_cursor"):
        value = s.get(SyncState, key)
        if value:
            stamp, run_id = value.value.split("|", 1)
            return datetime.fromisoformat(stamp), run_id
        return datetime(2000, 1, 1, tzinfo=timezone.utc), ""

    def _store_history_page(self, rows, cursor, limit, *, key, until=None, complete=False):
        """Validate then commit data and its corresponding cursor in one transaction."""
        require_history_page(rows, cursor, limit, utcnow())
        eligible = [row for row in rows if until is None
                    or timestamp(row["finished_at"]) <= until]
        payload = []
        for row in eligible:
            end = timestamp(row["finished_at"])
            start = timestamp(row["started_at"])
            payload.append({
                "run_id": str(row["run_id"]),
                "plan_id": str(row["plan_id"]) if row.get("plan_id") is not None else None,
                "job_id": str(row["job_id"]),
                "status": normalize_status(row["status"]),
                "started_at": start,
                "finished_at": end,
                "duration_seconds": (end - start).total_seconds(),
                "version": str(row.get("version") or "unknown"),
                "db_type": str(row.get("db_type") or "unknown"),
                "vm_name": str(row.get("vm_name") or "unknown"),
                "sched_pool_id": pool_of(row),
            })
        last = rows[-1]
        stamp = timestamp(last["finished_at"])
        identifier = str(last["run_id"])
        with self.Session.begin() as s:
            if s.bind.dialect.name != "sqlite":
                raise ValueError("This minimal PoC stores state in SQLite")
            added = 0
            if payload:
                from sqlalchemy.dialects.sqlite import insert
                stmt = insert(HistoricalRun).values(payload).on_conflict_do_nothing(
                    index_elements=["run_id"])
                added = s.execute(stmt).rowcount or 0
            if key == "history_cursor":
                s.merge(SyncState(key=key, value=f"{stamp.isoformat()}|{identifier}"))
            elif complete:
                s.execute(delete(SyncState).where(SyncState.key.in_(
                    ["history_reconcile_cursor", "history_reconcile_until"])))
            else:
                s.merge(SyncState(key="history_reconcile_cursor",
                                  value=f"{stamp.isoformat()}|{identifier}"))
                s.merge(SyncState(key="history_reconcile_until", value=until.isoformat()))
        return added

    def import_history(self, source):
        """Forward cursor never rewinds; its per-poll page budget is independent."""
        remaining = self.config.import_limit_per_poll
        imported = 0
        while remaining > 0:
            with self.Session() as s:
                dt, rid = self._cursor(s)
            page_limit = min(remaining, self.config.import_batch)
            rows = source.historical(dt.replace(tzinfo=None), rid, page_limit)
            if not rows:
                break
            imported += self._store_history_page(
                rows, (dt, rid), page_limit, key="history_cursor")
            remaining -= len(rows)
            if len(rows) < page_limit:
                break
        return imported

    def reconcile_history(self, source):
        """Revisit recent completion keys with a resumable, separately budgeted scan.

        Each scan has a fixed upper boundary. When exhausted, the next poll
        starts a new overlapping scan, recovering records inserted behind the
        forward cursor as long as they remain inside the configured lookback.
        """
        if not self.config.history_reconcile_days:
            return 0
        now = utcnow()
        with self.Session() as s:
            cursor_state = s.get(SyncState, "history_reconcile_cursor")
            until_state = s.get(SyncState, "history_reconcile_until")
            if cursor_state and until_state:
                cursor = self._cursor(s, "history_reconcile_cursor")
                until = timestamp(until_state.value)
            else:
                cursor = (now - timedelta(days=self.config.history_reconcile_days), "")
                until = now
        remaining = self.config.history_reconcile_limit_per_poll
        added = 0
        while remaining > 0:
            page_limit = min(remaining, self.config.import_batch)
            rows = source.historical(cursor[0].replace(tzinfo=None),
                                     cursor[1], page_limit)
            if not rows:
                # Reset only the reconciliation checkpoint; preserve forward progress.
                with self.Session.begin() as s:
                    s.execute(delete(SyncState).where(SyncState.key.in_(
                        ["history_reconcile_cursor", "history_reconcile_until"])))
                break
            # Stop at the scan boundary, even if a page straddles it.
            last = (timestamp(rows[-1]["finished_at"]), str(rows[-1]["run_id"]))
            complete = len(rows) < page_limit or last[0] >= until
            added += self._store_history_page(
                rows, cursor, page_limit, key="history_reconcile_cursor",
                until=until, complete=complete)
            remaining -= len(rows)
            if complete:
                break
            cursor = last
        return added

    def update_live(self, plans, jobs, pools=None):
        require_valid("plans", plans)
        require_valid("live_jobs", jobs)
        declared = declared_capacities(pools or [], jobs)
        now = utcnow()
        with self.Session.begin() as s:
            # Always swap the two snapshots together; avoid partial/inconsistent live state.
            s.execute(delete(CurrentJob))
            s.execute(delete(CurrentPlan))
            s.execute(delete(SchedulerPool))
            s.merge(SyncState(key="live_snapshot_at", value=now.isoformat()))
            for name, slots in declared.items():
                s.add(SchedulerPool(sched_pool_id=name, sched_capacity_slots=slots,
                                    seen_at=now))
            for r in plans:
                start = timestamp(r.get("started_at") or r.get("created_at"))
                if not start:
                    continue
                s.add(CurrentPlan(
                    plan_id=str(r["plan_id"]), status=normalize_status(r["status"]),
                    started_at=start, finished_at=timestamp(r.get("finished_at")),
                    priority=int(r.get("priority") or 0), seen_at=now,
                ))
            for r in jobs:
                s.add(CurrentJob(
                    run_id=str(r["run_id"]),
                    plan_id=str(r["plan_id"]) if r.get("plan_id") is not None else None,
                    job_id=str(r["job_id"]), status=normalize_status(r["status"]),
                    started_at=timestamp(r.get("started_at")),
                    finished_at=timestamp(r.get("finished_at")),
                    queued_at=timestamp(r.get("queued_at")),
                    version=str(r.get("version") or "unknown"),
                    db_type=str(r.get("db_type") or "unknown"),
                    vm_name=str(r.get("vm_name") or "unknown"),
                    sched_pool_id=pool_of(r),
                    priority=int(r.get("priority") or 0),
                ))

    def finish_plans(self):
        now = utcnow()
        with self.Session.begin() as s:
            regressor = ShadowResidualRegressor(s)
            finished = list(s.scalars(select(CurrentPlan).where(
                CurrentPlan.finished_at.is_not(None), CurrentPlan.finished_at <= now,
                CurrentPlan.status.in_(list(TERMINAL))
            )))
            learned = 0
            for plan in finished:
                predictions = list(s.scalars(select(Prediction).where(
                    Prediction.plan_id == plan.plan_id, Prediction.actual_finished_at.is_(None)
                ).order_by(Prediction.predicted_at)))
                # Store error on all predictions; train at most once per plan & phase.
                for p in predictions:
                    remaining = (timestamp(plan.finished_at) - timestamp(p.predicted_at)).total_seconds() / 60
                    if remaining < 0:
                        continue
                    p.actual_finished_at = plan.finished_at
                    p.actual_remaining_minutes = remaining
                    p.error_minutes = p.predicted_minutes - remaining
                    p.outcome_status = plan.status
                first_per_phase = {}
                for p in predictions:
                    if p.actual_remaining_minutes is not None and p.phase not in first_per_phase:
                        first_per_phase[p.phase] = p
                for phase, p in first_per_phase.items():
                    if plan.status in ("cancelled", "skipped"):
                        continue
                    if s.get(TrainingMarker, (plan.plan_id, phase)) is not None:
                        continue
                    if p.features.get("__scheduler_v1"):
                        regressor.learn(p.features, p.baseline_minutes,
                                        p.actual_remaining_minutes)
                    s.add(TrainingMarker(plan_id=plan.plan_id, phase=phase, prediction_id=p.id))
                    learned += 1
            if learned:
                regressor.save(s)
            return learned

    def forecast_active(self):
        now = utcnow()
        created = 0
        with self.Session.begin() as s:
            regressor = ShadowResidualRegressor(s)
            plans = list(s.scalars(select(CurrentPlan).where(
                CurrentPlan.status.not_in(list(TERMINAL))
            )))
            jobs = list(s.scalars(select(CurrentJob)))
            dur = DurationEstimator(s, self.config.max_history, finished_by=now)
            cutoff = now - timedelta(hours=1)
            recently_finished = s.scalar(select(func.count(HistoricalRun.run_id)).where(
                HistoricalRun.finished_at >= cutoff, HistoricalRun.finished_at <= now
            )) or 0
            # Evaluate only labeled shadow forecasts produced before completion.
            # At most one eligible point-in-time prediction from each build.
            labeled = list(s.scalars(select(Prediction).where(
                Prediction.actual_remaining_minutes.is_not(None),
                Prediction.actual_finished_at <= now,
                Prediction.outcome_status.not_in(["cancelled", "skipped"]),
            ).order_by(Prediction.id.desc()).limit(5000)))
            ordered_labeled = list(reversed(labeled))
            historical_shadows = {}
            completed_scheduler = {}
            for previous in ordered_labeled:
                features_at_prediction = previous.features or {}
                phase = previous.phase
                regime = features_at_prediction.get("__pressure_regime",
                                                      load_regime(features_at_prediction))
                cohort_key = (previous.plan_id, phase, regime)
                actual = previous.actual_remaining_minutes
                raw = features_at_prediction.get("__raw_scheduler_minutes")
                if (features_at_prediction.get("__scheduler_v1")
                        and isinstance(raw, (float, int)) and actual is not None):
                    completed_scheduler.setdefault(cohort_key, {
                        "phase": phase, "regime": regime,
                        "raw_scheduler": float(raw), "actual": actual,
                    })
                shadow = features_at_prediction.get("__shadow_prediction_minutes")
                if (features_at_prediction.get("__shadow_ready")
                        and isinstance(shadow, (float, int)) and actual is not None):
                    historical_shadows.setdefault(cohort_key, {
                        "phase": phase, "regime": regime,
                        "baseline": previous.baseline_minutes,
                        "shadow": float(shadow), "actual": actual,
                    })
            shadow_comparisons = list(historical_shadows.values())
            scheduler_observations = list(completed_scheduler.values())
            promoted = {}
            declared = dict(s.execute(
                select(SchedulerPool.sched_pool_id, SchedulerPool.sched_capacity_slots)
            ).all())
            scheduler = ObservedScheduler(
                s, now, jobs, self.config.max_history,
                pool_cache=self.pool_cache, declared_capacities=declared)
            projected = scheduler.project(jobs)
            latest = dict(s.execute(
                select(Prediction.plan_id, func.max(Prediction.predicted_at))
                .where(Prediction.plan_id.in_([x.plan_id for x in plans]))
                .group_by(Prediction.plan_id)
            ).all()) if plans else {}
            for plan in plans:
                previous_time = latest.get(plan.plan_id)
                if previous_time and (now - timestamp(previous_time)).total_seconds() < self.config.prediction_interval_seconds:
                    continue
                features, baseline = make_plan_features(plan, jobs, jobs, dur, now, recently_finished)
                if features["plan_total_jobs"] == 0:
                    continue
                plan_jobs = [j for j in jobs if j.plan_id == plan.plan_id]
                fallback_reasons = scheduler.plan_fallback_reasons(plan_jobs, projected)
                scheduler_available = not fallback_reasons
                raw_scheduler = scheduler.estimate_plan(
                    now, plan_jobs, projected, baseline)
                if scheduler.pool_aware:
                    features.update(scheduler.pool_features(plan_jobs))
                features["observed_vm_count"] = float(scheduler.observed_vm_count)
                features["observed_vm_slots"] = float(scheduler.observed_slot_count)
                phase = progress_phase(features)
                regime = load_regime(features)
                # Do not re-calibrate or promote ML corrections when the
                # source cannot establish a runnable observed VM queue.
                baseline = (scheduler_bias_correction(
                    raw_scheduler, features, scheduler_observations)
                    if scheduler_available else raw_scheduler)
                features["scheduler_baseline_hours"] = baseline / 60.0
                shadow, shadow_ready = (
                    regressor.estimate(features, baseline, self.config.min_training)
                    if scheduler_available else (baseline, False))
                group = (phase, regime)
                if group not in promoted:
                    promoted[group] = shadow_promotion(
                        shadow_comparisons, phase=phase, regime=regime)
                ml_used = bool(promoted[group] and shadow_ready)
                minutes = shadow if ml_used else baseline
                # Persist point-in-time shadow estimates only for delayed
                # validation. These keys are excluded from ML training.
                features["__prediction_method"] = (
                    "queue_scheduler" if scheduler_available else "historical_baseline")
                features["__scheduler_available"] = scheduler_available
                features["__scheduler_fallback_reasons"] = fallback_reasons
                features["__pool_capacity_source"] = scheduler.pool_source(plan_jobs)
                features["__pool_ids"] = sorted({pool_of(job) for job in plan_jobs})
                features["__raw_scheduler_minutes"] = raw_scheduler
                features["__pressure_regime"] = regime
                features["__scheduler_v1"] = True
                features["__shadow_ready"] = bool(shadow_ready)
                features["__shadow_prediction_minutes"] = shadow if shadow_ready else None
                p90, calibration_samples = upper_estimate(
                    minutes, features=features, phase=phase, river_used=ml_used,
                    completed=ordered_labeled)
                features["__p90_calibration_samples"] = calibration_samples
                features["__p90_cohort"] = "phase_regime_method"
                s.add(Prediction(
                    plan_id=plan.plan_id, predicted_at=now, phase=progress_phase(features),
                    model_version=RESIDUAL_MODEL_ID, used_model=ml_used,
                    baseline_minutes=baseline, predicted_minutes=minutes,
                    p90_minutes=p90, features=features,
                ))
                created += 1
        return created

    def tick(self):
        if not self.lock.acquire(blocking=False):
            log.debug("Sync skipped because one is running", extra={"event": "sync_skipped"})
            return {"status": "already_running"}
        started = time.monotonic()
        try:
            imported = 0
            reconciled = 0
            if self.config.mode in ("mssql", "files"):
                source = MSSQLSource(self.config.query_dir) if self.config.mode == "mssql" else CSVSource(self.config.csv_dir)
                imported = self.import_history(source)
                reconciled = self.reconcile_history(source)
                with self.Session() as s:
                    earliest = s.scalar(select(func.min(Prediction.predicted_at)).where(
                        Prediction.actual_finished_at.is_(None)))
                since = timestamp(earliest) - timedelta(days=1) if earliest else utcnow() - timedelta(days=60)
                plans, jobs = source.live(since.replace(tzinfo=None))
                pools = source.pools() if hasattr(source, "pools") else []
                self.update_live(plans, jobs, pools=pools)
            elif self.config.mode != "demo":
                raise ValueError("SOURCE_MODE must be demo, files, or mssql")
            learned = self.finish_plans()
            forecasts = self.forecast_active()
            with self.Session.begin() as s:
                job_observations = record_and_match(s, self.config, utcnow())
                retained = prune_storage(
                    s, self.config, utcnow(),
                    imported_history=imported + reconciled)
            self.last_reconciled = reconciled
            self.last_retention = retained
            self.last_error = None
            self.last_success = utcnow().isoformat()
            log.info("Synchronization complete", extra={
                "event": "sync_complete", "source_mode": self.config.mode,
                "imported": imported, "reconciled": reconciled,
                "retention_pruned": retained, "trained_snapshots": learned,
                "new_predictions": forecasts,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
            })
            return {"status": "ok", "imported": imported, "reconciled": reconciled,
                    "retention_pruned": retained, "trained_snapshots": learned,
                    "new_predictions": forecasts, "job_observations": job_observations}
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            # Avoid dumping SQL, feature snapshots or connection secrets into logs.
            log.error("Synchronization failed", extra={
                "event": "sync_failed", "exception_type": type(exc).__name__,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
            })
            raise
        finally:
            self.lock.release()
