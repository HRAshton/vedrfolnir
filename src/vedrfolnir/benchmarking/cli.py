"""Benchmark command-line interface and report export."""
from __future__ import annotations
import argparse
import csv
import json
import time
from pathlib import Path
from .evaluation import evaluate, evaluate_planstart
from .scheduler_replay import evaluate_scheduler_replay
from .loaders import csv_plans, wfcommons_plans, wfcommons_planstart, swf_plans, wfcommons_timeline_plans, wfcommons_taskspan_plans

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("csv", "csv-scheduler", "wfcommons", "wfcommons-start",
                                             "wfcommons-timeline", "wfcommons-taskspan", "swf", "travistorrent"), default="csv")
    parser.add_argument("--input", default="examples/benchmarks")
    parser.add_argument("--output", default="benchmark-results")
    parser.add_argument("--max-plans", type=int, default=400)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--min-training", type=int, default=30)
    parser.add_argument("--progress", action="store_true", help="Print evaluation progress")
    parser.add_argument("--offsets", default="0,60,300,900,1800,3600",
                        help="Prediction offsets in seconds since plan submission")
    args = parser.parse_args(argv)
    if args.max_plans < 1:
        parser.error("--max-plans must be positive")
    started_loading = time.perf_counter()
    loaders = {"csv": csv_plans, "csv-scheduler": csv_plans,
               "wfcommons": wfcommons_plans, "wfcommons-start": wfcommons_planstart,
               "wfcommons-timeline": wfcommons_timeline_plans,
               "wfcommons-taskspan": wfcommons_taskspan_plans, "swf": swf_plans}
    dataset_stats = {}
    try:
        if args.format == "travistorrent":
            from vedrfolnir.travistorrent import load_builds
            plans = load_builds(Path(args.input), limit=args.max_plans, stats=dataset_stats)
        else:
            plans = (swf_plans(args.input, args.max_plans)
                     if args.format == "swf"
                     else loaders[args.format](args.input, stats=dataset_stats)
                     if args.format in ("wfcommons-timeline", "wfcommons-taskspan")
                     else loaders[args.format](args.input))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    plans = sorted(plans, key=lambda x: (x.started_at, x.plan_id))[:args.max_plans]
    loading_seconds = time.perf_counter() - started_loading
    if args.progress:
        print(f"Loaded {len(plans)} selected plans in {loading_seconds:.1f}s. Evaluating...", flush=True)
    started_evaluation = time.perf_counter()
    try:
        offsets = tuple(sorted(set(int(x) for x in args.offsets.split(","))))
        if args.format in ("wfcommons-start", "travistorrent"):
            result = evaluate_planstart(
                plans, warmup=args.warmup, min_training=args.min_training,
                progress=args.progress)
        elif args.format in ("csv-scheduler", "wfcommons-timeline", "wfcommons-taskspan"):
            result = evaluate_scheduler_replay(
                plans, warmup=args.warmup, min_training=args.min_training,
                offsets=offsets, progress=args.progress)
        else:
            result = evaluate(
                plans, warmup=args.warmup, min_training=args.min_training,
                offsets=offsets, progress=args.progress)
    except ValueError as exc:
        parser.error(str(exc))
    evaluation_seconds = time.perf_counter() - started_evaluation
    scope = ({
        "swf": "single-job queue-wait ETA; not multi-job plans",
        "csv-scheduler": "real-time visible multi-job queue replay (CSV)",
        "wfcommons-timeline": "real workflow launch to finish; only consistent real task timestamps",
        "wfcommons-taskspan": "real active task span only (NOT workflow launch to finish)",
        "wfcommons-start": "workflow-start ETA only; no task progress",
        "travistorrent": "build-start ETA only; no per-job runtimes or queue observations",
    }).get(args.format, "multi-job plan ETA")
    caution = ({
        "travistorrent": "tr_duration is full BUILD duration repeated on job rows. Not a benchmark of job-composition runtime, queue waiting, or progress ETA.",
        "wfcommons-start": "Input uses pre-execution DAG only; task timestamps absent. No live progress or queue ETA.",
        "wfcommons-timeline": ("Only complete, clock-consistent per-task timelines accepted. "
            "Queue admission absent; pending work forces conservative fallback."),
        "wfcommons-taskspan": ("Measured first-task-start to last-task-finish ONLY; "
            "not workflow launch makespan, and not a queue-wait benchmark. "
            "Original task timestamps unaltered; missing queue times stay NULL."),
        "csv-scheduler": ("Use only real sourced job queue timestamps for queue claims; "
            "do not infer unseen arrivals or CPU slot inventory."),
    }).get(args.format, "Toy CSV fixtures are synthetic; report accuracy only on independent real traces.")
    result["summary"].update({
        "data_load_seconds": round(loading_seconds, 3),
        "evaluation_seconds": round(evaluation_seconds, 3),
        "format": args.format,
        "benchmark_scope": scope,
        "queue_observability": "unavailable; source has no queue timestamps"
          if args.format in ("wfcommons", "wfcommons-start", "travistorrent")
          else result["summary"].get("queue_observability", "observed queued_at / SWF wait"),
        "source": str(args.input),
        "caution": caution,
    })
    if dataset_stats:
        result["summary"]["dataset_stats"] = dataset_stats
    folder = Path(args.output)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "summary.json").write_text(json.dumps(result["summary"], indent=2)+"\n")
    with (folder / "predictions.csv").open("w", newline="", encoding="utf-8") as out:
        columns = ["plan_id", "predicted_at", "phase", "plan_index", "actual_minutes",
                   "baseline_minutes", "predicted_minutes", "river_used",
                   "shadow_minutes", "shadow_ready", "family", "task_count",
                   "scheduler_minutes", "calibrated_minutes",
                   "scheduler_available", "scheduler_fallback_reasons", "prediction_method",
                   "pending_jobs", "queue_admission_observed",
                   "pool_capacity_source", "regime"]
        writer = csv.DictWriter(out, fieldnames=columns)
        writer.writeheader()
        for row in result["forecasts"]:
            exported = {k: row.get(k) for k in columns}
            if "scheduler_fallback_reasons" in row:
                exported["scheduler_fallback_reasons"] = json.dumps(row["scheduler_fallback_reasons"])
            writer.writerow(exported)
    if "job_forecasts" in result:
        with (folder / "job_predictions.csv").open("w", newline="", encoding="utf-8") as out:
            columns = ["run_id", "plan_id", "plan_index", "status", "predicted_at",
                       "forecast_available", "unavailable_reasons", "prediction_method",
                       "predicted_start_at", "start_time_source", "predicted_finish_at",
                       "predicted_wait_minutes", "predicted_remaining_minutes",
                       "actual_wait_minutes", "actual_remaining_minutes", "outcome_status",
                       "capacity_source", "evidence_warnings", "p90_finish_at", "river_used"]
            writer = csv.DictWriter(out, fieldnames=columns)
            writer.writeheader()
            for row in result["job_forecasts"]:
                exported = {k: row.get(k) for k in columns}
                for key in ("unavailable_reasons", "evidence_warnings"):
                    exported[key] = json.dumps(row[key])
                writer.writerow(exported)
    from vedrfolnir.benchmark_report import write_report
    write_report(result, folder / "report.html")
    print(json.dumps(result["summary"], indent=2))
    print(f"\nDetailed offline report: {folder / 'report.html'}")


if __name__ == "__main__":
    main()
