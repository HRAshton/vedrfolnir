"""Cohort isolation, bounded samples and prediction-time load evidence."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from vedrfolnir import api, job_monitoring
from vedrfolnir.db import JobPrediction, CurrentJob, create_session_factory

NOW = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)


def outcome(name, *, pool="builders", kind="compile", observed="queued", available=True,
            error=1, status="passed", issue=None, evidence=None, finished=NOW):
    return JobPrediction(
        run_id=name, job_id=kind, sched_pool_id=pool, observed_status=observed,
        predicted_at=NOW-timedelta(hours=1), source_observed_at=NOW-timedelta(hours=1),
        forecast_available=available, prediction_method="queue_scheduler" if available else None,
        predicted_wait_minutes=2 if available else None, predicted_remaining_minutes=12 if available else None,
        unavailable_reasons=[] if available else ["pool_paused"], evidence_warnings=[],
        capacity_evidence=evidence or {}, actual_started_at=NOW-timedelta(minutes=10),
        actual_finished_at=finished, outcome_status=status, outcome_issue=issue,
        wait_error_minutes=error if available and observed == "queued" and status == "passed" and not issue else None,
        finish_error_minutes=error if available and status == "passed" and not issue else None)


@pytest.fixture
def sample(monkeypatch):
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    factory = create_session_factory(engine)
    monkeypatch.setattr(api.app.state, "Session", factory, raising=False)
    with factory.begin() as s:
        s.add_all([
            outcome("small", error=-2, evidence={"capacity_slots": 2, "queued_jobs": 0}),
            outcome("large", pool="android", kind="test", error=20,
                    evidence={"capacity_slots": 1, "queued_jobs": 2}),
            outcome("unavailable", available=False, evidence={"capacity_slots": 0, "queued_jobs": 2}),
            outcome("cancelled", status="cancelled"),
            outcome("invalid", issue="completion_before_forecast"),
            outcome("running", observed="running", error=3),
            outcome("pending", observed="pending", available=False),
        ])
    client = TestClient(api.app)
    yield factory, client
    client.close()
    engine.dispose()


def test_pool_and_job_type_breakdowns_keep_status_targets_and_exclusions_separate(sample):
    factory, client = sample
    for dimension, left, right in (("pool", "builders", "android"), ("job_type", "compile", "test")):
        data = client.get("/api/job-metrics/by-workload", params={"group_by": dimension}).json()
        cohorts = {(c["observed_status"], c["group"]): c for c in data["cohorts"]}
        small = cohorts[("queued", left)]
        assert small["completed_observations"] == 4
        assert small["evaluated_observations"] == 2
        assert small["available_forecasts"] == 1
        assert small["excluded_outcomes"] == 2
        assert small["unavailable_reasons"] == {"pool_paused": 1}
        assert small["wait_error"]["count"] == 1
        assert small["finish_error"]["signed_bias_minutes"] == -2
        assert cohorts[("queued", right)]["finish_error"]["mae_minutes"] == 20
        assert cohorts[("running", left)]["wait_error"]["count"] == 0
        assert cohorts[("running", left)]["finish_error"]["mae_minutes"] == 3
        assert cohorts[("pending", left)]["finish_error"]["count"] == 0
        with factory() as s:
            headline = job_monitoring.summarize_jobs(s, 90)["cohorts"]
        for row in headline:
            members = [c for c in data["cohorts"] if c["observed_status"] == row["observed_status"]]
            for key in ("completed_observations", "evaluated_observations", "available_forecasts", "excluded_outcomes"):
                assert sum(c[key] for c in members) == row[key]


def test_load_breakdowns_use_saved_evidence_and_legacy_missing_evidence_is_unknown(sample):
    factory, client = sample
    before = client.get("/api/job-metrics/by-workload?group_by=load").json()
    cohorts = {(c["observed_status"], c["group"]): c for c in before["cohorts"]}
    assert cohorts[("queued", "idle")]["wait_error"]["signed_bias_minutes"] == -2
    assert cohorts[("queued", "saturated")]["finish_error"]["mae_minutes"] == 20
    assert cohorts[("queued", "paused")]["available_forecasts"] == 0
    assert cohorts[("running", "unknown")]["finish_error"]["count"] == 1
    with factory.begin() as s:
        s.add(CurrentJob(run_id="today", job_id="compile", sched_pool_id="builders", status="queued"))
    assert client.get("/api/job-metrics/by-workload?group_by=load").json() == before
    assert client.get("/api/job-metrics/by-workload?group_by=unsupported").status_code == 422


@pytest.mark.parametrize("slots,queued,expected", [
    (None, 2, "unknown"), (0, 2, "paused"), (4, 1, "idle"), (4, 2, "busy"),
    (2, 3, "busy"), (2, 4, "saturated"), (1, None, "unknown"), (-1, 3, "unknown"),
    (True, 2, "unknown"), (2, float("nan"), "unknown"), (float("inf"), 2, "unknown"),
])
def test_load_boundaries_and_invalid_evidence(slots, queued, expected):
    assert job_monitoring.observed_pool_load({"capacity_slots": slots, "queued_jobs": queued}) == expected
    assert job_monitoring.observed_pool_load(None) == "unknown"


def test_grouping_uses_one_global_bounded_sample_per_status_without_filling_old_groups(sample, monkeypatch):
    factory, client = sample
    monkeypatch.setattr(job_monitoring, "SAMPLE_LIMIT", 2)
    with factory.begin() as s:
        s.add_all([outcome("new-a", pool="new", finished=NOW+timedelta(minutes=1)),
                   outcome("new-b", pool="new", finished=NOW+timedelta(minutes=2))])
    data = client.get("/api/job-metrics/by-workload").json()
    queued = [c for c in data["cohorts"] if c["observed_status"] == "queued"]
    assert data["sample_limit_per_status"] == 2
    assert len(queued) == 1 and queued[0]["group"] == "new"
    assert queued[0]["completed_observations"] == 2
