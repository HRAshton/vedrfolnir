"""Real CSV polls: capacity drift, pause/resume and rejected snapshots."""
import csv
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from vedrfolnir import api, service as service_module
from vedrfolnir.config import settings
from vedrfolnir.db import HistoricalRun, JobPrediction, create_session_factory, make_engine
from vedrfolnir.service import EngineService

NOW = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
FIELDS = ["run_id", "plan_id", "job_id", "status", "started_at", "finished_at",
          "queued_at", "vm_name", "sched_pool_id"]


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite:///{tmp_path / 'state.sqlite'}")
    factory = create_session_factory(engine)
    config = replace(settings, mode="files", csv_dir=str(tmp_path))
    svc = EngineService(factory, config)
    clock = [NOW]
    monkeypatch.setattr(service_module, "utcnow", lambda: clock[0])
    monkeypatch.setattr(api, "utcnow", lambda: clock[0])
    monkeypatch.setattr(api.app.state, "Session", factory, raising=False)
    monkeypatch.setattr(api.app.state, "service", svc, raising=False)
    with factory.begin() as s:
        for i in range(5):
            start = NOW-timedelta(minutes=100+i*20)
            s.add(HistoricalRun(run_id=f"old-{i}", job_id="compile", status="passed",
                                started_at=start, finished_at=start+timedelta(minutes=10),
                                duration_seconds=600, vm_name="worker", sched_pool_id="builders"))
    def job(name, status="queued", *, start=None, finish=None, queued=None):
        return {"run_id": name, "plan_id": None, "job_id": "compile", "status": status,
                "started_at": start, "finished_at": finish, "queued_at": queued,
                "vm_name": "worker" if start else "unknown", "sched_pool_id": "builders"}
    active = [job("running", "running", start=NOW-timedelta(minutes=4)),
              job("first", queued=NOW-timedelta(minutes=2)),
              job("second", queued=NOW-timedelta(minutes=1))]
    def write(name, fields, rows):
        with (tmp_path / f"{name}.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    def snapshot(slots, *, jobs=None, history=()):
        write("historical_jobs", FIELDS, history)
        write("live_jobs", FIELDS, active if jobs is None else jobs)
        write("plans", ["plan_id", "status", "started_at", "finished_at"], [])
        write("scheduler_pools", ["sched_pool_id", "sched_capacity_slots"],
              [{"sched_pool_id": "builders", "sched_capacity_slots": slots}])
    terminal = [job("running", "passed", start=NOW-timedelta(minutes=4), finish=NOW+timedelta(minutes=6)),
                job("first", "passed", start=NOW+timedelta(minutes=1), finish=NOW+timedelta(minutes=11)),
                job("second", "passed", start=NOW+timedelta(minutes=6), finish=NOW+timedelta(minutes=16))]
    client = TestClient(api.app)
    yield svc, factory, client, clock, snapshot, terminal, tmp_path
    client.close()
    engine.dispose()


def forecast(client, name="first"):
    return client.get(f"/api/jobs/{name}").json()["job"]["prediction"]


def test_capacity_increase_and_decrease_update_live_projection_without_rewriting_first_observation(deployment):
    svc, factory, client, clock, snapshot, terminal, _ = deployment
    snapshot(1)
    assert svc.tick()["job_observations"]["recorded"] == 3
    assert forecast(client)["predicted_wait_minutes"] == 6
    saved = client.get("/api/job-forecasts?run_id=first").json()[0]
    assert saved["capacity_evidence"]["capacity_slots"] == 1
    clock[0] = NOW+timedelta(minutes=1)
    snapshot(2)
    assert svc.tick()["job_observations"]["recorded"] == 0
    assert forecast(client)["predicted_wait_minutes"] == 0
    assert client.get("/api/job-forecasts?run_id=first").json()[0] == saved
    snapshot(1)
    svc.tick()
    assert forecast(client)["predicted_wait_minutes"] == 5
    snapshot(2, jobs=terminal, history=terminal)
    clock[0] = NOW+timedelta(minutes=17)
    assert svc.tick()["job_observations"]["matched"] == 3
    data = client.get("/api/job-metrics/by-workload?group_by=load").json()
    assert {c["group"] for c in data["cohorts"]} == {"saturated"}
    with factory() as s:
        queued = list(s.scalars(select(JobPrediction).where(JobPrediction.observed_status == "queued")))
        assert len(queued) == 2
        first = next(p for p in queued if p.run_id == "first")
        assert first.wait_error_minutes == 5  # Captured wait 6; observed wait 1.


def test_pause_resume_and_restart_preserve_unavailable_first_cohort(deployment, monkeypatch):
    svc, factory, client, clock, snapshot, terminal, _ = deployment
    snapshot(0)
    svc.tick()
    assert forecast(client)["unavailable_reasons"] == ["pool_paused"]
    assert forecast(client, "running")["forecast_available"] is True
    before = client.get("/api/job-forecasts").json()
    restarted = EngineService(factory, svc.config)
    monkeypatch.setattr(api.app.state, "service", restarted)
    clock[0] = NOW+timedelta(minutes=1)
    snapshot(2)
    assert restarted.tick()["job_observations"]["recorded"] == 0
    assert forecast(client)["forecast_available"] is True
    assert client.get("/api/job-forecasts").json() == before
    clock[0] = NOW+timedelta(minutes=17)
    snapshot(2, jobs=terminal, history=terminal)
    assert restarted.tick()["job_observations"]["matched"] == 3
    queued = next(c for c in client.get("/api/job-metrics/by-workload?group_by=load").json()["cohorts"]
                  if c["observed_status"] == "queued")
    assert queued["group"] == "paused"
    assert queued["evaluated_observations"] == 2 and queued["available_forecasts"] == 0
    assert queued["wait_error"]["count"] == queued["finish_error"]["count"] == 0
    assert queued["unavailable_reasons"] == {"pool_paused": 2}


def test_unknown_capacity_uses_observed_lower_bound_and_invalid_replacement_preserves_snapshot(deployment):
    svc, _, client, clock, snapshot, _, _ = deployment
    snapshot(None)
    svc.tick()
    response = client.get("/api/jobs").json()
    pool = next(p for p in response["pool_capacities"] if p["sched_pool_id"] == "builders")
    assert pool["source"] == "observed" and pool["capacity_slots"] == 1
    assert "capacity_inferred_not_declared" in forecast(client)["evidence_warnings"]
    stored = client.get("/api/job-forecasts").json()
    clock[0] = NOW+timedelta(minutes=1)
    snapshot(-1)
    with pytest.raises(ValueError):
        svc.tick()
    failed = client.get("/api/jobs").json()
    assert failed["source_observed_at"] == response["source_observed_at"]
    assert failed["data_source_sync"] == "error"
    assert forecast(client)["unavailable_reasons"] == ["source_error"]
    assert client.get("/api/job-forecasts").json() == stored
    snapshot(2)
    assert svc.tick()["job_observations"]["recorded"] == 0
    assert client.get("/api/jobs").json()["data_source_sync"] == "recent"
    assert forecast(client)["forecast_available"] is True


@pytest.mark.parametrize("removed", ["null", "missing_file"])
def test_removed_capacity_and_source_outage_do_not_keep_a_stale_declared_limit(deployment, removed):
    svc, _, client, clock, snapshot, _, source = deployment
    snapshot(2)
    svc.tick()
    clock[0] = NOW+timedelta(minutes=1)
    snapshot(None)
    if removed == "missing_file":
        (source / "scheduler_pools.csv").unlink()
    svc.tick()
    pool = next(p for p in client.get("/api/jobs").json()["pool_capacities"] if p["sched_pool_id"] == "builders")
    assert pool["source"] == "observed" and pool["capacity_slots"] == 1
    assert forecast(client)["predicted_wait_minutes"] == 5
    (source / "live_jobs.csv").unlink()
    with pytest.raises(FileNotFoundError):
        svc.tick()
    assert forecast(client)["unavailable_reasons"] == ["source_error"]
    clock[0] = NOW+timedelta(hours=1)
    svc.last_error = None  # Simulate a restarted worker with only its persisted clock.
    assert forecast(client)["unavailable_reasons"] == ["source_stale"]
