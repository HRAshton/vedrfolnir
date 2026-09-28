"""Deterministic workload generation and contention-aware virtual-time scheduling.

Only this simulator module knows VM speed multipliers, slot counts and alpha.
Observed job rows expose VM names only *after* dispatch.
"""
from __future__ import annotations

import heapq
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

ZERO = datetime(2025, 1, 1, tzinfo=timezone.utc)
JOB_KINDS = {"checkout": 2.0, "compile": 12.0, "package": 6.0, "deploy": 4.0}


@dataclass(frozen=True)
class VMConfig:
    """Ground truth, withheld from the prediction feature pipeline."""

    speed: float
    slots: int = 1
    server: str = "server-01"
    pool_id: str = "general"


@dataclass
class Job:
    run_id: str
    plan_id: str
    job_id: str
    vm_name: str | None
    priority: int
    created_at: datetime
    # True work (speed-one seconds) and remaining work are simulator-private.
    actual_seconds: float
    started_at: datetime | None = None
    finished_at: datetime | None = None
    remaining_work: float = 0.0
    sched_pool_id: str = "general"


@dataclass
class Build:
    plan_id: str
    priority: int
    created_at: datetime
    jobs: list[Job] = field(default_factory=list)
    finished_at: datetime | None = None


def normalize_vms(vms):
    """Accept explicit VMConfig or legacy name:speed settings (one slot)."""
    if not vms:
        raise ValueError("At least one VM is required")
    result = {}
    for name, config in vms.items():
        if not isinstance(name, str) or not name:
            raise ValueError("VM name must be non-empty")
        if isinstance(config, VMConfig):
            entry = config
        elif isinstance(config, (tuple, list)) and len(config) == 2:
            entry = VMConfig(float(config[0]), int(config[1]))
        else:
            entry = VMConfig(float(config))
        if (not math.isfinite(entry.speed) or entry.speed <= 0
                or not isinstance(entry.slots, int) or isinstance(entry.slots, bool)
                or entry.slots < 1):
            raise ValueError("VM speed must be positive and slots must be an integer >= 1")
        result[name] = entry
    return result


def make_vm_fleet(*, seed=42, servers=1, vms_per_server=30,
                  min_slots=1, max_slots=3, pool_mode=False):
    """Create balanced server pools with private, seeded speed and slot settings."""
    if servers < 1 or vms_per_server < 1 or min_slots < 1 or max_slots < min_slots:
        raise ValueError("Invalid server, VM, or VM slot configuration")
    rng = random.Random(seed + 1_000_003)
    return {
        f"server-{server:02d}-vm-{number:02d}": VMConfig(
            speed=rng.choice((0.7, 0.85, 1.0, 1.15, 1.4, 1.6)),
            slots=rng.randint(min_slots, max_slots),
            server=f"server-{server:02d}",
            pool_id=f"server-{server:02d}" if pool_mode else "general",
        )
        for server in range(1, servers + 1)
        for number in range(1, vms_per_server + 1)
    }


def generate_schedule(count, *, prefix, seed, start, arrival_seconds,
                      min_jobs, max_jobs, vms, contention=0.15, burst_size=0):
    """Dispatch highest-priority waiting job to least-loaded available VM.

    Idle VMs are always preferred. When all VMs are occupied but have spare
    slots, load is balanced by current job count (name breaks ties). If all
    slots are used, jobs wait priority-first, then FIFO.

    Each active job receives VM.speed/(1 + contention*(N-1)) work seconds
    per wall-clock second; the event clock advances to the next arrival or
    job completion, updating every running job's residual work.
    """
    if count < 1 or arrival_seconds <= 0 or min_jobs < 1 or max_jobs < min_jobs:
        raise ValueError("Invalid build counts, arrival rate, or jobs-per-build")
    if not math.isfinite(contention) or contention < 0 or not 0 <= burst_size <= count:
        raise ValueError("Contention must be finite/nonnegative and burst must fit builds")
    vms = normalize_vms(vms)
    rng = random.Random(seed)
    kinds = tuple(JOB_KINDS)
    pool_ids = tuple(sorted({vm.pool_id for vm in vms.values()}))
    builds = []
    at = start
    for index in range(count):
        if index == 0 or index >= burst_size:
            at += timedelta(seconds=max(1, rng.expovariate(1 / arrival_seconds)))
        plan_id = f"{prefix}-{index:05d}"
        priority = rng.choice((0, 0, 0, 1, 1, 2, 3))
        build = Build(plan_id, priority, at)
        for number in range(rng.randint(min_jobs, max_jobs)):
            kind = rng.choice(kinds)
            work = max(1.0, JOB_KINDS[kind] * 60 * rng.lognormvariate(0, 0.3))
            selected_pool = rng.choice(pool_ids) if len(pool_ids) > 1 else pool_ids[0]
            build.jobs.append(Job(f"{plan_id}-j{number:02d}", plan_id, kind,
                                  None, priority, at, work, remaining_work=work,
                                  sched_pool_id=selected_pool))
        builds.append(build)

    waiting = []
    running = {name: [] for name in sorted(vms)}
    arrival_index = 0
    now = start
    finished_count = 0
    total_jobs = sum(len(b.jobs) for b in builds)
    epsilon = 1e-6
    while finished_count < total_jobs:
        next_arrival = (builds[arrival_index].created_at
                        if arrival_index < len(builds)
                        else datetime.max.replace(tzinfo=timezone.utc))
        # The current parallelism determines each running job's service rate.
        next_finish = datetime.max.replace(tzinfo=timezone.utc)
        for vm, active in running.items():
            if not active:
                continue
            speed = vms[vm].speed / (1 + contention * (len(active) - 1))
            # datetime has microsecond precision; enforce progress at the last tick.
            soonest = max(1e-6, min(job.remaining_work for job in active) / speed)
            next_finish = min(next_finish, now + timedelta(seconds=soonest))
        advance_to = min(next_arrival, next_finish)
        if advance_to <= now and not waiting and arrival_index >= len(builds):
            raise RuntimeError("Virtual clock did not progress")
        seconds = (advance_to - now).total_seconds()
        if seconds < 0:
            raise RuntimeError("Virtual time went backwards")
        if seconds:
            for vm, active in running.items():
                if not active:
                    continue
                progress = seconds * vms[vm].speed / (1 + contention * (len(active) - 1))
                for job in active:
                    job.remaining_work = max(0.0, job.remaining_work - progress)
        now = advance_to

        for vm in running:
            survivors = []
            for job in running[vm]:
                if job.remaining_work <= epsilon:
                    job.finished_at = now
                    finished_count += 1
                else:
                    survivors.append(job)
            running[vm] = survivors

        while arrival_index < len(builds) and builds[arrival_index].created_at <= now:
            build = builds[arrival_index]
            for job in build.jobs:
                heapq.heappush(waiting,
                               (-job.priority, job.created_at, job.run_id, job))
            arrival_index += 1

        while waiting:
            available = [vm for vm, active in running.items()
                         if len(active) < vms[vm].slots]
            if not available:
                break
            # Pick the first *eligible* queued job, retaining priority/FIFO
            # order within each pool without head-of-line blocking others.
            candidate = next((item for item in sorted(waiting)
                              if any(vms[vm].pool_id == item[3].sched_pool_id
                                     for vm in available)), None)
            if candidate is None:
                break
            if candidate == waiting[0]:
                _, _, _, job = heapq.heappop(waiting)
            else:
                waiting.remove(candidate)
                heapq.heapify(waiting)
                job = candidate[3]
            eligible = [vm for vm in available
                        if vms[vm].pool_id == job.sched_pool_id]
            vm = min(eligible, key=lambda name: (
                bool(running[name]), len(running[name]), name))
            job.vm_name = vm
            job.started_at = now
            running[vm].append(job)

    for build in builds:
        build.finished_at = max(job.finished_at for job in build.jobs)
    return builds
