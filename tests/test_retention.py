"""Aging policy: bounded deletion without destroying training or late-label evidence."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from vedrfolnir.config import settings
from vedrfolnir.db import (
    CurrentJob, CurrentPlan, HistoricalRun, JobPrediction, Prediction,
    TrainingMarker, create_session_factory, make_engine,
)
from vedrfolnir.retention import prune_storage

NOW = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
OLD = NOW - timedelta(days=400)
VERY_OLD = NOW - timedelta(days=800)
NEW = NOW - timedelta(days=3)


def history(run, finish):
    return HistoricalRun(run_id=run, plan_id="build", job_id="compile",
                         status="passed", started_at=finish - timedelta(minutes=10),
                         finished_at=finish, duration_seconds=600)


def prediction(plan, predicted, *, actual=None):
    return Prediction(
        plan_id=plan, predicted_at=predicted, phase="start",
        model_version="test", used_model=False, baseline_minutes=10,
        predicted_minutes=10, p90_minutes=None, features={},
        actual_finished_at=actual,
        actual_remaining_minutes=10 if actual else None,
        error_minutes=0 if actual else None,
        outcome_status="passed" if actual else None,
    )


@pytest.fixture
def db(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'retention.sqlite3'}")
    factory = create_session_factory(engine)
    yield factory
    engine.dispose()


def test_bounded_pruning_preserves_training_active_and_unresolved_labels(db):
    config = replace(settings, history_retention_days=365,
                     plan_retention_days=365,
                     plan_unresolved_retention_days=730,
                     retention_batch=1)
    with db.begin() as s:
        for name in ("old-drop", "old-drop-two", "old-active", "old-unmatched"):
            s.add(history(name, OLD))
        s.add(history("new-keep", NEW))
        no_plan = history("old-without-plan", OLD)
        no_plan.plan_id = None
        s.add(no_plan)
        active_history = history("old-active-plan", OLD)
        active_history.plan_id = "active"
        s.add(active_history)
        s.add(CurrentJob(run_id="old-active", plan_id="active",
                         job_id="compile", status="running", started_at=OLD,
                         priority=0))
        s.add(CurrentPlan(plan_id="active", status="running",
                          started_at=OLD, priority=0))
        s.add(JobPrediction(
            run_id="old-unmatched", plan_id="p", job_id="compile",
            sched_pool_id="general", observed_status="queued",
            predicted_at=OLD, forecast_available=False,
            unavailable_reasons=[], evidence_warnings=[], capacity_evidence={},
        ))
        linked = prediction("trained", OLD, actual=OLD)
        s.add(linked)
        s.flush()
        s.add(TrainingMarker(plan_id="trained", phase="start",
                             prediction_id=linked.id))
        s.add(prediction("evaluated", OLD, actual=OLD))
        s.add(prediction("evaluated-2", OLD, actual=OLD))
        s.add(prediction("active", VERY_OLD))
        s.add(prediction("unresolved-old", VERY_OLD))
        s.add(prediction("unresolved-recent", OLD))
    with db.begin() as s:
        result = prune_storage(s, config, NOW)
        assert result == {"historical_runs": 1, "plan_predictions": 1}
    # Each tick removes at most one history and one plan row. Repeated ticks
    # eventually bound the eligible bulk while retaining pinned training data.
    for _ in range(4):
        with db.begin() as s:
            prune_storage(s, config, NOW)
    with db() as s:
        assert set(s.scalars(select(HistoricalRun.run_id))) == {
            "old-active", "old-active-plan", "old-unmatched", "new-keep",
        }
        names = set(s.scalars(select(Prediction.plan_id)))
        assert names == {
            "trained", "active", "unresolved-recent",
        }
        assert s.get(TrainingMarker, ("trained", "start")) is not None


def test_retentions_are_validated_against_overlap():
    with pytest.raises(ValueError, match="history_retention_days"):
        replace(settings, history_reconcile_days=7,
                history_retention_days=7).validate()
    with pytest.raises(ValueError, match="history_reconcile_days"):
        replace(settings, history_reconcile_days=-1).validate()
    with pytest.raises(ValueError, match="retention_batch"):
        replace(settings, retention_batch=0).validate()
    replace(settings, history_reconcile_days=0,
            history_retention_days=1).validate()


def test_import_aligned_history_cleanup_keeps_protected_runs_and_plan_cap(db):
    config = replace(settings, history_retention_days=365,
                     plan_retention_days=365, retention_batch=2)
    with db.begin() as session:
        for n in range(20):
            session.add(history(f"expired-{n:03d}", OLD))
        session.add(history("protected-job", OLD))
        session.add(CurrentJob(run_id="protected-job", plan_id="active",
                               job_id="compile", status="running", started_at=OLD,
                               priority=0))
        for i in range(5):
            session.add(prediction(f"old-plan-{i}", OLD, actual=OLD))
    with db.begin() as session:
        removed = prune_storage(session, config, NOW, imported_history=20)
        assert removed == {"historical_runs": 20, "plan_predictions": 2}
    with db() as session:
        assert set(session.scalars(select(HistoricalRun.run_id))) == {"protected-job"}
        assert len(list(session.scalars(select(Prediction.id)))) == 3
