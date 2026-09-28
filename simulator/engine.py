"""Fast, deterministic virtual-clock benchmark for build completion ETA.

The simulator alone knows VM speed factors and realized job durations.
Vedrfolnir receives only timestamped SQL state and completed outcomes.
"""
from __future__ import annotations

import csv
import heapq
import json
import math
import random
from time import perf_counter
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import (
    Column, DateTime, Integer, MetaData, String, Table, create_engine,
    delete, func, insert, select, update,
)
from sqlalchemy.orm import Session

from vedrfolnir.db import Base, HistoricalRun
from vedrfolnir.features import (
    DurationEstimator, Estimate, make_plan_features, progress_phase, quantile,
)
from vedrfolnir.adaptive import (
    ObservedScheduler, ShadowResidualRegressor, load_regime,
    scheduler_bias_correction, shadow_promotion,
)
from vedrfolnir.pooling import PoolCapacityCache

from .scheduling import ZERO, generate_schedule, make_vm_fleet, normalize_vms

metadata = MetaData()
build_table = Table(
    "sim_builds", metadata,
    Column("plan_id", String, primary_key=True),
    Column("priority", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("status", String, nullable=False),
    Column("updated_seq", Integer, nullable=False),
)
job_table = Table(
    "sim_jobs", metadata,
    Column("run_id", String, primary_key=True),
    Column("plan_id", String, nullable=False),
    Column("job_id", String, nullable=False),
    Column("vm_name", String, nullable=True),
    Column("sched_pool_id", String, nullable=False, default="general"),
    Column("priority", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("queued_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("status", String, nullable=False),
    Column("updated_seq", Integer, nullable=False),
)


def as_utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def events_for(builds, *, interval_seconds, predict):
    events = []
    for build in builds:
        events.append((build.created_at, 2, "build_arrive", build))
        events.append((build.finished_at, 1, "build_finish", build))
        for job in build.jobs:
            events.append((job.started_at, 3, "job_start", job))
            events.append((job.finished_at, 0, "job_finish", job))
    if predict and interval_seconds > 0:
        at = min(b.created_at for b in builds) + timedelta(seconds=interval_seconds)
        end = max(b.finished_at for b in builds)
        while at < end:
            events.append((at, 4, "tick", None))
            at += timedelta(seconds=interval_seconds)
    events.sort(key=lambda e: (e[0], e[1]))
    return events


def summarize(values):
    if not values:
        return {"count": 0, "mae_minutes": None, "p90_abs_error_minutes": None}
    errors = [abs(float(x)) for x in values]
    return {"count": len(errors), "mae_minutes": round(sum(errors) / len(errors), 3),
            "p90_abs_error_minutes": round(quantile(errors, 0.9), 3)}


class Simulator:
    """One in-memory SQLite DB for event state and Vedrfolnir's real ML classes."""

    def __init__(self, *, seed=42, history_builds=100, builds=200,
                 arrival_seconds=240.0, min_jobs=2, max_jobs=6,
                 vms=None, interval_seconds=600, reconstruct_historical_queue=False,
                 min_training=30, servers=1, vms_per_server=30,
                 min_slots=1, max_slots=3, contention=0.15, burst_size=0,
                 history_contention=None, history_vms=None,
                 declared_pool_capacity=False, capacity_refresh_seconds=600):
        self.seed = seed
        self.history_builds = history_builds
        self.build_count = builds
        self.arrival_seconds = arrival_seconds
        self.min_jobs = min_jobs
        self.max_jobs = max_jobs
        self.vms = normalize_vms(vms if vms is not None else make_vm_fleet(
            seed=seed, servers=servers, vms_per_server=vms_per_server,
            min_slots=min_slots, max_slots=max_slots))
        if not math.isfinite(contention) or contention < 0 or burst_size < 0:
            raise ValueError("Contention and burst size must be nonnegative")
        self.contention = contention
        self.history_contention = (contention if history_contention is None
                                   else history_contention)
        if not math.isfinite(self.history_contention) or self.history_contention < 0:
            raise ValueError("Historical contention must be finite and nonnegative")
        self.history_vms = normalize_vms(history_vms) if history_vms is not None else self.vms
        self.declared_pool_capacity = declared_pool_capacity
        self.pool_cache = PoolCapacityCache(capacity_refresh_seconds)
        self.burst_size = burst_size
        self.observed_slots = defaultdict(lambda: 1)
        self.live_metrics = {}
        self.shadow_comparisons = []
        self.scheduler_observations = []
        self.shadow_ready_count = 0
        self.shadow_promoted_count = 0
        self.interval_seconds = interval_seconds
        self.reconstruct = reconstruct_historical_queue
        self.min_training = min_training
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        metadata.create_all(self.engine)
        self.records = []
        self.predictions = defaultdict(list)
        self.learned_snapshots = 0
        self.seq = 0

    def close(self):
        self.engine.dispose()

    def _next_seq(self):
        self.seq += 1
        return self.seq

    def _read_jobs(self, session):
        active = {row.plan_id: row for row in session.execute(
            select(build_table).where(build_table.c.status != "passed")
        ).mappings()}
        if not active:
            return {}, []
        all_jobs = []
        for row in session.execute(select(job_table)).mappings():
            if row["plan_id"] not in active:
                continue
            all_jobs.append(SimpleNamespace(
                run_id=row["run_id"], plan_id=row["plan_id"], job_id=row["job_id"],
                vm_name=row["vm_name"], sched_pool_id=row["sched_pool_id"],
                status=row["status"], priority=row["priority"],
                queued_at=as_utc(row["queued_at"]),
                started_at=as_utc(row["started_at"]),
                finished_at=as_utc(row["finished_at"]),
                created_at=as_utc(row["created_at"]),
                version="unknown", db_type="unknown",
            ))
        plans = {key: SimpleNamespace(
            plan_id=key, started_at=as_utc(row.created_at), priority=row.priority)
            for key, row in active.items()}
        return plans, all_jobs

    def _forecast(self, session, now, *, only=None):
        plans, jobs = self._read_jobs(session)
        if not plans:
            return
        estimator = DurationEstimator(session, max_samples=200)
        recently_finished = session.scalar(select(func.count(HistoricalRun.run_id)).where(
            HistoricalRun.finished_at >= now - timedelta(hours=1),
            HistoricalRun.finished_at <= now,
        )) or 0
        declared = None
        if self.declared_pool_capacity:
            declared = {}
            for config in self.vms.values():
                declared[config.pool_id] = declared.get(config.pool_id, 0) + config.slots
        scheduler = ObservedScheduler(
            session, now, jobs, max_history=200,
            pool_cache=self.pool_cache, declared_capacities=declared)
        model = ShadowResidualRegressor(session)
        projected = scheduler.project(jobs)
        promotions = {}
        per_plan = defaultdict(list)
        for j in jobs:
            per_plan[j.plan_id].append(j)
        for plan_id, plan in plans.items():
            if only is not None and plan_id not in only:
                continue
            features, legacy = make_plan_features(plan, jobs, jobs, estimator, now,
                                                   recently_finished)
            features = dict(features)
            scheduler_available = scheduler.can_estimate_plan(
                per_plan[plan_id], projected)
            scheduler_minutes = scheduler.estimate_plan(
                now, per_plan[plan_id], projected, legacy)
            if scheduler.pool_aware:
                features.update(scheduler.pool_features(per_plan[plan_id]))
            features["observed_vm_count"] = float(scheduler.observed_vm_count)
            features["observed_vm_slots"] = float(scheduler.observed_slot_count)
            phase = progress_phase(features)
            regime = load_regime(features)
            calibrated = (scheduler_bias_correction(
                scheduler_minutes, features, self.scheduler_observations)
                if scheduler_available else scheduler_minutes)
            features["scheduler_baseline_hours"] = calibrated / 60.0
            shadow, shadow_ready = (model.estimate(
                features, calibrated, self.min_training)
                if scheduler_available else (calibrated, False))
            group = (phase, regime)
            if group not in promotions:
                promotions[group] = shadow_promotion(
                    self.shadow_comparisons, phase=phase, regime=regime)
            ml_used = bool(shadow_ready and promotions[group])
            estimate = shadow if ml_used else calibrated
            features["__pool_capacity_source"] = scheduler.pool_source(
                per_plan[plan_id])
            if plan_id.startswith("live-"):
                self.shadow_ready_count += int(shadow_ready)
                self.shadow_promoted_count += int(ml_used)
            self.predictions[plan_id].append({
                "plan_id": plan_id, "predicted_at": now,
                "phase": phase, "regime": regime, "features": features,
                "pool_capacity_source": features["__pool_capacity_source"],
                "pool_capacity_slots": features.get("pool_capacity_slots"),
                "legacy_minutes": legacy, "scheduler_minutes": scheduler_minutes,
                "calibrated_minutes": calibrated,
                "shadow_minutes": shadow if shadow_ready else None,
                "shadow_ready": shadow_ready,
                "predicted_minutes": estimate, "river_used": ml_used,
            })

    def _learn_finished(self, session, plan_ids, finished):
        model = ShadowResidualRegressor(session)
        for plan_id in plan_ids:
            snapshots = self.predictions.pop(plan_id, [])
            # Only first completed forecast per build/phase/load group is
            # eligible for prospective calibration or River gate selection.
            groups = {}
            for snap in snapshots:
                groups.setdefault((snap["phase"], snap["regime"]), snap)
            shadow_groups = {}
            for snap in snapshots:
                if snap["shadow_ready"]:
                    shadow_groups.setdefault((snap["phase"], snap["regime"]), snap)
            for snap in groups.values():
                actual = (finished[plan_id] - snap["predicted_at"]).total_seconds() / 60
                if actual > 0:
                    self.scheduler_observations.append({
                        "phase": snap["phase"], "regime": snap["regime"],
                        "raw_scheduler": snap["scheduler_minutes"], "actual": actual,
                    })
            for snap in shadow_groups.values():
                actual = (finished[plan_id] - snap["predicted_at"]).total_seconds() / 60
                if actual > 0:
                    self.shadow_comparisons.append({
                        "phase": snap["phase"], "regime": snap["regime"],
                        "baseline": snap["calibrated_minutes"],
                        "shadow": snap["shadow_minutes"], "actual": actual,
                    })
            phases = {}
            for snap in snapshots:
                phases.setdefault(snap["phase"], snap)
                if finished[plan_id] is not None and snap["predicted_at"] < finished[plan_id]:
                    actual = (finished[plan_id] - snap["predicted_at"]).total_seconds() / 60
                    if plan_id.startswith("live-"):
                        self.records.append({
                            "plan_id": plan_id,
                            "predicted_at": snap["predicted_at"].isoformat(),
                            "actual_finished_at": finished[plan_id].isoformat(),
                            "phase": snap["phase"], "regime": snap["regime"],
                            "pool_capacity_source": snap["pool_capacity_source"],
                            "pool_capacity_slots": snap["pool_capacity_slots"],
                            "actual_minutes": actual,
                            "legacy_minutes": snap["legacy_minutes"],
                            "scheduler_minutes": snap["scheduler_minutes"],
                            "calibrated_minutes": snap["calibrated_minutes"],
                            "predicted_minutes": snap["predicted_minutes"],
                            "shadow_minutes": snap["shadow_minutes"],
                            "shadow_ready": snap["shadow_ready"],
                            "river_used": snap["river_used"],
                        })
            for snap in phases.values():
                label = (finished[plan_id] - snap["predicted_at"]).total_seconds() / 60
                if label > 0:
                    model.learn(snap["features"], snap["calibrated_minutes"], label)
                    self.learned_snapshots += 1
        if plan_ids:
            model.save(session)

    def _replay(self, session, builds, *, predict):
        self.predictions.clear()
        completed_at = {b.plan_id: b.finished_at for b in builds}
        events = events_for(builds, interval_seconds=self.interval_seconds, predict=predict)
        i = 0
        active_builds = set()
        current_queued = 0
        current_running = 0
        running_by_vm = defaultdict(int)
        metrics = {"peak_active_builds": 0, "peak_queued_jobs": 0,
                   "peak_running_jobs": 0, "peak_running_jobs_on_vm": 0}
        while i < len(events):
            now = events[i][0]
            group = []
            while i < len(events) and events[i][0] == now:
                group.append(events[i])
                i += 1
            arrivals = set()
            finished = []
            did_tick = False
            for _, _, kind, item in group:
                if kind == "build_arrive":
                    arrivals.add(item.plan_id)
                    active_builds.add(item.plan_id)
                    current_queued += len(item.jobs)
                    session.execute(insert(build_table).values(
                        plan_id=item.plan_id, priority=item.priority,
                        created_at=now, status="running", finished_at=None,
                        updated_seq=self._next_seq()))
                    session.execute(insert(job_table), [
                        {"run_id": j.run_id, "plan_id": j.plan_id, "job_id": j.job_id,
                         "vm_name": None, "sched_pool_id": j.sched_pool_id,
                         "priority": j.priority, "created_at": now,
                         "queued_at": now, "started_at": None, "finished_at": None,
                         "status": "queued", "updated_seq": self._next_seq()}
                        for j in item.jobs
                    ])
                elif kind == "job_start":
                    current_queued -= 1
                    current_running += 1
                    running_by_vm[item.vm_name] += 1
                    self.observed_slots[item.vm_name] = max(
                        self.observed_slots[item.vm_name], running_by_vm[item.vm_name])
                    metrics["peak_running_jobs_on_vm"] = max(
                        metrics["peak_running_jobs_on_vm"], running_by_vm[item.vm_name])
                    session.execute(update(job_table).where(job_table.c.run_id == item.run_id)
                                    .values(status="running", started_at=now,
                                            vm_name=item.vm_name,
                                            updated_seq=self._next_seq()))
                elif kind == "job_finish":
                    current_running -= 1
                    running_by_vm[item.vm_name] -= 1
                    session.execute(update(job_table).where(job_table.c.run_id == item.run_id)
                                    .values(status="passed", finished_at=now,
                                            updated_seq=self._next_seq()))
                    session.add(HistoricalRun(
                        run_id=item.run_id, plan_id=item.plan_id, job_id=item.job_id,
                        vm_name=item.vm_name, sched_pool_id=item.sched_pool_id,
                        version="unknown", db_type="unknown",
                        status="passed", started_at=item.started_at, finished_at=now,
                        duration_seconds=(now - item.started_at).total_seconds()))
                elif kind == "build_finish":
                    session.execute(update(build_table).where(build_table.c.plan_id == item.plan_id)
                                    .values(status="passed", finished_at=now,
                                            updated_seq=self._next_seq()))
                    finished.append(item.plan_id)
                    active_builds.remove(item.plan_id)
                elif kind == "tick":
                    did_tick = True
            metrics["peak_active_builds"] = max(
                metrics["peak_active_builds"], len(active_builds))
            metrics["peak_queued_jobs"] = max(
                metrics["peak_queued_jobs"], current_queued)
            metrics["peak_running_jobs"] = max(
                metrics["peak_running_jobs"], current_running)
            session.flush()
            # Outcomes are revealed only after the completion event; River learns
            # strictly from snapshots made *before* the outcome was known.
            if finished and predict:
                self._learn_finished(session, finished, completed_at)
            if predict and (arrivals or did_tick):
                self._forecast(session, now, only=None if did_tick else arrivals)
        session.flush()
        if predict and builds[0].plan_id.startswith("live-"):
            self.live_metrics = metrics
        session.execute(delete(job_table))
        session.execute(delete(build_table))
        session.flush()

    def run(self):
        started_wall = perf_counter()
        if self.history_builds < 0 or self.build_count < 1:
            raise ValueError("History count must be nonnegative; builds must be positive")
        history = (generate_schedule(
            self.history_builds, prefix="history", seed=self.seed,
            start=ZERO, arrival_seconds=self.arrival_seconds,
            min_jobs=self.min_jobs, max_jobs=self.max_jobs, vms=self.history_vms,
            contention=self.history_contention,
            burst_size=min(self.burst_size, self.history_builds))
            if self.history_builds else [])
        start = (max(b.finished_at for b in history) + timedelta(days=1)
                 if history else ZERO + timedelta(days=1))
        live = generate_schedule(
            self.build_count, prefix="live", seed=self.seed + 1, start=start,
            arrival_seconds=self.arrival_seconds, min_jobs=self.min_jobs,
            max_jobs=self.max_jobs, vms=self.vms,
            contention=self.contention, burst_size=min(self.burst_size, self.build_count))
        with Session(self.engine) as session:
            if history:
                self._replay(session, history, predict=self.reconstruct)
            self._replay(session, live, predict=True)
            session.commit()
        first = {}
        for row in self.records:
            first.setdefault(row["plan_id"], row)
        start_rows = list(first.values())
        keys = ("legacy_minutes", "scheduler_minutes",
                "calibrated_minutes", "predicted_minutes")
        summary = {
            "seed": self.seed, "history_builds": self.history_builds,
            "pool_ids": sorted({v.pool_id for v in self.vms.values()}),
            "declared_pool_capacity": self.declared_pool_capacity,
            "capacity_refresh_recalculations": self.pool_cache.recalculations,
            "live_builds": self.build_count, "vm_count": len(self.vms),
            "server_count": len({config.server for config in self.vms.values()}),
            "vm_slot_range": [
                min(config.slots for config in self.vms.values()),
                max(config.slots for config in self.vms.values()),
            ],
            "contention": self.contention,
            "history_contention": self.history_contention,
            "historical_vm_count": len(self.history_vms),
            "burst_size": self.burst_size,
            "wall_seconds": round(perf_counter() - started_wall, 3),
            **self.live_metrics,
            "reconstruct_historical_queue": self.reconstruct,
            "historical_queue_snapshots_used": self.reconstruct,
            "learned_snapshots": self.learned_snapshots,
            "snapshots_generated": len(self.records),
            "scored_builds": len(start_rows),
            "scored_at": "first prediction per build",
            "model_predictions_used": sum(bool(r["river_used"]) for r in start_rows),
            "shadow_ready_first_predictions": sum(bool(r["shadow_ready"]) for r in start_rows),
            "shadow_snapshots_evaluated": len(self.shadow_comparisons),
            "scheduler_calibration_samples": len(self.scheduler_observations),
            "shadow_promoted_snapshots": self.shadow_promoted_count,
            "errors": {k: summarize([r[k] - r["actual_minutes"] for r in start_rows])
                       for k in keys},
        }
        return summary, self.records


def run_benchmark(*, output="benchmark-results/simulator", **kwargs):
    simulator = Simulator(**kwargs)
    try:
        summary, predictions = simulator.run()
    finally:
        simulator.close()
    folder = Path(output)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    columns = ["plan_id", "predicted_at", "actual_finished_at", "phase", "regime",
               "pool_capacity_source", "pool_capacity_slots", "actual_minutes", "legacy_minutes", "scheduler_minutes",
               "calibrated_minutes",
               "predicted_minutes", "shadow_minutes", "shadow_ready", "river_used"]
    with (folder / "predictions.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(predictions)
    return summary
