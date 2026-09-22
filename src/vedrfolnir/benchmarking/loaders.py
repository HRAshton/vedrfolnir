"""Parse public traces and local fixtures into workload records."""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
import csv
import gzip
import json
import math
import re
from .models import Job, Workload, WorkflowSummary, _date, _valid, _workflow_family

def wfcommons_planstart(folder):
    """Real workflow-start/makespan benchmark, no task timeline required.

    ONLY workflow specification is used for model inputs. Measured makespan
    and task runtime are outcomes, never features available at plan start.
    """
    folder = Path(folder)
    if not folder.exists():
        raise ValueError(f"WfCommons path does not exist: {folder}")
    paths = sorted(folder.rglob("*.json")) if folder.is_dir() else [folder]
    if not paths:
        raise ValueError(f"No WfCommons JSON files under {folder}")
    result, reasons = [], Counter()
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            workflow = data["workflow"]
            spec = workflow["specification"]
            execution = workflow["execution"]
            task_specs = spec["tasks"]
            if not isinstance(task_specs, list) or not task_specs:
                raise ValueError("no task specifications")
            starts_at = _date(execution["executedAt"])
            makespan = float(execution["makespanInSeconds"])
            if not math.isfinite(makespan) or makespan <= 0:
                raise ValueError("invalid makespanInSeconds")
            groups = Counter()
            edges = 0
            roots = 0
            for task in task_specs:
                name = str(task.get("name") or task.get("id") or "unknown")
                # Strip execution-specific Pegasus task IDs, retaining task kinds.
                kind = re.sub(r"_ID\d+$", "", name)
                groups[kind] += 1
                parents = task.get("parents") or []
                edges += len(parents)
                roots += not bool(parents)
            count = len(task_specs)
            metrics = spec.get("metrics") or {}
            family = _workflow_family(path, folder)
            features = {
                "plan_total_jobs": float(count),
                "log_plan_total_jobs": math.log1p(count),
                "task_type_count": float(len(groups)),
                "task_type_dominance": max(groups.values()) / count,
                "roots_fraction": roots / count,
                "edge_count_per_task": edges / count,
                "levels": float(metrics.get("numberOfLevels") or 0),
                "max_width_fraction": float(metrics.get("maximumWidth") or 0) / count,
                f"family__{family}": 1.0,
            }
            relative = path.relative_to(folder) if folder.is_dir() else Path(path.name)
            result.append(WorkflowSummary(
                str(relative), family, starts_at,
                starts_at + timedelta(seconds=makespan), count, features))
        except (KeyError, ValueError, TypeError, OverflowError, json.JSONDecodeError) as exc:
            reasons[str(exc)] += 1
    if not result:
        raise ValueError(
            f"Found {len(paths)} WfCommons JSON files, but no instances with "
            "workflow.specification.tasks and execution.executedAt / "
            f"makespanInSeconds. Rejection reasons: {reasons.most_common(4)}")
    return result
def csv_plans(folder):
    """Explicit benchmark data: plans.csv and jobs.csv with stable column aliases."""
    folder = Path(folder)
    with (folder / "jobs.csv").open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    grouped = {}
    for r in rows:
        job = Job(
            run_id=r["run_id"], plan_id=r["plan_id"], job_id=r["job_id"],
            queued_at=_date(r["queued_at"]) if r.get("queued_at") else None,
            started_at=_date(r["started_at"]), finished_at=_date(r["finished_at"]),
            status=r.get("status") or "passed", version=r.get("version") or "unknown",
            db_type=r.get("db_type") or "unknown", vm_name=r.get("vm_name") or "unknown",
            priority=int(r.get("priority") or 0),
            sched_pool_id=r.get("sched_pool_id") or "general",
        )
        grouped.setdefault(job.plan_id, []).append(job)
    with (folder / "plans.csv").open(newline="", encoding="utf-8-sig") as stream:
        result = []
        for row in csv.DictReader(stream):
            jobs = tuple(grouped.pop(row["plan_id"], ()))
            result.append(_valid(Workload(
                plan_id=row["plan_id"], started_at=_date(row["started_at"]),
                finished_at=_date(row["finished_at"]), jobs=jobs,
                priority=int(row.get("priority") or 0), status=row.get("status") or "passed",
            )))
    if grouped:
        raise ValueError(f"jobs refer to unknown plan IDs: {list(grouped)[:3]}")
    return result


def wfcommons_plans(folder):
    """Load WfFormat execution JSON with useful discovery/validation errors.

    Workflows expose tasks but not queue admission times; pending is not queued.
    Unrelated JSON (e.g. metadata) is skipped rather than crashing the import.
    """
    folder = Path(folder)
    if not folder.exists():
        raise ValueError(
            f"WfCommons path does not exist: {folder}. "
            "Run 'python -m vedrfolnir.benchmark_quickstart' to fetch the dataset."
        )
    paths = sorted(folder.rglob("*.json")) if folder.is_dir() else [folder]
    if not paths:
        raise ValueError(
            f"No JSON execution files under {folder}. "
            "Your Git sparse checkout may be empty. Re-run the quickstart downloader "
            "or inspect the Pegasus subdirectories."
        )
    result = []
    errors = {}
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            workflow = data["workflow"]
            execution = workflow["execution"]
            names = {str(t["id"]): str(t.get("name") or t["id"])
                     for t in workflow.get("specification", {}).get("tasks", [])}
            tasks = []
            for t in execution["tasks"]:
                if not t.get("executedAt") or t.get("runtimeInSeconds") is None:
                    continue
                start = _date(t["executedAt"])
                runtime = float(t["runtimeInSeconds"])
                if runtime <= 0 or not math.isfinite(runtime):
                    continue
                tasks.append((t, start, start + timedelta(seconds=runtime)))
            if not tasks:
                raise ValueError("no tasks with both executedAt and positive runtimeInSeconds")
            plan_start = (_date(execution["executedAt"])
                          if execution.get("executedAt") else min(x[1] for x in tasks))
            plan_start = min(plan_start, *(x[1] for x in tasks))
            plan_end = max(t[2] for t in tasks)
            plan_id = str(path.relative_to(folder) if folder.is_dir() else path.name)
            jobs = tuple(Job(run_id=f"{plan_id}:{t['id']}", plan_id=plan_id,
                             job_id=names.get(str(t["id"]), str(t["id"])),
                             queued_at=None, started_at=start, finished_at=end,
                             vm_name=str((t.get("machines") or ["unknown"])[0]))
                         for t, start, end in tasks)
            result.append(_valid(Workload(plan_id=plan_id, started_at=plan_start,
                                          finished_at=plan_end, jobs=jobs)))
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            if len(errors) < 6:
                errors[str(path.relative_to(folder) if folder.is_dir() else path.name)] = str(exc)
    if not result:
        examples = "; ".join(f"{name}: {reason}" for name, reason in errors.items())
        raise ValueError(
            f"Found {len(paths)} JSON files under {folder}, but zero valid "
            f"WfFormat execution instances. Examples: {examples}. "
            "Expected workflow.execution.tasks[] with executedAt and runtimeInSeconds. "
            "Check the data directory and the dataset version."
        )
    return result


def swf_plans(file, limit=None):
    """SWF records as one-job workloads: real queue waits, NOT multi-job plans."""
    path = Path(file)
    opener = gzip.open if path.suffix == ".gz" else open
    base = datetime(2000, 1, 1, tzinfo=timezone.utc)
    result = []
    with opener(path, "rt", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            line = line.strip()
            if not line or line.startswith(";"):
                continue
            fields = line.split()
            if len(fields) < 11:
                continue
            try:
                job_id = fields[0]
                submit, wait, runtime = (int(fields[i]) for i in (1, 2, 3))
                processors = int(fields[4])
            except ValueError:
                continue
            if submit < 0 or wait < 0 or runtime <= 0:
                continue
            queued = base + timedelta(seconds=submit)
            start = queued + timedelta(seconds=wait)
            finish = start + timedelta(seconds=runtime)
            size = "1" if processors == 1 else "2-4" if 2 <= processors <= 4 else "5+"
            key = f"swf-{job_id}"
            job = Job(run_id=key, plan_id=key, job_id=f"processors:{size}",
                      queued_at=queued, started_at=start, finished_at=finish,
                      status="passed" if fields[10] == "1" else "failed")
            result.append(_valid(Workload(plan_id=key, started_at=queued,
                                          finished_at=finish, jobs=(job,))))
            if limit is not None and len(result) >= limit:
                break
    if not result:
        raise ValueError("No valid SWF jobs found")
    return result




def wfcommons_timeline_plans(folder, *, stats=None, task_span=False):
    """Accept ONLY independently observed complete task timelines.

    Most public WfFormat instances provide runtimes but no individual task
    start times. Such instances are unsuitable for live-scheduler replay and
    must not have task starts or queues manufactured from their DAG.
    """
    folder = Path(folder)
    if not folder.exists():
        raise ValueError("WfCommons timeline path does not exist")
    paths = sorted(folder.rglob("*.json")) if folder.is_dir() else [folder]
    plans, reasons = [], Counter()
    inconsistent_workflow_clocks = 0
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            workflow = data["workflow"]
            spec_tasks = workflow["specification"]["tasks"]
            execution = workflow["execution"]
            tasks = execution["tasks"]
            if not spec_tasks or not tasks:
                raise ValueError("empty_tasks")
            specifications = {str(t["id"]): t for t in spec_tasks}
            ids = [str(t["id"]) for t in tasks]
            if (len(ids) != len(set(ids)) or len(specifications) != len(spec_tasks)
                    or set(ids) != set(specifications)):
                raise ValueError("incomplete_or_duplicate_task_ids")
            if any(t.get("executedAt") is None for t in tasks):
                raise ValueError("missing_task_executedAt")
            if any(t.get("runtimeInSeconds") is None for t in tasks):
                raise ValueError("missing_task_runtime")
            relative = str(path.relative_to(folder) if folder.is_dir() else path.name)
            executions = []
            for t in tasks:
                start = _date(t["executedAt"])
                duration = float(t["runtimeInSeconds"])
                if not math.isfinite(duration) or duration <= 0:
                    raise ValueError("invalid_task_runtime")
                end = start + timedelta(seconds=duration)
                machines = t.get("machines") or []
                spec = specifications[str(t["id"])]
                # Normalize per-execution suffix only; no actual runtime as a feature.
                kind = re.sub(r"_ID\d+$", "", str(spec.get("name") or t["id"]))
                executions.append(Job(
                    run_id=f"{relative}:{t['id']}",
                    plan_id=relative, job_id=kind,
                    queued_at=None, started_at=start, finished_at=end,
                    vm_name=str(machines[0]) if machines else "unknown",
                    priority=int(t.get("priority") or 0),
                ))
            first_start = min(job.started_at for job in executions)
            last_finish = max(job.finished_at for job in executions)
            workflow_start = (_date(execution["executedAt"])
                              if execution.get("executedAt") else first_start)
            if abs((workflow_start - first_start).total_seconds()) > 60:
                inconsistent_workflow_clocks += 1
            # Two distinct, explicitly labeled targets:
            # - Full workflow: enforce consistent clock and makespan.
            # - Active task span: first measured task to last measured task.
            #   Never shift execution timestamps to manufacture alignment.
            if task_span:
                window_start, window_end = first_start, last_finish
            else:
                if workflow_start > first_start:
                    raise ValueError("workflow_starts_after_task")
                window_start, window_end = workflow_start, last_finish
                if execution.get("makespanInSeconds") is not None:
                    span = float(execution["makespanInSeconds"])
                    if not math.isfinite(span) or span <= 0:
                        raise ValueError("invalid_workflow_makespan")
                    claimed_end = workflow_start + timedelta(seconds=span)
                    if claimed_end + timedelta(seconds=1) < last_finish:
                        raise ValueError("workflow_finishes_before_last_task")
                    window_end = max(claimed_end, last_finish)
            plans.append(_valid(Workload(
                plan_id=relative, started_at=window_start,
                finished_at=window_end, jobs=tuple(executions),
            )))
        except (ValueError, TypeError, KeyError, OverflowError,
                json.JSONDecodeError) as exc:
            code = str(exc)
            if code in ("missing_task_executedAt", "missing_task_runtime",
                        "incomplete_or_duplicate_task_ids",
                        "workflow_starts_after_task",
                        "workflow_finishes_before_last_task",
                        "invalid_workflow_makespan"):
                reasons[code] += 1
            else:
                reasons["invalid_or_unrelated_instance"] += 1
    if stats is not None:
        stats.update({
            "candidate_files": len(paths), "complete_timelines": len(plans),
            "skipped_files": len(paths) - len(plans),
            "skip_reasons": dict(sorted(reasons.items())),
            "queue_admission_available": False,
            "target": "observed_first_to_last_task" if task_span
                      else "workflow_launch_to_finish",
            "workflow_clock_offset_over_minute_files": inconsistent_workflow_clocks,
        })
    if not plans:
        raise ValueError(
            f"No complete real task timelines found in {len(paths)} WfCommons files. "
            f"Skipped: {dict(sorted(reasons.items()))}. "
            "For inconsistent workflow/task clocks, --format wfcommons-taskspan "
            "evaluates only the independently observed active task span; "
            "never fabricate queue admissions or silently align clocks."
        )
    return plans


def wfcommons_taskspan_plans(folder, *, stats=None):
    """Opt-in measured active task span, never full workflow-launch makespan."""
    return wfcommons_timeline_plans(folder, stats=stats, task_span=True)
