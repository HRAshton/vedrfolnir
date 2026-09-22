"""Observed and declared runner-pool capacities, learned without separate models.

Observed concurrency is a *lower bound* on slots, not the true maximum.
The ten-minute cache controls expensive historical re-estimation; current
running/queued load and an explicit scheduler declaration refresh each poll.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
import re
import zlib

from .features import timestamp

DEFAULT_POOL = "general"
REFRESH_SECONDS = 600
LOOKBACK_DAYS = 30
POOL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")


def pool_id(value):
    result = str(value or DEFAULT_POOL).strip()
    if not POOL_PATTERN.fullmatch(result):
        raise ValueError("sched_pool_id must be 1-80 letters, digits, _ . : or -")
    return result


def pool_of(job):
    value = job.get("sched_pool_id") if isinstance(job, dict) else getattr(
        job, "sched_pool_id", None)
    return pool_id(value)


def capacity_slots(value):
    """A missing limit differs from an explicitly disabled (zero-slot) pool."""
    if value is None or value == "":
        return None
    if isinstance(value, bool) or str(value).strip() != str(int(value)):
        raise ValueError("sched_capacity_slots must be a nonnegative integer or NULL")
    slots = int(value)
    if not 0 <= slots <= 1_000_000:
        raise ValueError("sched_capacity_slots must be between 0 and 1000000")
    return slots


def declared_capacities(pools, jobs=()):
    """Merge optional pool table and job annotations, rejecting conflicts."""
    result = {}
    for row in pools:
        name = pool_of(row)
        value = capacity_slots(row.get("sched_capacity_slots"))
        if name in result and result[name] != value:
            raise ValueError("Conflicting declared capacity for pool")
        result[name] = value
    for job in jobs:
        name = pool_of(job)
        raw = job.get("sched_capacity_slots") if isinstance(job, dict) else getattr(
            job, "sched_capacity_slots", None)
        value = capacity_slots(raw)
        if value is not None:
            if name in result and result[name] is not None and result[name] != value:
                raise ValueError("Conflicting declared capacity for pool")
            result[name] = value
    return result


class PoolCapacityCache:
    """Rolling history overlap refreshed at most every 600 simulated seconds."""

    def __init__(self, refresh_seconds=REFRESH_SECONDS):
        if refresh_seconds <= 0:
            raise ValueError("Capacity refresh interval must be positive")
        self.refresh_seconds = refresh_seconds
        self.refreshed_at = None
        self.historical_peaks = {}
        self.recent_live_peaks = {}
        self.recalculations = 0

    def snapshot(self, history, live_jobs, now, declared=None):
        now = timestamp(now)
        refresh = (self.refreshed_at is None or now < self.refreshed_at or
                   (now - self.refreshed_at).total_seconds() >= self.refresh_seconds)
        if refresh:
            window_start = now - timedelta(days=LOOKBACK_DAYS)
            events = defaultdict(list)
            for job in history:
                if getattr(job, "status", "") not in ("passed", "failed", "timed_out"):
                    continue
                start = timestamp(job.started_at)
                end = timestamp(job.finished_at)
                if not start or not end or start >= end or end > now or end < window_start:
                    continue
                p = pool_of(job)
                events[p].append((max(start, window_start), 1))
                events[p].append((end, -1))
            inferred = {}
            for p, transitions in events.items():
                # Finish before start at same instant; touching tasks do not overlap.
                running, peak = 0, 0
                for _, delta in sorted(transitions, key=lambda x: (x[0], x[1])):
                    running += delta
                    peak = max(peak, running)
                inferred[p] = peak
            self.historical_peaks = inferred
            self.recent_live_peaks = {}
            self.refreshed_at = now
            self.recalculations += 1

        running, queued = defaultdict(int), defaultdict(int)
        for job in live_jobs:
            p = pool_of(job)
            if job.status == "running":
                running[p] += 1
            elif job.status == "queued":
                queued[p] += 1
        for p, count in running.items():
            self.recent_live_peaks[p] = max(self.recent_live_peaks.get(p, 0), count)
        declared = declared or {}
        snapshots = {}
        for p in sorted(set(self.historical_peaks) | set(self.recent_live_peaks) |
                        set(running) | set(queued) | set(declared)):
            observed = max(self.historical_peaks.get(p, 0),
                           self.recent_live_peaks.get(p, 0), running[p])
            selected = declared.get(p)
            snapshots[p] = {
                "observed_slots": observed,
                "declared_slots": selected,
                "capacity_slots": selected if selected is not None
                                  else observed if observed else None,
                "source": ("declared" if selected is not None else
                           "observed" if observed else "unknown"),
                "running_jobs": running[p],
                "queued_jobs": queued[p],
            }
        return snapshots


def pool_features(pool_snapshots, plan_jobs):
    """Numeric, reproducibly hashed pool identity for one shared River model."""
    pools = {pool_of(job) for job in plan_jobs if job.status in ("running", "queued")}
    cap = sum(pool_snapshots[p]["capacity_slots"] or 0
              for p in pools if p in pool_snapshots)
    running = sum(pool_snapshots[p]["running_jobs"] for p in pools
                  if p in pool_snapshots)
    queued = sum(pool_snapshots[p]["queued_jobs"] for p in pools
                 if p in pool_snapshots)
    unknown = sum(p not in pool_snapshots or
                  pool_snapshots[p]["capacity_slots"] is None for p in pools)
    declared = sum(p in pool_snapshots and
                   pool_snapshots[p]["source"] == "declared" for p in pools)
    values = {
        "pool_count": float(len(pools)),
        "pool_capacity_slots": float(cap),
        "pool_running_jobs": float(running),
        "pool_queued_jobs": float(queued),
        "pool_queue_pressure": queued / max(1, cap),
        "pool_capacity_unknown_count": float(unknown),
        "pool_declared_capacity_count": float(declared),
    }
    # Bounded categorical encoding; not a separate model per pool.
    for p in sorted(pools):
        bucket = zlib.crc32(p.encode("utf-8")) % 16
        name = f"pool_identity_bucket_{bucket:02d}"
        values[name] = values.get(name, 0.0) + 1.0 / max(1, len(pools))
    return values
