"""Observable scheduling baseline and guarded online residual correction.

No simulator-private VM speed, concurrency limit or contention parameter enters
this module. Historical completed runs and current job states are its inputs.
"""
from __future__ import annotations

import heapq
import math
import pickle
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from types import SimpleNamespace

from sqlalchemy import select

from .db import HistoricalRun, ModelCheckpoint, utcnow
from .features import DurationEstimator, quantile, timestamp
from .learning import build_model
from .pooling import (
    DEFAULT_POOL, PoolCapacityCache, pool_of,
    pool_features as make_pool_features,
)

RESIDUAL_MODEL_ID = "scheduler_residual_river_v1"
MAX_ETA_MINUTES = 60 * 24 * 30


@dataclass(frozen=True)
class JobProjection:
    started_at: datetime | None
    finished_at: datetime


class ObservedScheduler:
    """Priority/FIFO queue replay, calibrated exclusively from observed history.

    VM inventory and observed slot counts are inferred from finished run
    intervals and currently running jobs. No unknown future arrivals assumed.
    An unassigned queued job can execute on any observed VM; already assigned
    jobs remain pinned to their VM.
    """

    def __init__(self, session, now, jobs, max_history=500, *,
                 pool_cache=None, declared_capacities=None):
        self.now = timestamp(now)
        self.duration = DurationEstimator(session, max_history, finished_by=self.now)
        historical = list(session.scalars(
            select(HistoricalRun)
            .where(HistoricalRun.finished_at <= self.now)
            .order_by(HistoricalRun.finished_at.desc())
            .limit(max(300, min(5000, max_history * 8)))
        ))
        self.pool_cache = pool_cache if pool_cache is not None else PoolCapacityCache()
        self.pool_snapshots = self.pool_cache.snapshot(
            historical, jobs, self.now, declared_capacities)
        self.pool_aware = bool(declared_capacities) or any(
            pool_of(row) != DEFAULT_POOL for row in historical) or any(
            pool_of(job) != DEFAULT_POOL for job in jobs)
        self.queue_order_unknown_pools = {pool_of(job) for job in jobs
                                          if job.status == "queued" and job.queued_at is None}
        self.pool_affinity_pools = {pool_of(job) for job in jobs if job.status == "queued"
                                    and job.vm_name not in (None, "", "unknown", "__unassigned__")}
        self.pool_type_samples = defaultdict(list)
        vm_events = defaultdict(list)
        by_type = defaultdict(list)
        by_pair = defaultdict(list)
        currently_running = defaultdict(int)
        self.vm_names = set()
        self.vm_slots = defaultdict(lambda: 1)
        for row in historical:
            vm = str(row.vm_name or "")
            if vm and vm != "unknown":
                self.vm_names.add(vm)
                vm_events[vm].append((timestamp(row.started_at), 1))
                vm_events[vm].append((timestamp(row.finished_at), -1))
            if row.status not in ("passed", "failed", "timed_out") or row.duration_seconds <= 0:
                continue
            value = row.duration_seconds / 60
            by_type[row.job_id].append(value)
            self.pool_type_samples[(pool_of(row), row.job_id)].append(value)
            if vm and vm != "unknown":
                by_pair[(row.job_id, vm)].append(value)
        for vm, events in vm_events.items():
            # At identical timestamps a job finishing frees a slot before
            # another starts; do not count an artificial overlap.
            events.sort(key=lambda event: (event[0], event[1]))
            current = 0
            for _, delta in events:
                current += delta
                self.vm_slots[vm] = max(self.vm_slots[vm], current)
        for job in jobs:
            vm = getattr(job, "vm_name", None)
            if vm and vm != "unknown":
                self.vm_names.add(vm)
            if job.status == "running":
                vm = vm if vm and vm != "unknown" else "__unassigned__"
                currently_running[vm] += 1
        for vm, count in currently_running.items():
            self.vm_names.add(vm)
            self.vm_slots[vm] = max(self.vm_slots[vm], count)

        self.vm_names = tuple(sorted(self.vm_names)) or ("__unassigned__",)
        self.pair_samples = by_pair
        self.vm_factor = {}
        ratios = defaultdict(list)
        for (job_type, vm), values in by_pair.items():
            if len(values) >= 3 and len(by_type[job_type]) >= 5:
                overall = median(by_type[job_type])
                if overall > 0:
                    ratios[vm].append(median(values) / overall)
        for vm, values in ratios.items():
            if values:
                self.vm_factor[vm] = min(4.0, max(0.25, median(values)))
        # The synthetic "__unassigned__" bucket is not a discovered VM.
        # Treating it as one silently fabricates available capacity at cold start.
        self.observable_vms = tuple(vm for vm in self.vm_names if vm != "__unassigned__")
        self.observed_vm_count = len(self.observable_vms)
        self.observed_slot_count = sum(self.vm_slots[vm] for vm in self.observable_vms)

    def remaining(self, job, vm):
        """Conditional running-time prediction with learned VM-specific medians."""
        elapsed = 0.0
        if job.status == "running" and job.started_at is not None:
            elapsed = max(0.0, (self.now - timestamp(job.started_at)).total_seconds() / 60)
        samples = self.pair_samples.get((job.job_id, vm), [])
        survivors = [duration - elapsed for duration in samples if duration > elapsed]
        if len(survivors) >= 3:
            return max(0.01, median(survivors))
        # DurationEstimator's fallback already handles censored running jobs
        # and installations with no historical VM names.
        baseline = self.duration.remaining(job, self.now).p50
        if samples:
            return max(0.01, median(survivors)) if survivors else max(0.01, baseline)
        return max(0.01, baseline * self.vm_factor.get(vm, 1.0))

    def pool_features(self, plan_jobs):
        return make_pool_features(self.pool_snapshots, plan_jobs)

    def pool_source(self, plan_jobs):
        if not self.pool_aware:
            return "legacy_vm"
        pools = {pool_of(job) for job in plan_jobs
                 if job.status in ("queued", "running")}
        sources = {self.pool_snapshots.get(name, {}).get("source", "unknown")
                   for name in pools}
        return next(iter(sources)) if len(sources) == 1 else (
            "mixed" if sources else "unknown")

    def pool_remaining(self, job, pool):
        if job.status == "running" and job.vm_name not in (None, "", "unknown"):
            return self.remaining(job, job.vm_name)
        samples = self.pool_type_samples.get((pool, job.job_id), [])
        elapsed = (max(0.0, (self.now - timestamp(job.started_at)).total_seconds() / 60)
                   if job.status == "running" and job.started_at else 0.0)
        survivors = [duration - elapsed for duration in samples if duration > elapsed]
        if len(survivors) >= 3:
            return max(0.01, median(survivors))
        return max(0.01, self.duration.remaining(job, self.now).p50)

    def project_pool_jobs(self, jobs):
        """Replay each resource pool's priority/FIFO queue on its observed slots.

        Declared zero means paused (queued work is not projected). Missing
        capacity means unknown, not a fictitious one-slot worker.
        """
        projected = {}
        per_pool = defaultdict(list)
        releases = defaultdict(list)
        occupied = defaultdict(int)
        sequence = 0
        for job in jobs:
            pool = pool_of(job)
            if job.status in ("passed", "failed", "timed_out", "cancelled", "skipped"):
                if job.finished_at:
                    projected[job.run_id] = JobProjection(timestamp(job.started_at), timestamp(job.finished_at))
            elif job.status == "running":
                minutes = self.pool_remaining(job, pool)
                end = self.now + timedelta(minutes=minutes)
                projected[job.run_id] = JobProjection(timestamp(job.started_at), end)
                occupied[pool] += 1
                sequence += 1
                heapq.heappush(releases[pool], (end, sequence))
            elif job.status == "queued":
                per_pool[pool].append(job)

        for pool, waiting in per_pool.items():
            state = self.pool_snapshots.get(pool)
            if state is None or state["capacity_slots"] is None:
                continue
            limit = state["capacity_slots"]
            if limit == 0:
                # Zero-slot pool must not start any queued work.
                continue
            now = self.now
            waiting.sort(key=lambda job: (
                -int(job.priority or 0),
                timestamp(job.queued_at or job.started_at or self.now),
                job.run_id,
            ))
            for job in waiting:
                while occupied[pool] >= limit:
                    if not releases[pool]:
                        break
                    end, _ = heapq.heappop(releases[pool])
                    now = max(now, end)
                    occupied[pool] -= 1
                    while releases[pool] and releases[pool][0][0] <= now:
                        heapq.heappop(releases[pool])
                        occupied[pool] -= 1
                if occupied[pool] >= limit:
                    break
                end = now + timedelta(minutes=self.pool_remaining(job, pool))
                occupied[pool] += 1
                sequence += 1
                heapq.heappush(releases[pool], (end, sequence))
                projected[job.run_id] = JobProjection(now, end)
        return projected

    def project_pools(self, jobs):
        """Compatibility view of pool projections for plan forecasting."""
        return {run_id: p.finished_at for run_id, p in self.project_pool_jobs(jobs).items()}

    def project_jobs(self, jobs):
        """Project dispatch and finish together, using the same slot allocation."""
        if self.pool_aware:
            return self.project_pool_jobs(jobs)
        occupied = defaultdict(int)
        releases = []
        projected = {}
        next_sequence = 0
        for job in jobs:
            if job.status in ("passed", "failed", "timed_out", "cancelled", "skipped"):
                if job.finished_at:
                    projected[job.run_id] = JobProjection(timestamp(job.started_at), timestamp(job.finished_at))
            elif job.status == "running":
                vm = job.vm_name if job.vm_name and job.vm_name != "unknown" else "__unassigned__"
                occupied[vm] += 1
                end = self.now + timedelta(minutes=self.remaining(job, vm))
                next_sequence += 1
                heapq.heappush(releases, (end, next_sequence, vm))
                projected[job.run_id] = JobProjection(timestamp(job.started_at), end)

        waiting = sorted((job for job in jobs if job.status == "queued"),
                         key=lambda job: (-int(job.priority or 0),
                                          timestamp(job.queued_at or job.started_at or self.now),
                                          job.run_id))
        at = self.now
        for job in waiting:
            assigned = getattr(job, "vm_name", None)
            pinned = assigned and assigned not in ("unknown", "__unassigned__")
            while True:
                candidates = (assigned,) if pinned else self.vm_names
                eligible = [vm for vm in candidates
                            if occupied[vm] < max(occupied[vm], self.vm_slots[vm])]
                if eligible:
                    break
                if not releases:
                    return projected
                at = releases[0][0]
                while releases and releases[0][0] <= at:
                    _, _, vm = heapq.heappop(releases)
                    occupied[vm] -= 1
            # Prefer an idle VM over load-sharing.
            vm = min(eligible, key=lambda name: (occupied[name] > 0, occupied[name], name))
            minutes = self.remaining(job, vm)
            end = at + timedelta(minutes=minutes)
            occupied[vm] += 1
            next_sequence += 1
            heapq.heappush(releases, (end, next_sequence, vm))
            projected[job.run_id] = JobProjection(at, end)
        return projected

    def project(self, jobs):
        """Return the unchanged plan-forecast end-time view."""
        return {run_id: p.finished_at for run_id, p in self.project_jobs(jobs).items()}

    def plan_fallback_reasons(self, plan_jobs, projected):
        """Explain the existing eligibility gate using only current observations.

        Multiple blockers can coexist. An empty list means a complete runnable
        plan can be projected; diagnostics never invent capacity or admissions.
        """
        known = {"queued", "running", "pending", "passed", "failed",
                 "timed_out", "cancelled", "skipped"}
        remaining = [job for job in plan_jobs if job.status in ("queued", "running")]
        reasons = []
        if not plan_jobs:
            return ["no_plan_jobs"]
        if not remaining:
            reasons.append("no_remaining_jobs")
        if any(job.status not in known for job in plan_jobs):
            reasons.append("unsupported_job_status")
        if any(job.status == "pending" for job in plan_jobs):
            reasons.append("pending_jobs")
        if not self.pool_aware and self.observed_vm_count <= 0:
            reasons.append("vm_inventory_unobserved")
        for job in remaining:
            if job.run_id in projected:
                continue
            reason = "projection_incomplete"
            if self.pool_aware and job.status == "queued":
                capacity = self.pool_snapshots.get(pool_of(job), {}).get("capacity_slots")
                if capacity is None:
                    reason = "pool_capacity_unknown"
                elif capacity == 0:
                    reason = "pool_paused"
            if reason not in reasons:
                reasons.append(reason)
        return reasons

    def can_estimate_plan(self, plan_jobs, projected):
        """Only simulate a complete, known, runnable plan on observed VMs."""
        return not self.plan_fallback_reasons(plan_jobs, projected)

    def estimate_plan(self, now, plan_jobs, projected, fallback):
        """Conservatively use the original baseline if scheduling is unobserved."""
        if not self.can_estimate_plan(plan_jobs, projected):
            return fallback
        pending_ends = [projected[job.run_id] for job in plan_jobs
                        if job.status in ("queued", "running")]
        return min(MAX_ETA_MINUTES, max(
            1.0, (max(pending_ends) - timestamp(now)).total_seconds() / 60
        ))


def residual_features(features, baseline):
    """Exclude stored metadata so future outcomes cannot leak into training."""
    values = {key: float(value) for key, value in features.items()
              if not key.startswith("__") and isinstance(value, (int, float))
              and math.isfinite(value)}
    values["scheduler_log_minutes"] = math.log1p(max(1.0, baseline))
    return values


class ShadowResidualRegressor:
    """River predicts log(actual_remaining / scheduler_baseline), not raw ETA.

    The correction is bounded to 0.5x..2x. It is never selected based solely
    on its training error; completed build predictions gate promotion.
    """

    def __init__(self, session):
        checkpoint = session.get(ModelCheckpoint, RESIDUAL_MODEL_ID)
        if checkpoint is None:
            self.model = build_model()
            self.seen_count = 0
        else:
            # Application-owned database checkpoints only; do not deserialize
            # user-provided bytes.
            self.model = pickle.loads(checkpoint.payload)
            self.seen_count = checkpoint.seen_count

    def estimate(self, features, baseline, min_training=30):
        if self.seen_count < min_training:
            return baseline, False
        try:
            residual = self.model.predict_one(residual_features(features, baseline))
            if residual is None or not math.isfinite(residual):
                return baseline, False
            corrected = baseline * math.exp(max(-math.log(2), min(math.log(2), residual)))
            return max(1.0, min(MAX_ETA_MINUTES, corrected)), True
        except (ValueError, OverflowError):
            return baseline, False

    def learn(self, features, baseline, actual_remaining_minutes):
        if baseline <= 0 or actual_remaining_minutes <= 0:
            return
        target = math.log(max(0.01, actual_remaining_minutes) / max(0.01, baseline))
        self.model.learn_one(residual_features(features, baseline), target)
        self.seen_count += 1

    def save(self, session):
        row = session.get(ModelCheckpoint, RESIDUAL_MODEL_ID)
        if row is None:
            row = ModelCheckpoint(name=RESIDUAL_MODEL_ID, payload=b"", seen_count=0)
            session.add(row)
        row.payload = pickle.dumps(self.model, protocol=pickle.HIGHEST_PROTOCOL)
        row.seen_count = self.seen_count
        row.updated_at = utcnow()


def load_regime(features):
    """Coarse, installation-independent ratio of queued work to observed slots."""
    slots = max(1.0, float(features.get("observed_vm_slots") or 1.0))
    pressure = (float(features["pool_queue_pressure"])
                if "pool_queue_pressure" in features else
                float(features.get("global_queue_count") or 0.0) / slots)
    return "saturated" if pressure >= 2.0 else "busy" if pressure >= 0.5 else "idle"


def scheduler_bias_correction(baseline, features, observations, *, min_builds=30):
    """Correct chronic *underestimation*, using only completed prior builds.

    Conservative, bounded half-median correction is permitted only at
    sustained saturation and only when a rolling cohort agrees. Keeping the
    raw scheduler output allows unbiased paired benchmarking.
    """
    if load_regime(features) != "saturated":
        return baseline
    phase = features.get("phase") or _phase_for_features(features)
    cohort = [obs for obs in observations
              if obs.get("phase") == phase and obs.get("regime") == "saturated"][-100:]
    if len(cohort) < min_builds:
        return baseline
    errors = [obs["actual"] - obs["raw_scheduler"] for obs in cohort]
    recent = errors[-20:]
    if (sum(error > 0 for error in errors) / len(errors) < 0.70
            or sum(error > 0 for error in recent) / len(recent) < 0.70):
        return baseline
    correction = min(60.0, max(0.0, 0.5 * median(errors)), baseline * 0.75)
    # Check that the latest independent completions still benefit.
    if correction <= 0 or sum(abs(e - correction) for e in recent) >= (
            0.90 * sum(abs(e) for e in recent)):
        return baseline
    return min(MAX_ETA_MINUTES, baseline + correction)


def _phase_for_features(features):
    from .features import progress_phase
    return progress_phase(features)


def shadow_promotion(comparisons, *, phase=None, regime=None,
                     min_builds=40, required_gain=0.08):
    """Promote only after paired completed-build validation *in this regime*.

    The first eligible forecast per build/phase/regime is prequential. The
    full rolling cohort must beat the scheduler on MAE without P90 regression,
    and the latest 20 completed builds must continue to improve. Promotion is
    re-evaluated each forecast, so workload drift withdraws it automatically.
    """
    recent = [row for row in comparisons
              if (phase is None or row.get("phase") == phase)
              and (regime is None or row.get("regime") == regime)][-200:]
    if len(recent) < min_builds:
        return False
    base_errors = [abs(x["baseline"] - x["actual"]) for x in recent]
    shadow_errors = [abs(x["shadow"] - x["actual"]) for x in recent]
    base_mae = sum(base_errors) / len(recent)
    shadow_mae = sum(shadow_errors) / len(recent)
    if base_mae <= 0:
        return False
    tail = 20
    recent_base = sum(base_errors[-tail:]) / tail
    recent_shadow = sum(shadow_errors[-tail:]) / tail
    return (
        shadow_mae <= base_mae * (1 - required_gain)
        and quantile(shadow_errors, 0.9) <= max(1.0, quantile(base_errors, 0.9))
        and recent_shadow < recent_base
        and quantile(shadow_errors[-tail:], 0.9) <= max(
            1.0, quantile(base_errors[-tail:], 0.9))
    )
