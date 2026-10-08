"""Validation matrix protocol and reproducibility regression tests."""
import csv
import json
from datetime import datetime, timedelta, timezone

import pytest

from simulator.validation import (
    SCENARIOS, bootstrap_seed_delta, first_predictions, metrics,
    score, validate,
)


def row(plan_id, at, true=30, *, scheduler=20, shadow=None):
    return {
        "plan_id": plan_id,
        "predicted_at": at.isoformat(),
        "actual_minutes": true,
        "legacy_minutes": 40.0,
        "scheduler_minutes": scheduler,
        "calibrated_minutes": scheduler,
        "predicted_minutes": scheduler,
        "shadow_minutes": shadow,
        "shadow_ready": shadow is not None,
        "river_used": False,
    }


def test_warmup_uses_chronological_build_start_not_completion_order():
    at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = [
        row("late", at + timedelta(minutes=30)),
        row("early", at),
        row("early", at + timedelta(minutes=10), scheduler=21),
        row("mid", at + timedelta(minutes=2)),
    ]
    all_first, heldout = first_predictions(rows, warmup_builds=1)
    assert [x["plan_id"] for x in all_first] == ["early", "mid", "late"]
    assert [x["plan_id"] for x in heldout] == ["mid", "late"]
    assert all_first[0]["scheduler_minutes"] == 20


def test_matched_shadow_scoring_not_mixed_with_all_builds():
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = [
        row("a", now, true=10, scheduler=100, shadow=None),
        row("b", now, true=20, scheduler=30, shadow=21),
    ]
    scored = score(rows)
    assert scored["scheduler_minutes"]["mae_minutes"] == 50
    assert scored["scheduler_on_shadow_subset"]["mae_minutes"] == 10
    assert scored["shadow_minutes"]["mae_minutes"] == 1
    assert scored["shadow_ready_builds"] == 1
    assert scored["selected_river_builds"] == 0


def test_bootstrap_only_after_three_independent_seeds():
    assert bootstrap_seed_delta([]) is None
    assert bootstrap_seed_delta([-1, 2]) is None
    assert bootstrap_seed_delta([-2, -2, -2]) == [-2, -2]
    assert metrics([])["count"] == 0


def test_validation_smoke_creates_reproducible_summary_and_existing_fixture(tmp_path):
    options = dict(
        seeds=[42], scenarios=["steady", "burst"],
        history_builds=4, builds=7, warmup_builds=2, min_training=2,
        interval_seconds=1800, modes=(False, True), include_fixture=True,
    )
    results = validate(output=tmp_path / "first", **options)
    repeated = validate(output=tmp_path / "second", **options)
    assert results["protocol"] == repeated["protocol"]
    assert results["synthetic"] == [
        {**item, "wall_seconds_total": other["wall_seconds_total"]}
        for item, other in zip(repeated["synthetic"], results["synthetic"])
    ]
    assert len(results["synthetic"]) == 4
    assert len(results["reconstruction_comparisons"]) == 2
    assert results["midbuild"] or all(
        x["scored_builds"] == 5 for x in results["synthetic"])
    assert "Mid-build forecasts" in (
        tmp_path / "first" / "report.md").read_text()

    assert results["existing_benchmarks"]["bundled_csv_fixture"]["evaluated"] > 0
    assert (tmp_path / "first" / "report.md").exists()
    runs = list(csv.DictReader((tmp_path / "first" / "runs.csv").open()))
    assert len(runs) == 4
    report = json.loads((tmp_path / "first" / "summary.json").read_text())
    assert report["protocol"]["warmup_builds"] == 2
    for item in results["synthetic"]:
        assert item["scored_builds"] == 5
        assert item["scores"]["scheduler_minutes"]["count"] == 5
        assert item["scores"]["selected_river_builds"] == 0


def test_unknown_scenario_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unknown"):
        validate(output=tmp_path, seeds=[42], scenarios=["unseen"],
                 history_builds=1, builds=5, warmup_builds=1,
                 include_fixture=False)


def test_reconstruction_modes_preserve_ground_truth_timestamps(tmp_path):
    validate(output=tmp_path, seeds=[6], scenarios=["drift"],
             history_builds=6, builds=8, warmup_builds=2,
             min_training=2, modes=(False, True),
             include_fixture=False, interval_seconds=1800)
    root = tmp_path / "runs" / "drift" / "seed-6"
    def truth(mode):
        rows = list(csv.DictReader((root / mode / "first_predictions.csv").open()))
        return {r["plan_id"]: (r["predicted_at"], r["actual_finished_at"],
                               r["actual_minutes"]) for r in rows}
    assert truth("reconstructed") == truth("snapshots_only")


def test_midbuild_snapshots_include_at_most_one_per_build_phase():
    from simulator.validation import midbuild_snapshots
    at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = [
        {**row("a", at), "phase": "start"},
        {**row("a", at + timedelta(minutes=10)), "phase": "half"},
        {**row("a", at + timedelta(minutes=20)), "phase": "half"},
        {**row("b", at + timedelta(minutes=3)), "phase": "late"},
        {**row("c", at + timedelta(minutes=3)), "phase": "half"},
    ]
    result = midbuild_snapshots(rows, {"a", "b"})
    assert [(x["plan_id"], x["phase"]) for x in result] == [
        ("a", "start"), ("a", "half"), ("b", "late"),
    ]
    assert result[1]["predicted_at"] == (at + timedelta(minutes=10)).isoformat()


def test_default_drift_is_congested_not_an_idle_only_scenario():
    from simulator.scheduling import generate_schedule, make_vm_fleet, ZERO
    config = SCENARIOS["drift"]
    assert config.contention > config.historical_contention
    assert config.arrival_seconds < SCENARIOS["steady"].arrival_seconds
    builds = generate_schedule(100, prefix="driftload", seed=42, start=ZERO,
                               arrival_seconds=config.arrival_seconds,
                               min_jobs=config.min_jobs, max_jobs=config.max_jobs,
                               vms=make_vm_fleet(seed=42),
                               contention=config.contention,
                               burst_size=round(100 * config.burst_fraction))
    assert any(job.started_at > job.created_at for build in builds
               for job in build.jobs)



def test_paired_model_effects_use_distinct_seeds_and_signed_deltas():
    from simulator.validation import model_effects, regression_failures
    runs = [
        {
            "scenario": "overload", "seed": seed,
            "reconstruct_historical_queue": True,
            "scheduler_mae_minutes": 10.0,
            "calibrated_mae_minutes": 9.0,
            "selected_mae_minutes": 9.5,
        }
        for seed in (4, 7, 9)
    ]
    effects = model_effects(runs)
    assert len(effects) == 2
    assert all(effect["seed_count"] == 3 for effect in effects)
    assert effects[0]["mean_delta_mae_minutes"] == -1.0
    assert effects[0]["seed_bootstrap_95pct_interval_minutes"] == [-1.0, -1.0]
    assert effects[1]["improved_seeds"] == 3

    small = {
        "synthetic": [{
            "scenario": "overload", "reconstruct_historical_queue": True,
            "seeds": 3,
            "scores": {
                "scheduler_minutes": {
                    "mae_minutes": 10.0, "p90_abs_error_minutes": 20.0,
                },
                "predicted_minutes": {
                    "mae_minutes": 10.6, "p90_abs_error_minutes": 21.5,
                },
            },
        }],
    }
    assert len(regression_failures(small)) == 2
    small["synthetic"][0]["seeds"] = 2
    assert not regression_failures(small)
    small["synthetic"][0]["seeds"] = 3
    small["synthetic"][0]["scores"]["predicted_minutes"] = {
        "mae_minutes": 10.1, "p90_abs_error_minutes": 20.3,
    }
    assert not regression_failures(small)



def test_pool_scenarios_run_with_isolated_capacity_and_no_future_vm_leakage(tmp_path):
    from simulator.scheduling import make_vm_fleet, generate_schedule, ZERO
    vms = make_vm_fleet(seed=7, servers=2, vms_per_server=2, pool_mode=True)
    assert {vm.pool_id for vm in vms.values()} == {"server-01", "server-02"}
    builds = generate_schedule(
        5, prefix="multi", seed=7, start=ZERO, arrival_seconds=50,
        min_jobs=2, max_jobs=3, vms=vms, contention=0.1)
    for build in builds:
        for job in build.jobs:
            assert vms[job.vm_name].pool_id == job.sched_pool_id
    assert SCENARIOS["multi_pool"].pool_mode
    assert SCENARIOS["multi_pool_declared"].declared_pool_capacity

    from simulator.engine import Simulator
    observed = Simulator(
        seed=7, history_builds=5, builds=9, arrival_seconds=90,
        min_jobs=2, max_jobs=3, vms=vms,
        interval_seconds=1800, reconstruct_historical_queue=True)
    try:
        summary, rows = observed.run()
        assert summary["pool_ids"] == ["server-01", "server-02"]
        assert summary["scored_builds"] == 9
        assert all(row["pool_capacity_source"] in
                   ("observed", "unknown", "mixed", "declared") for row in rows)
    finally:
        observed.close()

    declared = Simulator(
        seed=7, history_builds=5, builds=9, arrival_seconds=90,
        min_jobs=2, max_jobs=3, vms=vms, declared_pool_capacity=True,
        interval_seconds=1800)
    try:
        summary, rows = declared.run()
        assert summary["declared_pool_capacity"]
        assert any(row["pool_capacity_source"] == "declared" for row in rows)
    finally:
        declared.close()


def test_validation_cli_help_is_valid_on_python_315(capsys):
    import pytest
    from simulator.validation import main
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "--quality-gate" in capsys.readouterr().out
