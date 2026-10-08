"""Integration smoke test (uses genuine River if installed, otherwise lightweight fake)."""
from datetime import timedelta
from dataclasses import replace
from sqlalchemy import create_engine, select, func
from vedrfolnir.config import settings
from vedrfolnir.db import create_session_factory, CurrentPlan, Prediction, TrainingMarker, utcnow

from vedrfolnir.demo import seed_demo
from vedrfolnir.service import EngineService


def test_demo_prediction_and_idempotent_training(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite+pysqlite:///{tmp_path/'test.sqlite'}")
    Session = create_session_factory(eng)
    assert seed_demo(Session)
    assert not seed_demo(Session)
    config = replace(settings, mode="demo", prediction_interval_seconds=300)
    svc = EngineService(Session, config)
    res = svc.tick()
    assert res["new_predictions"] == 6
    with Session.begin() as s:
        before = s.scalar(select(func.count(TrainingMarker.plan_id)))
        p = s.get(CurrentPlan, "demo-plan-01")
        p.status = "passed"
        finished = utcnow() + timedelta(minutes=30)
        p.finished_at = finished
    assert svc.tick()["trained_snapshots"] == 0
    with Session() as s:
        assert s.scalar(select(func.count(TrainingMarker.plan_id))) == before
        assert s.scalars(select(Prediction).where(Prediction.plan_id == "demo-plan-01")).first().actual_finished_at is None
    monkeypatch.setattr("vedrfolnir.service.utcnow", lambda: finished + timedelta(seconds=1))
    after_first = svc.tick()
    assert after_first["trained_snapshots"] == 1
    after_second = svc.tick()
    assert after_second["trained_snapshots"] == 0
    with Session() as s:
        assert s.scalar(select(func.count(TrainingMarker.plan_id))) == before + 1
        latest = s.scalars(select(Prediction).where(Prediction.plan_id == "demo-plan-01")).first()
        assert latest.error_minutes is not None
    eng.dispose()


def test_no_observed_vm_uses_uncorrected_heuristic_and_no_river(tmp_path):
    from vedrfolnir.db import CurrentJob
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path/'cold.sqlite'}")
    Session = create_session_factory(engine)
    config = replace(settings, mode="demo", min_training=0)
    svc = EngineService(Session, config)
    now = utcnow()
    with Session.begin() as db:
        db.add(CurrentPlan(plan_id="cold-plan", status="running",
                           started_at=now, priority=0))
        db.add(CurrentJob(run_id="cold-run", plan_id="cold-plan",
                          job_id="new-kind", vm_name="unknown",
                          status="queued", queued_at=now, priority=0))
    assert svc.forecast_active() == 1
    with Session() as db:
        prediction = db.scalars(
            select(Prediction).where(Prediction.plan_id == "cold-plan")
        ).one()
        assert prediction.features["__prediction_method"] == "historical_baseline"
        assert prediction.features["observed_vm_count"] == 0
        assert prediction.features["__scheduler_available"] is False
        assert "vm_inventory_unobserved" in prediction.features["__scheduler_fallback_reasons"]
        from vedrfolnir.api import to_json
        response = to_json(prediction)
        assert response["scheduler_available"] is False
        assert response["scheduler_fallback_reasons"] == prediction.features["__scheduler_fallback_reasons"]
        assert prediction.predicted_minutes == prediction.baseline_minutes
        assert prediction.used_model is False
    engine.dispose()
