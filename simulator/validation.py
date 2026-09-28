"""Multi-seed synthetic validation and optional existing recorded-trace benchmarks.

Run: python -m simulator.validation --quick
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import random
from collections import defaultdict
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

from vedrfolnir.features import quantile
from .engine import Simulator
from .scheduling import make_vm_fleet


@dataclass(frozen=True)
class Scenario:
    name: str
    arrival_seconds: float
    min_jobs: int
    max_jobs: int
    burst_fraction: float = 0.0
    contention: float = 0.15
    historical_contention: float | None = None
    historical_vm_fraction: float = 1.0
    pool_mode: bool = False
    declared_pool_capacity: bool = False


SCENARIOS = {
    "steady": Scenario("steady", 180, 2, 5),
    "burst": Scenario("burst", 150, 3, 6, burst_fraction=1.0),
    "overload": Scenario("overload", 12, 4, 7, burst_fraction=0.25),
    "drift": Scenario("drift", 12, 4, 7, burst_fraction=0.20,
                      contention=0.55, historical_contention=0.05,
                      historical_vm_fraction=0.5),
    "drift_idle": Scenario("drift_idle", 75, 2, 6, contention=0.55,
                           historical_contention=0.05, historical_vm_fraction=0.5),
    "multi_pool": Scenario("multi_pool", 40, 3, 7, pool_mode=True),
    "multi_pool_declared": Scenario(
        "multi_pool_declared", 40, 3, 7, pool_mode=True,
        declared_pool_capacity=True),
    "multi_pool_drift": Scenario(
        "multi_pool_drift", 22, 3, 7, pool_mode=True,
        historical_vm_fraction=0.5, contention=0.40,
        historical_contention=0.08),
}
PREDICTORS = ("legacy_minutes", "scheduler_minutes",
              "calibrated_minutes", "predicted_minutes")


def metrics(pairs):
    """Accuracy metrics for matched point-in-time forecast and outcome pairs."""
    pairs = list(pairs)
    if not pairs:
        return {"count": 0, "mae_minutes": None,
                "p90_abs_error_minutes": None, "bias_minutes": None}
    errors = [estimate - truth for estimate, truth in pairs]
    return {"count": len(errors),
            "mae_minutes": round(mean(abs(x) for x in errors), 3),
            "p90_abs_error_minutes": round(quantile([abs(x) for x in errors], 0.9), 3),
            "bias_minutes": round(mean(errors), 3)}


def first_predictions(records, warmup_builds):
    """Choose earliest forecast per build; exclude first chronological builds."""
    earliest = {}
    for row in records:
        current = earliest.get(row["plan_id"])
        if current is None or row["predicted_at"] < current["predicted_at"]:
            earliest[row["plan_id"]] = row
    ordered = sorted(earliest.values(), key=lambda r: (r["predicted_at"], r["plan_id"]))
    if warmup_builds >= len(ordered):
        raise ValueError("warmup_builds must be less than the live build count")
    return ordered, ordered[warmup_builds:]


def score(rows):
    """Score selected predictions for all builds; shadow only on a matched subset."""
    result = {name: metrics((r[name], r["actual_minutes"]) for r in rows)
              for name in PREDICTORS}
    paired = [r for r in rows if r.get("shadow_ready")
              and r.get("shadow_minutes") is not None]
    result["shadow_minutes"] = metrics(
        (r["shadow_minutes"], r["actual_minutes"]) for r in paired)
    result["scheduler_on_shadow_subset"] = metrics(
        (r["scheduler_minutes"], r["actual_minutes"]) for r in paired)
    result["selected_river_builds"] = sum(bool(r["river_used"]) for r in rows)
    result["shadow_ready_builds"] = len(paired)
    return result


def midbuild_snapshots(records, heldout_ids):
    """Earliest snapshot per build and prediction-time phase (no outcome peeking)."""
    chosen = {}
    for row in records:
        if row["plan_id"] not in heldout_ids:
            continue
        key = (row["plan_id"], row["phase"])
        if key not in chosen or row["predicted_at"] < chosen[key]["predicted_at"]:
            chosen[key] = row
    return list(chosen.values())


def write_csv(path, rows, fields):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def bootstrap_seed_delta(values):
    """Descriptive interval on independent seed-level paired MAE differences."""
    if len(values) < 3:
        return None
    rng = random.Random(77)
    samples = [mean(rng.choices(values, k=len(values))) for _ in range(1000)]
    return [round(quantile(samples, 0.025), 3),
            round(quantile(samples, 0.975), 3)]


def model_effects(run_rows):
    """Paired first-forecast MAE deltas using independent *seed* as unit.

    Negative changes are improvements. Never bootstrap individual correlated
    builds or compare a model's shadow-only subset against all builds.
    """
    by_group = defaultdict(list)
    for run in run_rows:
        by_group[(run["scenario"], run["reconstruct_historical_queue"])].append(run)
    effects = []
    for (scenario, reconstruct), group in sorted(by_group.items()):
        ordered = sorted(group, key=lambda row: row["seed"])
        raw = [row["scheduler_mae_minutes"] for row in ordered]
        for key, label in (
            ("calibrated_mae_minutes", "calibrated"),
            ("selected_mae_minutes", "selected"),
        ):
            differences = [row[key] - base for row, base in zip(ordered, raw)]
            effects.append({
                "scenario": scenario,
                "reconstruct_historical_queue": reconstruct,
                "model": label,
                "seed_count": len(differences),
                "mean_delta_mae_minutes": round(mean(differences), 3),
                "improved_seeds": sum(difference < 0 for difference in differences),
                "worsened_seeds": sum(difference > 0 for difference in differences),
                "seed_bootstrap_95pct_interval_minutes": bootstrap_seed_delta(differences),
            })
    return effects


def regression_failures(summary, *, tolerance=0.05, min_seeds=3):
    """Guard against substantial aggregate first-forecast regressions.

    The tolerance is prospective and only evaluated when at least three
    independent seeds are available. Never require River to outperform a
    robust scheduler if the gate correctly keeps it in shadow mode.
    """
    failed = []
    for group in summary["synthetic"]:
        if group["seeds"] < min_seeds:
            continue
        metrics_by_model = group["scores"]
        baseline = metrics_by_model["scheduler_minutes"]
        selected = metrics_by_model["predicted_minutes"]
        for key in ("mae_minutes", "p90_abs_error_minutes"):
            old = baseline[key]
            new = selected[key]
            allowance = max(0.25, old * tolerance)
            if new > old + allowance:
                failed.append(
                    f'{group["scenario"]} recon={group["reconstruct_historical_queue"]}: '
                    f'selected {key}={new:.3f} exceeds raw scheduler '
                    f'{old:.3f} plus tolerance {allowance:.3f}')
    return failed


def run_existing_trace(fmt, source, folder, *, max_plans=400, warmup=20):
    """Reuse the established benchmark runner, keeping its scores independent."""
    from vedrfolnir.benchmarking.cli import main as benchmark_main
    with redirect_stdout(io.StringIO()):
        benchmark_main([
            "--format", fmt, "--input", str(source), "--output", str(folder),
            "--max-plans", str(max_plans), "--warmup", str(warmup),
            "--min-training", "10",
        ])
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    return {"format": fmt, "scope": summary["benchmark_scope"],
            "evaluated": summary["plans_evaluated"],
            "source": str(source), "summary_path": str(folder / "summary.json"),
            "baseline": summary.get("baseline"),
            "selected_predictor": summary.get("selected_predictor")}


def markdown_report(data):
    lines = [
        "# Build ETA validation",
        "",
        "Synthetic first-forecast evaluation with chronological warmup and delayed labels.",
        "MAE and P90 errors are in minutes. Independent trace scores are NOT pooled.",
        "",
        "| Scenario | Historical queue | Seeds | Builds | Legacy MAE | Raw scheduler MAE | Calibrated MAE | Selected MAE | Selected P90 | River selected |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in data["synthetic"]:
        scores = row["scores"]
        lines.append(
            f'| {row["scenario"]} | {row["reconstruct_historical_queue"]} | '
            f'{row["seeds"]} | {row["scored_builds"]} | '
            f'{scores["legacy_minutes"]["mae_minutes"]:.2f} | '
            f'{scores["scheduler_minutes"]["mae_minutes"]:.2f} | '
            f'{scores["calibrated_minutes"]["mae_minutes"]:.2f} | '
            f'{scores["predicted_minutes"]["mae_minutes"]:.2f} | '
            f'{scores["predicted_minutes"]["p90_abs_error_minutes"]:.2f} | '
            f'{scores["selected_river_builds"]} |')
    lines += [
        "", "## Paired model effects by seed",
        "",
        "Negative MAE deltas mean improvement over the raw scheduler. Each",
        "bootstrap sample resamples *seeds*, not correlated build forecasts.",
        "",
        "| Scenario | Reconstruct | Predictor | Seeds | Mean Δ MAE | Improved seeds | 95% seed CI |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for effect in data["model_effects"]:
        lines.append(
            f'| {effect["scenario"]} | {effect["reconstruct_historical_queue"]} | '
            f'{effect["model"]} | {effect["seed_count"]} | '
            f'{effect["mean_delta_mae_minutes"]:+.3f} | '
            f'{effect["improved_seeds"]} | '
            f'{effect["seed_bootstrap_95pct_interval_minutes"] or "n/a"} |')
    lines += [
        "",
        "## Mid-build forecasts (one snapshot per build and progress phase)",
        "",
        "| Scenario | Reconstruct | Phase | Builds | Raw MAE | Calibrated MAE | Selected MAE | River selected |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in data["midbuild"]:
        scores = row["scores"]
        lines.append(
            f'| {row["scenario"]} | {row["reconstruct_historical_queue"]} | '
            f'{row["phase"]} | {row["builds_scored"]} | '
            f'{scores["scheduler_minutes"]["mae_minutes"]:.2f} | '
            f'{scores["calibrated_minutes"]["mae_minutes"]:.2f} | '
            f'{scores["predicted_minutes"]["mae_minutes"]:.2f} | '
            f'{scores["selected_river_builds"]} |')
    lines += [
        "",
        "Each phase uses the earliest prediction-time snapshot for each held-out",
        "build. Phase coverage differs by scenario; never pool with first forecasts.",
        "",
        "## Reconstruction comparison (same seed and workload)",
        "",
    ]
    for effect in data["reconstruction_comparisons"]:
        ci = effect["seed_bootstrap_95pct_interval_minutes"]
        lines.append(
            f'- {effect["scenario"]}: ON minus OFF selected MAE '
            f'{effect["mean_on_minus_off_mae_minutes"]:+.2f} min, '
            f'ON wins {effect["on_wins"]}/{effect["paired_seeds"]} seeds, '
            f'descriptive bootstrap interval {ci if ci is not None else "n/a (<3 seeds)"}.')
    lines += [
        "",
        "Reconstruction changes historical ML training exposure, not only feature values.",
        "",
        "## Existing trace runners: different task scopes",
        "",
    ]
    for name, result in data["existing_benchmarks"].items():
        lines.append(f'- {name}: {result["scope"]}, evaluated {result["evaluated"]} '
                     f'items, summary at {result["summary_path"]}.')
    lines += [
        "",
        "TravisTorrent: build-start total duration only; cannot test per-job queues.",
        "Pegasus: often workflow-start/makespan only; cannot test runtime VM contention.",
        "SWF: observed single-job waits; not multi-job build completion.",
        "Bundled CSV fixtures: synthetic smoke data, not real external evidence.",
        "",
        "## Caveats",
        "",
        "- Synthetic scheduling policy may favor the matching queue replay model.",
        "- No production traces, unobservable dependencies, or future job arrivals.",
        "- VM slot inference uses only *observed* historical overlap.",
        "- Pool slot estimates use prior 30-day overlap, refreshed every 10 virtual minutes.",
        "- Authoritative limits are supplied only in the declared-pool scenario.",
        "- The shadow model is only comparable against scheduler_on_shadow_subset.",
        "- Many seeds are needed before a bootstrap interval is informative.",
        "",
    ]
    return "\n".join(lines)


def validate(*, output, seeds, scenarios, history_builds, builds,
             warmup_builds, modes=(False, True), interval_seconds=900,
             min_training=30, include_fixture=True,
             wfcommons=None, swf=None, travistorrent=None,
             max_trace_plans=400, trace_warmup=20):
    if not seeds or len(set(seeds)) != len(seeds):
        raise ValueError("Provide distinct seeds")
    if not scenarios or any(scenario not in SCENARIOS for scenario in scenarios):
        raise ValueError("Unknown or missing scenario")
    if not modes or builds <= 1 or not 0 <= warmup_builds < builds:
        raise ValueError("Invalid modes, build count or warmup")
    if history_builds < 0 or interval_seconds <= 0 or min_training < 0:
        raise ValueError("Invalid history, snapshot interval or training threshold")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    run_rows = []
    grouped = defaultdict(list)
    grouped_phases = defaultdict(list)
    for name in scenarios:
        config = SCENARIOS[name]
        for seed in seeds:
            vms = make_vm_fleet(
                seed=seed, servers=2 if config.pool_mode else 1,
                vms_per_server=15 if config.pool_mode else 30,
                pool_mode=config.pool_mode)
            history_vms = dict(sorted(vms.items())[
                :max(1, int(len(vms) * config.historical_vm_fraction))])
            for reconstruct in modes:
                sim = Simulator(
                    seed=seed, history_builds=history_builds, builds=builds,
                    arrival_seconds=config.arrival_seconds,
                    min_jobs=config.min_jobs, max_jobs=config.max_jobs,
                    burst_size=round(builds * config.burst_fraction),
                    contention=config.contention,
                    history_contention=config.historical_contention,
                    history_vms=history_vms, vms=vms,
                    interval_seconds=interval_seconds,
                    reconstruct_historical_queue=reconstruct,
                    min_training=min_training,
                    declared_pool_capacity=config.declared_pool_capacity,
                )
                try:
                    original, records = sim.run()
                finally:
                    sim.close()
                all_first, held_out = first_predictions(records, warmup_builds)
                scores = score(held_out)
                phase_samples = midbuild_snapshots(
                    records, {r["plan_id"] for r in held_out})
                for snapshot in phase_samples:
                    grouped_phases[(name, reconstruct, snapshot["phase"])].append(
                        snapshot)
                mode = "reconstructed" if reconstruct else "snapshots_only"
                folder = output / "runs" / name / f"seed-{seed}" / mode
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "summary.json").write_text(
                    json.dumps(original, indent=2) + "\n", encoding="utf-8")
                write_csv(folder / "snapshots_by_phase.csv",
                          phase_samples,
                          ["plan_id", "predicted_at", "actual_finished_at",
                           "phase", "regime", "pool_capacity_source",
                           "pool_capacity_slots", "actual_minutes", "legacy_minutes",
                           "scheduler_minutes", "calibrated_minutes",
                           "predicted_minutes", "shadow_minutes",
                           "shadow_ready", "river_used"])
                write_csv(folder / "first_predictions.csv", [
                    {**row, "scored": index >= warmup_builds}
                    for index, row in enumerate(all_first)
                ], ["plan_id", "predicted_at", "actual_finished_at", "scored",
                    "pool_capacity_source", "pool_capacity_slots",
                    "actual_minutes", "legacy_minutes", "scheduler_minutes",
                    "calibrated_minutes", "predicted_minutes",
                    "shadow_minutes", "shadow_ready", "river_used"])
                grouped[(name, reconstruct)].extend(held_out)
                run_rows.append({
                    "scenario": name, "seed": seed,
                    "reconstruct_historical_queue": reconstruct,
                    "scored_builds": len(held_out),
                    "legacy_mae_minutes": scores["legacy_minutes"]["mae_minutes"],
                    "scheduler_mae_minutes": scores["scheduler_minutes"]["mae_minutes"],
                    "calibrated_mae_minutes": scores["calibrated_minutes"]["mae_minutes"],
                    "selected_mae_minutes": scores["predicted_minutes"]["mae_minutes"],
                    "selected_p90_minutes": scores["predicted_minutes"]["p90_abs_error_minutes"],
                    "shadow_ready_builds": scores["shadow_ready_builds"],
                    "selected_river_builds": scores["selected_river_builds"],
                    "wall_seconds": original["wall_seconds"],
                    "peak_active_builds": original["peak_active_builds"],
                    "peak_queued_jobs": original["peak_queued_jobs"],
                    "peak_running_jobs": original["peak_running_jobs"],
                })
                print(f'{name} seed={seed} reconstructed={reconstruct}: '
                      f'scheduler MAE={scores["scheduler_minutes"]["mae_minutes"]:.2f}, '
                      f'selected MAE={scores["predicted_minutes"]["mae_minutes"]:.2f}, '
                      f'wall={original["wall_seconds"]:.2f}s', flush=True)

    synthetic = []
    for (scenario, reconstructed), rows in sorted(grouped.items()):
        matches = [r for r in run_rows
                   if r["scenario"] == scenario
                   and r["reconstruct_historical_queue"] == reconstructed]
        synthetic.append({
            "scenario": scenario, "reconstruct_historical_queue": reconstructed,
            "seeds": len(matches), "scored_builds": len(rows),
            "scores": score(rows),
            "peak_active_builds_max": max(r["peak_active_builds"] for r in matches),
            "peak_queued_jobs_max": max(r["peak_queued_jobs"] for r in matches),
            "peak_running_jobs_max": max(r["peak_running_jobs"] for r in matches),
            "wall_seconds_total": round(sum(r["wall_seconds"] for r in matches), 3),
        })

    midbuild = [{
        "scenario": scenario, "reconstruct_historical_queue": mode,
        "phase": phase, "builds_scored": len(rows), "scores": score(rows),
    } for (scenario, mode, phase), rows in sorted(grouped_phases.items())
      if phase != "start"]

    by_pair = defaultdict(dict)
    for r in run_rows:
        by_pair[(r["scenario"], r["seed"])][r["reconstruct_historical_queue"]] = r
    reconstruction = []
    for scenario in scenarios:
        diffs = []
        for (name, _), modes_per_seed in by_pair.items():
            if name == scenario and True in modes_per_seed and False in modes_per_seed:
                on = modes_per_seed[True]
                off = modes_per_seed[False]
                if on["scored_builds"] != off["scored_builds"]:
                    raise RuntimeError("Mismatched holdout build counts across modes")
                diffs.append(on["selected_mae_minutes"] - off["selected_mae_minutes"])
        if diffs:
            reconstruction.append({
                "scenario": scenario, "paired_seeds": len(diffs),
                "mean_on_minus_off_mae_minutes": round(mean(diffs), 3),
                "on_wins": sum(value < 0 for value in diffs),
                "seed_bootstrap_95pct_interval_minutes": bootstrap_seed_delta(diffs),
            })

    existing = {}
    if include_fixture:
        folder = output / "existing" / "bundled_csv_fixture"
        root = Path(__file__).resolve().parents[1]
        existing["bundled_csv_fixture"] = run_existing_trace(
            "csv", root / "examples" / "benchmarks", folder, warmup=10)
    for name, fmt, path in (
        ("wfcommons_pegasus", "wfcommons-start", wfcommons),
        ("swf_single_job_queue", "swf", swf),
        ("travistorrent_build_start", "travistorrent", travistorrent),
    ):
        if path is not None:
            existing[name] = run_existing_trace(
                fmt, path, output / "existing" / name,
                max_plans=max_trace_plans, warmup=trace_warmup)

    result = {
        "schema": "vedrfolnir-validation-v1",
        "protocol": {
            "scored_at": "first chronological forecast after warmup",
            "seeds": list(seeds), "scenarios": list(scenarios),
            "history_builds": history_builds, "live_builds": builds,
            "warmup_builds": warmup_builds, "snapshot_interval_seconds": interval_seconds,
            "reconstruction_modes": list(modes),
            "labels": "only after job/build completion",
        },
        "synthetic": synthetic,
        "midbuild": midbuild,
        "model_effects": model_effects(run_rows),
        "reconstruction_comparisons": reconstruction,
        "existing_benchmarks": existing,
    }
    (output / "summary.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8")
    write_csv(output / "runs.csv", run_rows, list(run_rows[0]))
    (output / "report.md").write_text(markdown_report(result), encoding="utf-8")
    print(f'Validation report: {output / "report.md"}', flush=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true",
                        help="Small smoke run; not an accuracy claim")
    parser.add_argument("--output", default="benchmark-results/validation")
    parser.add_argument("--seeds", default=None, help="Comma-separated seed integers")
    parser.add_argument("--scenarios", default=None,
                        help="Comma-separated: steady,burst,overload,drift,multi_pool,multi_pool_declared,multi_pool_drift")
    parser.add_argument("--history-builds", type=int, default=None)
    parser.add_argument("--builds", type=int, default=None)
    parser.add_argument("--warmup-builds", type=int, default=None)
    parser.add_argument("--interval-seconds", type=int, default=900)
    parser.add_argument("--min-training", type=int, default=30)
    parser.add_argument("--modes", choices=("both", "reconstructed", "snapshots"),
                        default="both")
    parser.add_argument("--quality-gate", action="store_true",
                        help="Fail if selected ETA regresses by more than five percent on three or more seeds")
    parser.add_argument("--skip-fixture", action="store_true")
    parser.add_argument("--wfcommons", type=Path)
    parser.add_argument("--swf", type=Path)
    parser.add_argument("--travistorrent", type=Path)
    parser.add_argument("--max-trace-plans", type=int, default=400)
    parser.add_argument("--trace-warmup", type=int, default=20)
    args = parser.parse_args(argv)
    seeds = (args.seeds or ("42" if args.quick else "42,43,44")).split(",")
    scenarios = (args.scenarios or
                 ("steady,burst" if args.quick else "steady,burst,overload,drift")).split(",")
    modes = ((False, True) if args.modes == "both" else
             (True,) if args.modes == "reconstructed" else (False,))
    try:
        result = validate(
            output=args.output, seeds=[int(x.strip()) for x in seeds],
            scenarios=[x.strip() for x in scenarios],
            history_builds=args.history_builds if args.history_builds is not None
                           else 8 if args.quick else 150,
            builds=args.builds if args.builds is not None
                   else 14 if args.quick else 200,
            warmup_builds=args.warmup_builds if args.warmup_builds is not None
                          else 3 if args.quick else 40,
            modes=modes, interval_seconds=args.interval_seconds,
            min_training=min(args.min_training, 3) if args.quick else args.min_training,
            include_fixture=not args.skip_fixture,
            wfcommons=args.wfcommons, swf=args.swf, travistorrent=args.travistorrent,
            max_trace_plans=args.max_trace_plans, trace_warmup=args.trace_warmup,
        )
        if args.quality_gate:
            failures = regression_failures(result)
            if failures:
                parser.error("Quality gate failed: " + "; ".join(failures))
        return result
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
