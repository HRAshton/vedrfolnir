"""Determinism, data isolation and benchmark smoke coverage."""
import csv
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from simulator.cli import main, parse_vms
from simulator.engine import Simulator, generate_schedule, job_table, build_table
from simulator.scheduling import ZERO, VMConfig, make_vm_fleet
from vedrfolnir.db import HistoricalRun


def test_schedule_is_deterministic_and_vm_speed_is_hidden():
    a = generate_schedule(12, prefix="x", seed=42, start=ZERO,
                          arrival_seconds=30, min_jobs=2, max_jobs=3,
                          vms={"a": 1.5, "b": 0.5})
    b = generate_schedule(12, prefix="x", seed=42, start=ZERO,
                          arrival_seconds=30, min_jobs=2, max_jobs=3,
                          vms={"a": 1.5, "b": 0.5})
    assert [(p.plan_id, p.finished_at) for p in a] == [
        (p.plan_id, p.finished_at) for p in b
    ]
    assert all(j.created_at <= j.started_at < j.finished_at
               for p in a for j in p.jobs)


def test_simulator_trains_without_future_leak_and_cleans_live_sql():
    s = Simulator(seed=12, history_builds=8, builds=10, arrival_seconds=60,
                  min_jobs=2, max_jobs=3, interval_seconds=300,
                  min_training=2, reconstruct_historical_queue=True)
    try:
        summary, rows = s.run()
        assert summary["scored_builds"] == 10
        assert summary["learned_snapshots"] >= 10
        assert summary["shadow_ready_first_predictions"] > 0
        # Only 18 total builds: the shadow gating needs 20 *completed*
        # builds before River is allowed to affect selected predictions.
        assert summary["model_predictions_used"] == 0
        assert summary["errors"]["scheduler_minutes"]["mae_minutes"] is not None
        assert all(r["actual_finished_at"] > r["predicted_at"] for r in rows)
        with Session(s.engine) as session:
            assert session.execute(select(job_table)).first() is None
            assert session.execute(select(build_table)).first() is None
            assert len(session.scalars(select(HistoricalRun.run_id)).all()) == 8 * 0 + sum(
                len(b.jobs) for b in generate_schedule(8, prefix="history", seed=12,
                          start=ZERO, arrival_seconds=60, min_jobs=2, max_jobs=3,
                          vms=s.vms)
            ) + sum(len(b.jobs) for b in generate_schedule(10, prefix="live", seed=13,
                          start=ZERO, arrival_seconds=60, min_jobs=2, max_jobs=3,
                          vms=s.vms))
    finally:
        s.close()


def test_queue_reconstruction_flag_changes_historical_training():
    common = dict(seed=31, history_builds=8, builds=7, arrival_seconds=90,
                  interval_seconds=600, min_jobs=2, max_jobs=2, min_training=2)
    without = Simulator(**common, reconstruct_historical_queue=False)
    with_history = Simulator(**common, reconstruct_historical_queue=True)
    try:
        a, _ = without.run()
        b, _ = with_history.run()
        assert b["learned_snapshots"] > a["learned_snapshots"]
        assert a["scored_builds"] == b["scored_builds"] == 7
    finally:
        without.close()
        with_history.close()


def test_cli_creates_artifacts(tmp_path):
    target = tmp_path / "output"
    result = main(["--history-builds", "4", "--builds", "5",
                   "--min-jobs", "2", "--max-jobs", "2",
                   "--interval-seconds", "600", "--seed", "5",
                   "--output", str(target)])
    assert (target / "summary.json").is_file()
    assert (target / "predictions.csv").is_file()
    loaded = json.loads((target / "summary.json").read_text())
    assert loaded["scored_builds"] == result["scored_builds"] == 5
    with (target / "predictions.csv").open(newline="") as stream:
        assert len(list(csv.DictReader(stream))) >= 5
    assert parse_vms(["vm1:1.5:3", "vm2:0.7:1"]) == {
        "vm1": VMConfig(1.5, 3), "vm2": VMConfig(0.7, 1)
    }


def test_fleet_has_30_vms_per_server_and_1_to_3_slots():
    fleet = make_vm_fleet(seed=3, servers=2, vms_per_server=30)
    assert len(fleet) == 60
    assert {config.server for config in fleet.values()} == {"server-01", "server-02"}
    assert all(1 <= config.slots <= 3 for config in fleet.values())
    assert fleet == make_vm_fleet(seed=3, servers=2, vms_per_server=30)


def test_balancer_uses_all_idle_vms_before_sharing_any():
    fleet = make_vm_fleet(seed=14)
    builds = generate_schedule(120, prefix="burst", seed=14, start=ZERO,
                               arrival_seconds=120, min_jobs=2, max_jobs=2,
                               vms=fleet, burst_size=120)
    first = min(b.created_at for b in builds)
    immediate = [j for b in builds for j in b.jobs if j.started_at == first]
    # At least one job starts on each VM at the same time.
    assert {j.vm_name for j in immediate} == set(fleet)
    assert len(immediate) == sum(spec.slots for spec in fleet.values())
    assert max(j.finished_at for b in builds for j in b.jobs) > first


def test_vm_slot_limits_hold_during_100_build_burst():
    fleet = make_vm_fleet(seed=20)
    builds = generate_schedule(120, prefix="saturated", seed=20, start=ZERO,
                               arrival_seconds=180, min_jobs=3, max_jobs=3,
                               vms=fleet, burst_size=120)
    by_vm = {}
    for build in builds:
        for job in build.jobs:
            by_vm.setdefault(job.vm_name, []).extend((
                (job.started_at, 1), (job.finished_at, -1)))
    for vm, events in by_vm.items():
        running = 0
        for _, change in sorted(events):
            running += change
            assert 0 <= running <= fleet[vm].slots
        assert running == 0


def test_contention_changes_finish_times_but_not_initial_assignment():
    fleet = {"vm1": VMConfig(speed=1.0, slots=2)}
    kwargs = dict(count=2, prefix="load", seed=5, start=ZERO,
                  arrival_seconds=100, min_jobs=1, max_jobs=1,
                  vms=fleet, burst_size=2)
    plain = generate_schedule(**kwargs, contention=0)
    contention = generate_schedule(**kwargs, contention=0.15)
    clean_jobs = [b.jobs[0] for b in plain]
    loaded_jobs = [b.jobs[0] for b in contention]
    assert all(a.started_at == b.started_at for a, b in zip(clean_jobs, loaded_jobs))
    assert all(a.vm_name == b.vm_name == "vm1" for a, b in zip(clean_jobs, loaded_jobs))
    assert all(b.finished_at > a.finished_at for a, b in zip(clean_jobs, loaded_jobs))


def test_single_slot_preserves_no_contention_behavior():
    fleet = {"vm1": VMConfig(speed=1.0, slots=1)}
    kwargs = dict(count=3, prefix="single", seed=5, start=ZERO,
                  arrival_seconds=100, min_jobs=1, max_jobs=1,
                  vms=fleet, burst_size=3)
    a = generate_schedule(**kwargs, contention=0)
    b = generate_schedule(**kwargs, contention=0.15)
    assert [(x.jobs[0].started_at, x.finished_at) for x in a] == [
        (x.jobs[0].started_at, x.finished_at) for x in b
    ]


def test_stress_reports_peaks_and_runtime():
    s = Simulator(seed=11, history_builds=5, builds=130, burst_size=130,
                  arrival_seconds=90, min_jobs=2, max_jobs=2,
                  interval_seconds=3600, min_training=2)
    try:
        summary, _ = s.run()
        assert summary["vm_count"] == 30
        assert summary["peak_active_builds"] >= 100
        assert summary["peak_running_jobs"] >= 30
        assert summary["peak_running_jobs_on_vm"] <= 3
        assert summary["peak_queued_jobs"] > 0
        assert summary["wall_seconds"] >= 0
        assert summary["scored_builds"] == 130
    finally:
        s.close()


def test_queued_jobs_do_not_reveal_future_vm_assignments():
    s = Simulator(seed=3, history_builds=0, builds=4, burst_size=4,
                  min_jobs=1, max_jobs=1, interval_seconds=3600,
                  vms={"single": VMConfig(1.0, 1)})
    observed_queued = []
    original = s._forecast

    def inspect(session, now, *, only=None):
        rows = session.execute(
            select(job_table.c.status, job_table.c.vm_name)
        ).all()
        for status, vm in rows:
            if status == "queued":
                observed_queued.append(vm)
                assert vm is None  # Assignment is future until job_start.
            elif status == "running":
                assert vm == "single"
        return original(session, now, only=only)

    s._forecast = inspect
    try:
        summary, _ = s.run()
        assert summary["scored_builds"] == 4
        assert observed_queued
    finally:
        s.close()
