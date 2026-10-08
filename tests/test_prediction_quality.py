"""API exposure of current ML state and ETA evidence flags."""
from datetime import timedelta
from types import SimpleNamespace
from sqlalchemy import create_engine
from vedrfolnir.db import Base, ModelCheckpoint, create_session_factory, utcnow
from vedrfolnir.adaptive import RESIDUAL_MODEL_ID
from vedrfolnir.prediction_quality import prediction_evidence


def test_evidence_is_diagnostic_not_fabricated_confidence():
    cold = prediction_evidence(
        {"__prediction_method": "historical_baseline",
         "observed_vm_count": 0,
         "plan_active_jobs_without_matching_history": 2},
        p90_available=False,
    )
    assert cold["level"] == "limited"
    assert cold["method"] == "historical_baseline"
    assert "vm_inventory_unobserved" in cold["warnings"]
    assert "job_duration_history_scarce" in cold["warnings"]
    assert "p90_not_yet_calibrated" in cold["warnings"]
    warm = prediction_evidence(
        {"__prediction_method": "queue_scheduler", "observed_vm_count": 10,
         "plan_active_jobs_without_matching_history": 0},
        p90_available=True,
    )
    assert warm == {"method": "queue_scheduler", "level": "observational",
                    "warnings": []}
    assert prediction_evidence(
        {"__prediction_method": "queue_scheduler", "observed_vm_count": 5,
         "__pressure_regime": "saturated"},
        river_used=True, p90_available=True,
    )["method"] == "river_residual_correction"


def test_status_uses_active_residual_checkpoint_not_obsolete_plan_model(monkeypatch):
    from vedrfolnir import api
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Session = create_session_factory(engine)
    with Session.begin() as db:
        db.add(ModelCheckpoint(name=RESIDUAL_MODEL_ID,
                               payload=b"fake-checkpoint-not-loaded", seen_count=17))
        db.add(ModelCheckpoint(name="plan_eta_river_v1",
                               payload=b"old", seen_count=999))
    service = SimpleNamespace(last_error=None, last_success=utcnow().isoformat(),
                              last_reconciled=0,
                              last_retention={"historical_runs": 0, "plan_predictions": 0})
    monkeypatch.setattr(api.app.state, "Session", Session, raising=False)
    monkeypatch.setattr(api.app.state, "service", service, raising=False)
    result = api.status()
    assert result["river_training_snapshots"] == 17
    assert result["data_source_sync"] == "recent"
    service.last_success = (utcnow() - timedelta(days=1)).isoformat()
    assert api.status()["data_source_sync"] == "stale"
    service.last_error = "sync failed"
    assert api.status()["data_source_sync"] == "error"
    engine.dispose()



def test_pool_observation_warns_without_claiming_true_capacity():
    observed = prediction_evidence(
        {"__prediction_method": "queue_scheduler",
         "__pool_capacity_source": "observed",
         "__pool_ids": ["mssql"],
         "observed_vm_count": 10},
        p90_available=True,
    )
    assert "pool_capacity_inferred_not_declared" in observed["warnings"]
    assert observed["level"] == "limited"
    declared = prediction_evidence(
        {"__prediction_method": "queue_scheduler",
         "__pool_capacity_source": "declared",
         "__pool_ids": ["mssql"],
         "observed_vm_count": 10},
        p90_available=True,
    )
    assert "pool_capacity_inferred_not_declared" not in declared["warnings"]


def test_pool_aware_load_regime_uses_relevant_pool_not_all_vms():
    from vedrfolnir.adaptive import load_regime
    assert load_regime({
        "global_queue_count": 3,
        "observed_vm_slots": 200,
        "pool_queue_pressure": 5.0,
    }) == "saturated"
    assert load_regime({
        "global_queue_count": 200,
        "observed_vm_slots": 3,
        "pool_queue_pressure": 0.1,
    }) == "idle"


def test_readonly_pool_endpoint_exposes_lower_bound_vs_declaration(monkeypatch):
    from dataclasses import replace
    from sqlalchemy import create_engine
    from vedrfolnir.config import settings
    from vedrfolnir.db import (
        CurrentJob, SchedulerPool, create_session_factory)
    from vedrfolnir.service import EngineService
    from vedrfolnir import api

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Session = create_session_factory(engine)
    service = EngineService(Session, replace(settings, mode="demo"))
    with Session.begin() as db:
        db.add(SchedulerPool(sched_pool_id="ios", sched_capacity_slots=0))
        db.add(CurrentJob(
            run_id="job", plan_id="plan", job_id="ui",
            sched_pool_id="ios", status="queued", priority=0,
        ))
    monkeypatch.setattr(api.app.state, "Session", Session, raising=False)
    monkeypatch.setattr(api.app.state, "service", service, raising=False)
    response = api.pools()
    pool = next(x for x in response["pools"] if x["sched_pool_id"] == "ios")
    assert pool["capacity_source"] == "declared"
    assert pool["capacity_slots"] == 0
    assert pool["observed_capacity_slots"] == 0
    engine.dispose()


def test_old_forecasts_keep_unknown_scheduler_diagnostics():
    from vedrfolnir.api import to_json
    from vedrfolnir.db import Prediction
    old = Prediction(id=1, plan_id="old", predicted_at=utcnow(),
                     baseline_minutes=10, predicted_minutes=10, features={}, used_model=False)
    response = to_json(old)
    assert response["scheduler_available"] is None
    assert response["scheduler_fallback_reasons"] is None
