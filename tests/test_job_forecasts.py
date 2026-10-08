"""HTTP job ETA behavior with real queue competition and source freshness."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.pool import StaticPool

from vedrfolnir import api, service as service_module
from vedrfolnir.config import settings
from vedrfolnir.db import (
    HistoricalRun, ModelCheckpoint, Prediction, SyncState, create_session_factory,
)
from vedrfolnir.service import EngineService

NOW = datetime(2026, 3, 1, tzinfo=timezone.utc)


def row(name, status="queued", *, plan="own", pool="general", priority=0):
    return {"run_id": name, "plan_id": plan, "job_id": "compile", "status": status,
            "queued_at": NOW - timedelta(minutes=5) if status != "pending" else None,
            "started_at": NOW - timedelta(minutes=4) if status == "running" else None,
            "finished_at": None, "vm_name": "worker" if status == "running" else "unknown",
            "sched_pool_id": pool, "priority": priority}


@pytest.fixture
def environment(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    factory = create_session_factory(engine)
    config = replace(settings, mode="files", poll_seconds=120)
    svc = EngineService(factory, config)
    monkeypatch.setattr(api.app.state, "Session", factory, raising=False)
    monkeypatch.setattr(api.app.state, "service", svc, raising=False)
    monkeypatch.setattr(api, "utcnow", lambda: NOW)
    monkeypatch.setattr(service_module, "utcnow", lambda: NOW)
    with factory.begin() as s:
        for i in range(6):
            start = NOW - timedelta(minutes=100 + i * 20)
            s.add(HistoricalRun(run_id=f"old-{i}", plan_id="old", job_id="compile",
                                status="passed", started_at=start,
                                finished_at=start + timedelta(minutes=10),
                                duration_seconds=600, vm_name="worker"))

    def load(rows, pools=()):
        plans = [{"plan_id": name, "status": "running",
                  "started_at": NOW - timedelta(minutes=10), "finished_at": None}
                 for name in sorted({r["plan_id"] for r in rows})]
        svc.update_live(plans, rows, list(pools))

    client = TestClient(api.app)
    yield client, svc, factory, load
    client.close()
    engine.dispose()


def test_admitted_job_is_forecast_despite_pending_sibling_and_api_is_readonly(environment):
    client, svc, factory, load = environment
    load([row("running", "running", plan="external"), row("target/run"), row("later", "pending")])
    response = client.get("/api/jobs/target/run")
    assert response.status_code == 200
    body = response.json()
    prediction = body["job"]["prediction"]
    assert body["data_source_sync"] == "recent"
    assert prediction["forecast_available"] is True
    assert prediction["predicted_start_at"] == (NOW + timedelta(minutes=6)).isoformat()
    assert prediction["predicted_finish_at"] == (NOW + timedelta(minutes=16)).isoformat()
    assert prediction["predicted_wait_minutes"] == 6
    assert prediction["river_used"] is False
    assert prediction["p90_finish_at"] is None
    assert client.get("/api/jobs/later").json()["job"]["prediction"]["unavailable_reasons"] == ["pending_job"]
    assert svc.pool_cache.recalculations == 0
    with factory() as s:
        assert s.scalar(select(func.count(Prediction.id))) == 0
        assert s.scalar(select(func.count(ModelCheckpoint.name))) == 0
    svc.forecast_active()
    with factory() as s:
        whole = s.scalars(select(Prediction).where(Prediction.plan_id == "own")).one()
        assert whole.features["__scheduler_available"] is False
        assert "pending_jobs" in whole.features["__scheduler_fallback_reasons"]


def test_plan_filter_and_pagination_preserve_other_plans_competing_queue(environment):
    client, _, _, load = environment
    load([row("running", "running", plan="external"),
          row("ahead", plan="external", priority=2), row("target")])
    body = client.get("/api/jobs", params={"plan_id": "own", "limit": 1}).json()
    assert body["total_jobs"] == 1
    assert body["jobs"][0]["prediction"]["predicted_wait_minutes"] == 16
    assert client.get("/api/jobs", params={"offset": 3}).json()["jobs"] == []
    assert client.get("/api/jobs", params={"limit": 0}).status_code == 422
    assert client.get("/api/jobs/missing").status_code == 404


@pytest.mark.parametrize("capacity,reason", [(0, "pool_paused"), (None, "pool_capacity_unknown")])
def test_unknown_and_paused_pools_have_no_fabricated_job_deadlines(environment, capacity, reason):
    client, _, _, load = environment
    load([row("target", pool="never-observed")],
         [{"sched_pool_id": "never-observed", "sched_capacity_slots": capacity}])
    p = client.get("/api/jobs/target").json()["job"]["prediction"]
    assert p["forecast_available"] is False
    assert reason in p["unavailable_reasons"]
    assert p["predicted_start_at"] is p["predicted_finish_at"] is None


def test_running_job_uses_observed_start_even_if_pool_is_paused(environment):
    client, _, _, load = environment
    load([row("running", "running", pool="builders")],
         [{"sched_pool_id": "builders", "sched_capacity_slots": 0}])
    p = client.get("/api/jobs/running").json()["job"]["prediction"]
    assert p["forecast_available"] is True
    assert p["start_time_source"] == "observed"
    assert p["prediction_method"] == "historical_duration"
    assert p["predicted_start_at"] == (NOW - timedelta(minutes=4)).isoformat()
    assert p["predicted_wait_minutes"] == 0
    assert p["predicted_remaining_minutes"] == 6


@pytest.mark.parametrize("state,reason", [
    ("stale", "source_stale"), ("error", "source_error"),
    ("not_yet_synced", "source_not_yet_synced"),
])
def test_source_failures_withhold_etas_instead_of_refreshing_stale_work(environment, state, reason):
    client, svc, factory, load = environment
    load([row("target")])
    if state == "error":
        svc.last_error = "temporary upstream failure"
    else:
        with factory.begin() as s:
            if state == "stale":
                s.get(SyncState, "live_snapshot_at").value = (NOW - timedelta(hours=1)).isoformat()
            else:
                s.execute(delete(SyncState).where(SyncState.key == "live_snapshot_at"))
    body = client.get("/api/jobs/target").json()
    assert body["data_source_sync"] == state
    assert reason in body["job"]["prediction"]["unavailable_reasons"]
    assert body["job"]["prediction"]["predicted_finish_at"] is None


def test_persisted_snapshot_freshness_survives_process_restart(environment, monkeypatch):
    client, svc, factory, load = environment
    load([row("target")])
    restarted = EngineService(factory, svc.config)
    assert restarted.last_success is None
    monkeypatch.setattr(api.app.state, "service", restarted)
    assert client.get("/api/jobs/target").json()["data_source_sync"] == "recent"


def test_future_admissions_do_not_enter_queue_or_invent_capacity(environment):
    client, _, _, load = environment
    current = [row("running", "running", plan="external"), row("target")]
    load(current)
    before = client.get("/api/jobs/target").json()["job"]["prediction"]
    future = row("future", priority=100)
    future["queued_at"] = NOW + timedelta(hours=1)
    load(current + [future])
    assert client.get("/api/jobs/target").json()["job"]["prediction"] == before
    assert "queue_admission_in_future" in client.get("/api/jobs/future").json()["job"]["prediction"]["unavailable_reasons"]


def test_queue_admission_and_pool_affinity_limitations_are_explicit(environment):
    client, _, _, load = environment
    missing = row("missing")
    missing["queued_at"] = None
    pinned = row("pinned", pool="builders")
    pinned["vm_name"] = "worker"
    load([missing, pinned], [{"sched_pool_id": "builders", "sched_capacity_slots": 2}])
    assert "queue_admission_unknown" in client.get("/api/jobs/missing").json()["job"]["prediction"]["unavailable_reasons"]
    assert "pool_vm_affinity_unsupported" in client.get("/api/jobs/pinned").json()["job"]["prediction"]["unavailable_reasons"]


def test_unobservable_competitor_order_blocks_only_its_resource_pool(environment):
    client, _, _, load = environment
    unknown = row("unknown", pool="builders")
    unknown["queued_at"] = None
    load([unknown, row("affected", pool="builders"), row("separate", pool="other")],
         [{"sched_pool_id": "builders", "sched_capacity_slots": 1},
          {"sched_pool_id": "other", "sched_capacity_slots": 1}])
    affected = client.get("/api/jobs/affected").json()["job"]["prediction"]
    assert affected["unavailable_reasons"] == ["queue_order_unknown"]
    separate = client.get("/api/jobs/separate").json()
    assert separate["job"]["prediction"]["forecast_available"] is True
    assert separate["job"]["prediction"]["predicted_wait_minutes"] == 0
    capacities = {p["sched_pool_id"]: p for p in separate["pool_capacities"]}
    assert capacities["builders"]["capacity_slots"] == 1
    assert capacities["builders"]["queued_jobs"] == 2


def test_pool_affinity_restriction_on_competitor_prevents_false_dispatch_precision(environment):
    client, _, _, load = environment
    pinned = row("pinned", pool="builders")
    pinned["vm_name"] = "specific-worker"
    load([pinned, row("affected", pool="builders")],
         [{"sched_pool_id": "builders", "sched_capacity_slots": 2}])
    p = client.get("/api/jobs/affected").json()["job"]["prediction"]
    assert p["unavailable_reasons"] == ["pool_vm_affinity_unsupported"]
    assert p["predicted_finish_at"] is None


def test_individual_jobs_do_not_require_a_workflow_record(environment):
    client, svc, _, _ = environment
    svc.update_live([], [row("standalone", plan=None)])
    body = client.get("/api/jobs/standalone").json()
    assert body["job"]["plan_id"] is None
    assert body["job"]["prediction"]["forecast_available"] is True


def test_terminal_job_details_have_observed_times_without_a_new_forecast(environment):
    client, _, _, load = environment
    done = row("done", "passed")
    done["started_at"] = NOW - timedelta(minutes=15)
    done["queued_at"] = NOW - timedelta(minutes=20)
    done["finished_at"] = NOW - timedelta(minutes=1)
    load([done])
    assert client.get("/api/jobs").json()["jobs"] == []
    body = client.get("/api/jobs/done").json()["job"]
    assert body["finished_at"] == done["finished_at"].isoformat()
    assert body["prediction"]["unavailable_reasons"] == ["job_terminal"]
    assert body["prediction"]["predicted_finish_at"] is None


def test_rejected_source_snapshot_preserves_freshness_timestamp_and_prior_jobs(environment, monkeypatch):
    client, _, _, load = environment
    load([row("target")])
    before = client.get("/api/jobs/target").json()
    monkeypatch.setattr(service_module, "utcnow", lambda: NOW + timedelta(minutes=1))
    with pytest.raises(ValueError):
        load([row("corrupt", "invalid-status")])
    assert client.get("/api/jobs/target").json() == before
