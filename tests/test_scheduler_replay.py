"""True chronological replay of the production scheduler on real task timelines."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import csv
import json

import pytest

from vedrfolnir.benchmarks import (
    Job, Workload, _make_job, evaluate_scheduler_replay,
    wfcommons_timeline_plans,
)
from vedrfolnir.benchmarking.cli import main


T0 = datetime(2026, 3, 1, tzinfo=timezone.utc)


def work(name, *, minute=0, duration=20, pool="general",
         admission=True, vm="vm-1"):
    start = T0 + timedelta(minutes=minute)
    jobs = (
        Job(run_id=f"{name}-a", plan_id=name, job_id="compile",
            queued_at=start if admission else None,
            started_at=start + timedelta(minutes=2),
            finished_at=start + timedelta(minutes=8),
            vm_name=vm, sched_pool_id=pool),
        Job(run_id=f"{name}-b", plan_id=name, job_id="test",
            queued_at=start if admission else None,
            started_at=start + timedelta(minutes=10),
            finished_at=start + timedelta(minutes=duration),
            vm_name=vm, sched_pool_id=pool),
    )
    return Workload(name, start, start + timedelta(minutes=duration), jobs)


def test_trace_replay_uses_real_scheduler_and_reports_missing_queue():
    plans = [work(f"p-{i}", minute=i * 27) for i in range(4)]
    result = evaluate_scheduler_replay(
        plans, warmup=1, offsets=(0, 180, 600),
        min_training=1000)
    report = result["summary"]
    assert report["plans_evaluated"] == 3
    assert report["scheduler_eligible_plans"] >= 2
    assert report["queue_admission_coverage"] == 1.0
    assert report["scheduler_fallback_reason_counts"] == {}
    assert report["raw_scheduler"]["count"] == 3
    assert report["raw_on_eligible_subset"]["count"] >= 2
    assert report["selected_predictor"]["count"] == 3
    assert result["forecasts"][0]["scheduler_available"] is False
    assert any(row["scheduler_available"] for row in result["forecasts"])
    assert not any(row["river_used"] for row in result["forecasts"])


def test_trace_replay_preserves_pending_status_and_never_invents_queue():
    plan = work("unknown", admission=False)
    res = evaluate_scheduler_replay([plan], warmup=0, offsets=(0, 60, 660))
    assert res["summary"]["plans_with_complete_queue_admission"] == 0
    assert res["summary"]["queue_admission_coverage"] == 0
    first = res["forecasts"][0]
    assert first["scheduler_available"] is False
    assert first["pending_jobs"] == 2
    assert "pending_jobs" in first["scheduler_fallback_reasons"]
    assert res["summary"]["scheduler_fallback_reason_counts"]["pending_jobs"] == 1
    assert res["summary"]["phases"]["start"]["scheduler_fallback_reason_counts"]["pending_jobs"] == 1
    assert first["prediction_method"] == "historical_baseline"
    future = plan.jobs[1]
    assert _make_job(future, plan.started_at).status == "pending"
    assert _make_job(future, plan.started_at).vm_name == "unknown"
    assert _make_job(future, future.started_at).vm_name == "vm-1"


def test_replay_outcome_and_machine_assignment_not_visible_before_future_plan():
    early = work("early", minute=0)
    future = work("future", minute=100, duration=500, pool="special", vm="future-vm")
    left = evaluate_scheduler_replay(
        [early], warmup=0, offsets=(0, 180))
    right = evaluate_scheduler_replay(
        [early, future], warmup=0, offsets=(0, 180))
    earliest = lambda report: [row for row in report["forecasts"]
                               if row["plan_id"] == "early"]
    a, b = earliest(left), earliest(right)
    assert [(x["baseline_minutes"], x["scheduler_minutes"],
             x["predicted_minutes"], x["scheduler_available"]) for x in a] == [
                 (x["baseline_minutes"], x["scheduler_minutes"],
                  x["predicted_minutes"], x["scheduler_available"]) for x in b
             ]


def test_trace_loader_rejects_absent_task_timestamps_instead_of_fabricating(tmp_path):
    path = tmp_path / "workflow.json"
    start = "2026-03-01T00:00:00Z"
    document = {"workflow": {
        "specification": {"tasks": [
            {"id": "a", "name": "compile", "parents": []},
            {"id": "b", "name": "test", "parents": ["a"]}]},
        "execution": {"executedAt": start, "makespanInSeconds": 1200,
                      "tasks": [
                          {"id": "a", "executedAt": start,
                           "runtimeInSeconds": 600, "machines": ["runner-1"]},
                          {"id": "b", "runtimeInSeconds": 500}]}},
    }
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="missing_task_executedAt"):
        wfcommons_timeline_plans(tmp_path)

    document["workflow"]["execution"]["tasks"][1]["executedAt"] = (
        "2026-03-01T00:10:00Z")
    path.write_text(json.dumps(document))
    records = wfcommons_timeline_plans(tmp_path)
    assert len(records) == 1
    assert len(records[0].jobs) == 2
    assert all(job.queued_at is None for job in records[0].jobs)
    assert records[0].jobs[0].vm_name == "runner-1"
    assert (records[0].finished_at - records[0].started_at).total_seconds() == 1200

    # A partial instance is not accepted as a complete workflow.
    document["workflow"]["execution"]["tasks"].pop()
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="incomplete_or_duplicate_task_ids"):
        wfcommons_timeline_plans(tmp_path)


def test_taskspan_mode_is_distinct_when_workflow_clock_is_inconsistent(tmp_path):
    from vedrfolnir.benchmarks import wfcommons_taskspan_plans
    path = tmp_path / "task-timing.json"
    # This mirrors the real public Snakemake clock discrepancy: task events
    # precede the reported overall workflow launch by hours.
    doc = {"workflow": {
        "specification": {"tasks": [{"id": "a", "name": "compile"}]},
        "execution": {
            "executedAt": "2026-03-01T05:00:00Z",
            "makespanInSeconds": 600,
            "tasks": [{"id": "a", "executedAt": "2026-03-01T02:00:00Z",
                       "runtimeInSeconds": 600, "machines": ["worker-01"]}],
        },
    }}
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="workflow_starts_after_task|invalid_or_unrelated_instance"):
        wfcommons_timeline_plans(path)
    stats = {}
    result = wfcommons_taskspan_plans(path, stats=stats)
    assert len(result) == 1
    assert result[0].started_at == datetime(2026, 3, 1, 2, tzinfo=timezone.utc)
    assert (result[0].finished_at - result[0].started_at).total_seconds() == 600
    assert stats["target"] == "observed_first_to_last_task"
    assert stats["workflow_clock_offset_over_minute_files"] == 1
    assert stats["queue_admission_available"] is False


def test_cli_timeline_writes_scheduler_eligibility_without_queue_claim(tmp_path):
    folder = tmp_path / "data"
    folder.mkdir()
    start = T0
    for i in range(3):
        at = start + timedelta(hours=i)
        data = {"workflow": {
            "specification": {"tasks": [{"id": "first", "name": "compile"},
                                        {"id": "second", "name": "test"}]},
            "execution": {
                "executedAt": at.isoformat(),
                "makespanInSeconds": 18 * 60,
                "tasks": [
                    {"id": "first", "executedAt": at.isoformat(),
                     "runtimeInSeconds": 360, "machines": ["vm-one"]},
                    {"id": "second", "executedAt": (
                        at + timedelta(minutes=9)).isoformat(),
                     "runtimeInSeconds": 540, "machines": ["vm-one"]},
                ]}}}
        (folder / f"trace-{i}.json").write_text(json.dumps(data))
    output = tmp_path / "report"
    main(["--format", "wfcommons-timeline", "--input", str(folder),
          "--output", str(output), "--warmup", "1", "--min-training", "30",
          "--offsets", "0,600"])
    summary = json.loads((output / "summary.json").read_text())
    assert summary["dataset_stats"]["candidate_files"] == 3
    assert summary["dataset_stats"]["complete_timelines"] == 3
    assert summary["dataset_stats"]["queue_admission_available"] is False
    assert summary["benchmark_scope"].startswith("real workflow launch to finish")
    assert summary["plans_evaluated"] == 2
    assert summary["queue_admission_coverage"] == 0
    with (output / "predictions.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) >= 3
    assert "scheduler_available" in rows[0]
    assert "pending_jobs" in json.loads(rows[0]["scheduler_fallback_reasons"])
    assert (output / "report.html").exists()
    report = (output / "report.html").read_text()
    assert "Scheduler eligibility" in report
    assert "pending_jobs" in report
    assert "Individual job forecasts" in report
    assert (output / "job_predictions.csv").is_file()


def test_recorded_future_admission_does_not_make_first_forecast_runnable():
    from dataclasses import replace
    plans = [work(f"p-{i}", minute=i * 27) for i in range(4)]
    plans = [replace(p, jobs=(p.jobs[0], replace(
        p.jobs[1], queued_at=p.started_at + timedelta(minutes=1)))) for p in plans]
    result = evaluate_scheduler_replay(plans, warmup=1, offsets=(0, 180))
    summary = result["summary"]
    assert summary["queue_admission_coverage"] == 1.0
    assert summary["scheduler_eligible_plans"] == 0
    assert summary["scheduler_fallback_reason_counts"] == {"pending_jobs": 3}
    later = [row for row in result["forecasts"] if row["plan_index"] >= 1
             and row["predicted_at"] != plans[row["plan_index"]].started_at.isoformat()]
    assert len(later) == 3
    assert all(row["scheduler_available"] for row in later)


def test_future_outcomes_and_placements_do_not_change_admitted_queue_forecast():
    from dataclasses import replace
    seed = work("history", minute=0)
    target = work("target", minute=27)
    changed = replace(target, finished_at=target.finished_at + timedelta(hours=5),
                      jobs=tuple(replace(j, finished_at=j.finished_at + timedelta(hours=5),
                                         vm_name="future-only-vm") for j in target.jobs))
    before = evaluate_scheduler_replay([seed, target], warmup=1, offsets=(0,))
    after = evaluate_scheduler_replay([seed, changed], warmup=1, offsets=(0,))
    left, right = before["forecasts"][-1], after["forecasts"][-1]
    assert left["scheduler_available"] is True
    for key in ("baseline_minutes", "scheduler_minutes", "calibrated_minutes",
                "predicted_minutes", "scheduler_available", "scheduler_fallback_reasons", "features"):
        assert left[key] == right[key]


def test_fifo_control_obeys_capacity_and_exercises_eligible_first_forecasts(tmp_path):
    import runpy
    from pathlib import Path
    from vedrfolnir.benchmarking.loaders import csv_plans
    generator = runpy.run_path(str(Path(__file__).parents[1] / "examples/benchmarks/queue_control.py"))
    generator["generate"](tmp_path, count=8)
    plans = csv_plans(tmp_path)
    jobs = [job for plan in plans for job in plan.jobs]
    assert all(job.queued_at == plan.started_at for plan in plans for job in plan.jobs)
    for vm in {j.vm_name for j in jobs}:
        ordered = sorted((j for j in jobs if j.vm_name == vm), key=lambda j: j.started_at)
        assert all(left.finished_at <= right.started_at for left, right in zip(ordered, ordered[1:]))
    result = evaluate_scheduler_replay(plans, warmup=2, offsets=(0, 180), min_training=1000)
    assert result["summary"]["scheduler_eligible_plans"] == 6
    assert result["summary"]["scheduler_fallback_reason_counts"] == {}


def test_job_replay_scores_first_observation_without_cherry_picking_later_capacity():
    result = evaluate_scheduler_replay([work("cold")], warmup=0, offsets=(0, 180))
    metrics = result["summary"]["job_metrics"]
    assert metrics["queued"]["jobs_observed"] == 2
    assert metrics["queued"]["forecast_available_jobs"] == 0
    assert metrics["running"]["jobs_observed"] == 1
    assert metrics["running"]["forecast_available_jobs"] == 1
    later = [r for r in result["job_forecasts"] if r["run_id"] == "cold-b" and r["predicted_at"] != T0.isoformat()]
    assert later and later[0]["forecast_available"] is True
    first = result["job_forecasts"][0]
    assert first["actual_wait_minutes"] == 2
    assert first["actual_remaining_minutes"] == 8
    assert first["outcome_status"] == "passed"


def test_job_replay_forecasts_admitted_work_despite_future_pending_stage():
    from dataclasses import replace
    prior, target = work("prior"), work("target", minute=27)
    target = replace(target, jobs=(target.jobs[0], replace(
        target.jobs[1], queued_at=target.started_at + timedelta(minutes=5))))
    result = evaluate_scheduler_replay([prior, target], warmup=1, offsets=(0,))
    assert result["summary"]["scheduler_eligible_plans"] == 0
    metrics = result["summary"]["job_metrics"]
    assert metrics["queued"]["forecast_available_jobs"] == 1
    assert metrics["pending"]["forecast_available_jobs"] == 0
    assert metrics["pending"]["unavailable_reason_counts"] == {"pending_job": 1}


def test_job_replay_hides_future_starts_finishes_and_machine_assignments():
    from dataclasses import replace
    prior, target = work("prior"), work("target", minute=27)
    changed = replace(target, finished_at=target.finished_at + timedelta(hours=2),
                      jobs=tuple(replace(j, started_at=j.started_at + timedelta(hours=1),
                                         finished_at=j.finished_at + timedelta(hours=2),
                                         vm_name="future-machine") for j in target.jobs))
    left = evaluate_scheduler_replay([prior, target], warmup=1, offsets=(0,))
    right = evaluate_scheduler_replay([prior, changed], warmup=1, offsets=(0,))
    def predictions(result):
        return [{k: v for k, v in r.items() if k not in ("actual_wait_minutes", "actual_remaining_minutes")}
                for r in result["job_forecasts"] if r["plan_id"] == "target"]
    assert predictions(left) == predictions(right)
