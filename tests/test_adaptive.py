"""Observed-only scheduler calibration and shadow-validation regression tests."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from vedrfolnir.adaptive import (
    RESIDUAL_MODEL_ID, ObservedScheduler, ShadowResidualRegressor,
    residual_features, shadow_promotion,
)
from vedrfolnir.db import Base, HistoricalRun, ModelCheckpoint


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def historic(run_id, vm, start_minutes, end_minutes, *, finished_at=None):
    start = NOW + timedelta(minutes=start_minutes)
    end = NOW + timedelta(minutes=end_minutes)
    return HistoricalRun(
        run_id=run_id, job_id="compile", plan_id="old-build",
        vm_name=vm, status="passed", started_at=start,
        finished_at=finished_at or end,
        duration_seconds=(end - start).total_seconds(),
        version="unknown", db_type="unknown",
    )


def live_job(name, *, status="queued", priority=0, vm="unknown"):
    return SimpleNamespace(
        run_id=name, job_id="compile", plan_id="new-build",
        status=status, priority=priority, vm_name=vm,
        started_at=None, finished_at=None, queued_at=NOW,
        version="unknown", db_type="unknown",
    )


@pytest.mark.parametrize("capacity,expected", [
    (None, ["pool_capacity_unknown"]), (0, ["pool_paused"]), (2, []),
])
def test_pool_fallback_diagnostics_distinguish_unknown_paused_and_runnable(capacity, expected):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            job = live_job("queued")
            job.sched_pool_id = "builders"
            scheduler = ObservedScheduler(session, NOW, [job],
                                          declared_capacities={"builders": capacity})
            projected = scheduler.project([job])
            assert scheduler.plan_fallback_reasons([job], projected) == expected
            assert scheduler.can_estimate_plan([job], projected) == (not expected)
            assert scheduler.estimate_plan(NOW, [job], projected, 77) == (30 if capacity == 2 else 77)
    finally:
        engine.dispose()


def test_structural_and_incomplete_projection_diagnostics():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            session.add(historic("known", "vm-a", -10, -5))
            session.flush()
            scheduler = ObservedScheduler(session, NOW, [])
            assert scheduler.plan_fallback_reasons([], {}) == ["no_plan_jobs"]
            pending = live_job("blocked", status="pending")
            assert scheduler.plan_fallback_reasons([pending], {}) == ["no_remaining_jobs", "pending_jobs"]
            unknown = live_job("bad", status="unrecognized")
            assert scheduler.plan_fallback_reasons([unknown], {}) == ["no_remaining_jobs", "unsupported_job_status"]
            queued = live_job("queued")
            assert scheduler.plan_fallback_reasons([queued], {}) == ["projection_incomplete"]
            assert scheduler.plan_fallback_reasons([queued], scheduler.project([queued])) == []
    finally:
        engine.dispose()
def test_scheduler_infers_vm_inventory_parallel_slots_and_priorities():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            session.add_all([
                historic("one", "vm-a", -10, -6),
                historic("two", "vm-a", -9, -5),
                historic("three", "vm-a", -8, -4),
                historic("four", "vm-b", -3, -2),
                # A future outcome must never contribute to calibration.
                historic("future", "vm-unseen", 1, 2),
            ])
            session.flush()
            queue = [live_job("low"), live_job("highest", priority=3),
                     live_job("medium", priority=2), live_job("last")]
            scheduler = ObservedScheduler(session, NOW, queue)
            assert scheduler.vm_names == ("vm-a", "vm-b")
            assert scheduler.vm_slots["vm-a"] == 3
            assert scheduler.vm_slots["vm-b"] == 1
            projected = scheduler.project(queue)
            assert len(projected) == len(queue)
            assert all(value > NOW for value in projected.values())
            assert projected["highest"] <= projected["low"]
            assert scheduler.estimate_plan(NOW, queue, projected, 200) > 0
    finally:
        engine.dispose()


def test_scheduler_falls_back_when_no_known_runnable_jobs():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            pending = live_job("blocked", status="pending")
            scheduler = ObservedScheduler(session, NOW, [pending])
            assert scheduler.project([pending]) == {}
            assert scheduler.estimate_plan(NOW, [pending], {}, 41.5) == 41.5
            assert scheduler.estimate_plan(NOW, [], {}, 3.5) == 3.5
    finally:
        engine.dispose()


def test_shadow_promotion_requires_paired_out_of_sample_builds():
    good = [{"baseline": 30.0, "shadow": 21.0, "actual": 20.0}
            for _ in range(40)]
    assert not shadow_promotion(good[:19])
    assert shadow_promotion(good)
    poor = [{"baseline": 21.0, "shadow": 30.0, "actual": 20.0}
            for _ in range(40)]
    assert not shadow_promotion(poor)
    assert not shadow_promotion(good + poor * 6)
    # MAE improvement alone is insufficient when the tail regresses badly.
    tail = [{"baseline": 30.0, "shadow": 20.0, "actual": 20.0}
            for _ in range(17)]
    tail += [{"baseline": 30.0, "shadow": 80.0, "actual": 20.0}
             for _ in range(3)]
    assert not shadow_promotion(tail)


def test_river_residual_persistence_bounds_and_metadata_filter():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    features = {"plan_total_jobs": 3.0, "global_queue_count": 4.0,
                "__shadow_prediction_minutes": 10000000.0}
    try:
        with Session(engine) as session:
            regressor = ShadowResidualRegressor(session)
            assert regressor.estimate(features, 30.0, min_training=1) == (30.0, False)
            assert "__shadow_prediction_minutes" not in residual_features(features, 30.0)
            for _ in range(6):
                regressor.learn(features, 30.0, 24.0)
            regressor.save(session)
            session.commit()
        with Session(engine) as session:
            restored = ShadowResidualRegressor(session)
            assert restored.seen_count == 6
            corrected, ready = restored.estimate(features, 30.0, min_training=2)
            assert ready
            assert 15.0 <= corrected <= 60.0
            assert session.get(ModelCheckpoint, RESIDUAL_MODEL_ID) is not None
    finally:
        engine.dispose()


def test_overload_bias_correction_is_bounded_and_requires_previous_outcomes():
    from vedrfolnir.adaptive import load_regime, scheduler_bias_correction
    saturated = {
        "global_queue_count": 120.0, "observed_vm_slots": 30.0,
        "plan_completed_fraction": 0.0,
    }
    observations = [
        {"phase": "start", "regime": "saturated",
         "raw_scheduler": 20.0, "actual": 40.0}
        for _ in range(40)
    ]
    assert load_regime(saturated) == "saturated"
    assert scheduler_bias_correction(20, saturated, observations[:29]) == 20
    assert scheduler_bias_correction(20, saturated, observations) == 30
    assert scheduler_bias_correction(
        20, {**saturated, "global_queue_count": 2}, observations) == 20
    assert scheduler_bias_correction(20, saturated, [
        {**sample, "actual": 10} for sample in observations
    ]) == 20
    # The last 20 completed builds have shifted: withdraw the correction.
    abrupt_drift = observations[:20] + [
        {**sample, "actual": 10.0} for sample in observations[20:]
    ]
    assert scheduler_bias_correction(20, saturated, abrupt_drift) == 20


def test_shadow_promotion_is_specific_to_phase_and_queue_regime():
    historical = [
        {"phase": "start", "regime": "idle",
         "baseline": 30.0, "shadow": 20.0, "actual": 20.0}
        for _ in range(60)
    ]
    overloaded = [
        {"phase": "start", "regime": "saturated",
         "baseline": 20.0, "shadow": 40.0, "actual": 20.0}
        for _ in range(60)
    ]
    assert shadow_promotion(historical + overloaded, phase="start", regime="idle")
    assert not shadow_promotion(historical + overloaded,
                                phase="start", regime="saturated")
    assert not shadow_promotion(historical + overloaded,
                                phase="half", regime="idle")
    # Recent regression forces the model back to the scheduler.
    old = historical + [
        {"phase": "start", "regime": "idle",
         "baseline": 21.0, "shadow": 30.0, "actual": 20.0}
        for _ in range(20)
    ]
    assert not shadow_promotion(old, phase="start", regime="idle")


def test_unknown_vm_inventory_uses_heuristic_instead_of_fabricated_slot():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            queued = live_job("cold")
            scheduler = ObservedScheduler(session, NOW, [queued])
            assert scheduler.observed_vm_count == 0
            assert scheduler.observed_slot_count == 0
            projected = scheduler.project([queued])
            assert scheduler.can_estimate_plan([queued], projected) is False
            assert scheduler.estimate_plan(NOW, [queued], projected, 45.0) == 45.0
    finally:
        engine.dispose()


def test_unknown_job_status_does_not_silently_disappear_from_plan():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            session.add(historic("old", "vm-a", -30, -20))
            session.flush()
            queued = live_job("runnable")
            unknown = live_job("future", status="unrecognized")
            scheduler = ObservedScheduler(session, NOW, [queued, unknown])
            projected = scheduler.project([queued, unknown])
            assert scheduler.estimate_plan(
                NOW, [queued, unknown], projected, 60.0) == 60.0
    finally:
        engine.dispose()



def test_legacy_pool_default_does_not_activate_pool_scheduler():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            session.add(historic("old", "vm-a", -30, -20))
            session.flush()
            queued = live_job("next")
            scheduler = ObservedScheduler(session, NOW, [queued])
            assert scheduler.pool_aware is False
            assert scheduler.estimate_plan(
                NOW, [queued], scheduler.project([queued]), 500) < 500
    finally:
        engine.dispose()
