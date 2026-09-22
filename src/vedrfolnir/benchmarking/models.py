"""Normalized benchmark entities and validation."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from vedrfolnir.features import timestamp


@dataclass(frozen=True)
class Job:
    run_id: str
    plan_id: str
    job_id: str
    queued_at: datetime | None
    started_at: datetime
    finished_at: datetime
    status: str = "passed"
    version: str = "unknown"
    db_type: str = "unknown"
    vm_name: str = "unknown"
    priority: int = 0
    sched_pool_id: str = "general"


@dataclass(frozen=True)
class Workload:
    plan_id: str
    started_at: datetime
    finished_at: datetime
    jobs: tuple[Job, ...]
    priority: int = 0
    status: str = "passed"


@dataclass(frozen=True)
class WorkflowSummary:
    """Observed workflow makespan and pre-execution structural features.

    Pegasus WfInstances often lacks per-task start times. Never manufacture a
    task timeline from runtimes or make a queued/running-job benchmark from it.
    """
    plan_id: str
    family: str
    started_at: datetime
    finished_at: datetime
    task_count: int
    features: dict[str, float]


def _workflow_family(path: Path, root: Path) -> str:
    relative = path.relative_to(root) if root.is_dir() else Path(path.name)
    if len(relative.parts) > 1:
        return relative.parts[0].lower()
    return re.split(r"[-_]", path.stem)[0].lower()

def _date(raw):
    dt = timestamp(raw)
    if dt is None:
        raise ValueError("missing benchmark timestamp")
    return dt


def _valid(plan):
    if not plan.jobs or plan.started_at >= plan.finished_at:
        raise ValueError(f"invalid or empty plan {plan.plan_id}")
    for job in plan.jobs:
        if (job.plan_id != plan.plan_id or job.started_at < plan.started_at
                or job.started_at >= job.finished_at or job.finished_at > plan.finished_at
                or (job.queued_at is not None and job.queued_at > job.started_at)):
            raise ValueError(f"inconsistent times or plan membership: {job.run_id}")
    return plan


