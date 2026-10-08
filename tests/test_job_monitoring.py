"""Prospective recording, delayed labels, restart safety and monitor APIs."""
import csv
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import io

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.pool import StaticPool

from vedrfolnir import api
from vedrfolnir.config import settings
from vedrfolnir.db import CurrentJob, HistoricalRun, JobPrediction, create_session_factory
from vedrfolnir.job_monitoring import record_and_match, summarize_jobs, daily_job_errors
from vedrfolnir.service import EngineService

NOW = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path/'monitor.sqlite'}", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    factory = create_session_factory(engine)
    config = replace(settings, mode="files", job_retention_days=90)
    service = EngineService(factory, config)
    monkeypatch.setattr(api.app.state, "Session", factory, raising=False)
    monkeypatch.setattr(api.app.state, "service", service, raising=False)
    monkeypatch.setattr(api, "utcnow", lambda: NOW)
    def live(name="target", status="queued", *, at=NOW):
        import vedrfolnir.service as module
        monkeypatch.setattr(module, "utcnow", lambda: at)
        service.update_live([], [{"run_id": name, "plan_id": None, "job_id": "compile", "status": status,
                                 "queued_at": NOW - timedelta(minutes=1),
                                 "started_at": at - timedelta(minutes=2) if status == "running" else None,
                                 "finished_at": None, "vm_name": "worker" if status == "running" else "unknown",
                                 "sched_pool_id": "builders"}],
                            [{"sched_pool_id": "builders", "sched_capacity_slots": 1}])
    def observe(at=NOW):
        with factory.begin() as s:
            return record_and_match(s, config, at)
    client = TestClient(api.app)
    yield factory, config, live, observe, client
    client.close()
    engine.dispose()


def complete(factory, *, name="target", start=NOW+timedelta(minutes=4),
             finish=NOW+timedelta(minutes=14), status="passed", kind="compile"):
    with factory.begin() as s:
        s.add(HistoricalRun(run_id=name, job_id=kind, plan_id=None, status=status,
                            started_at=start, finished_at=finish, duration_seconds=(finish-start).total_seconds()))


def test_first_forecast_is_saved_once_and_labels_wait_for_completion(env):
    factory, _, live, observe, client = env
    live()
    assert observe()["recorded"] == 1
    assert observe()["recorded"] == 0
    complete(factory)
    assert observe(NOW+timedelta(minutes=10))["matched"] == 0
    assert client.get("/api/job-metrics").json()["awaiting_outcomes"] == 1
    assert observe(NOW+timedelta(minutes=15))["matched"] == 1
    assert observe(NOW+timedelta(minutes=16))["matched"] == 0
    with factory() as s:
        prediction = s.scalars(select(JobPrediction)).one()
        assert prediction.predicted_wait_minutes == 0
        assert prediction.wait_error_minutes == -4
        assert prediction.finish_error_minutes == 16
        assert prediction.outcome_status == "passed"
    cohort = client.get("/api/job-metrics").json()["cohorts"][0]
    assert cohort["wait_error"]["mae_minutes"] == 4
    assert cohort["finish_error"]["signed_bias_minutes"] == 16
    trend = client.get("/api/job-metrics/daily", params={"days": 3}).json()["rows"]
    assert trend[-1]["queued"]["wait_error"]["count"] == 1


def test_restart_preserves_first_forecast_and_each_status_has_its_own_target(env):
    factory, config, live, observe, client = env
    live()
    observe()
    EngineService(factory, config)
    assert observe()["recorded"] == 0
    live(status="running", at=NOW+timedelta(minutes=6))
    assert observe(NOW+timedelta(minutes=6))["recorded"] == 1
    assert {p["observed_status"] for p in client.get("/api/job-forecasts").json()} == {"queued", "running"}
    complete(factory)
    observe(NOW+timedelta(minutes=15))
    with factory() as s:
        records = list(s.scalars(select(JobPrediction)))
        assert len(records) == 2
        assert next(p for p in records if p.observed_status == "running").wait_error_minutes is None


def test_missing_admission_is_not_replaced_by_a_later_available_first_sample(env):
    factory, _, live, observe, client = env
    live()
    with factory.begin() as s:
        s.get(CurrentJob, "target").queued_at = None
    observe()
    live(at=NOW+timedelta(minutes=1))
    assert observe(NOW+timedelta(minutes=1))["recorded"] == 0
    complete(factory)
    observe(NOW+timedelta(minutes=15))
    cohort = client.get("/api/job-metrics").json()["cohorts"][0]
    assert cohort["evaluated_observations"] == 1
    assert cohort["available_forecasts"] == 0
    assert cohort["finish_error"]["count"] == 0
    assert cohort["unavailable_reasons"] == {"queue_admission_unknown": 1}


@pytest.mark.parametrize("status", ["cancelled", "skipped"])
def test_administrative_outcomes_are_labelled_but_not_scored(env, status):
    factory, _, live, observe, client = env
    live()
    observe()
    complete(factory, status=status)
    observe(NOW+timedelta(minutes=15))
    cohort = client.get("/api/job-metrics").json()["cohorts"][0]
    assert cohort["excluded_outcomes"] == 1
    assert cohort["finish_error"]["count"] == 0


def test_bad_outcome_clocks_are_visible_instead_of_becoming_silent_pending_forecasts(env):
    factory, _, live, observe, client = env
    live()
    observe()
    complete(factory, start=NOW-timedelta(minutes=10), finish=NOW-timedelta(minutes=1))
    observe(NOW+timedelta(minutes=1))
    metrics = client.get("/api/job-metrics").json()
    assert metrics["awaiting_outcomes"] == 0
    assert metrics["label_issues"] == 1
    assert metrics["cohorts"][0]["finish_error"]["count"] == 0
    assert client.get("/api/job-forecasts").json()[0]["outcome_issue"] == "completion_before_forecast"


def test_stale_queued_status_is_excluded_when_actual_start_precedes_capture(env):
    factory, _, live, observe, client = env
    live()
    observe()
    complete(factory, start=NOW-timedelta(minutes=2))
    observe(NOW+timedelta(minutes=15))
    assert client.get("/api/job-forecasts").json()[0]["outcome_issue"] == "start_before_queued_forecast"
    assert client.get("/api/job-metrics").json()["cohorts"][0]["wait_error"]["count"] == 0


def test_retention_protects_active_jobs_and_recently_completed_long_jobs(env):
    factory, config, live, observe, _ = env
    live()
    observe()
    with factory.begin() as s:
        row = s.scalars(select(JobPrediction)).one()
        row.predicted_at = NOW-timedelta(days=100)
        assert record_and_match(s, config, NOW)["pruned"] == 0
        row.actual_finished_at = NOW
        row.outcome_status = "passed"
        s.execute(delete(CurrentJob))
        assert record_and_match(s, config, NOW)["pruned"] == 0
        assert record_and_match(s, config, NOW+timedelta(days=91))["pruned"] == 1


def test_export_is_readonly_and_safe_for_spreadsheet_text_cells(env):
    factory, _, live, observe, client = env
    live(name="=SUM(1,2)")
    observe()
    reply = client.get("/api/job-forecasts/export")
    assert reply.status_code == 200
    assert reply.headers["content-type"].startswith("text/csv")
    row = list(csv.DictReader(io.StringIO(reply.text)))[0]
    assert row["run_id"] == "'=SUM(1,2)"
    assert client.get("/api/job-forecasts", params={"run_id": "=SUM(1,2)"}).json()[0]["run_id"] == "=SUM(1,2)"
    assert client.get("/api/job-forecasts/export", params={"limit": 10001}).status_code == 422
    with factory() as s:
        assert len(list(s.scalars(select(JobPrediction)))) == 1


def test_missing_or_stale_source_clock_does_not_create_apparently_prospective_forecasts(env):
    _, _, live, observe, client = env
    live()
    assert observe(NOW+timedelta(hours=1))["recorded"] == 0
    assert client.get("/api/job-metrics").json()["stored_observations"] == 0


def test_known_completed_execution_is_not_recorded_from_a_lagging_live_snapshot(env):
    _, _, live, observe, client = env
    factory = env[0]
    complete(factory, start=NOW-timedelta(minutes=10), finish=NOW-timedelta(minutes=1))
    live()
    assert observe()["recorded"] == 0
    assert client.get("/api/job-metrics").json()["stored_observations"] == 0


def test_future_history_cannot_change_a_first_forecast_or_supply_an_early_label(env):
    factory, _, live, observe, client = env
    complete(factory, name="future-history", start=NOW+timedelta(minutes=1),
             finish=NOW+timedelta(minutes=61))
    live()
    observe()
    row = client.get("/api/job-forecasts").json()[0]
    assert row["predicted_remaining_minutes"] == 30  # Cold start, no completed history yet.
    assert row["outcome_status"] is None
    assert client.get("/api/jobs").json()["jobs"][0]["prediction"]["predicted_remaining_minutes"] == 30


def test_identity_mismatch_is_visible_and_can_recover_when_source_is_corrected(env):
    factory, _, live, observe, client = env
    live()
    observe()
    complete(factory, kind="different-job")
    assert observe(NOW+timedelta(minutes=15))["matched"] == 0
    assert client.get("/api/job-metrics").json()["label_issues"] == 1
    with factory.begin() as s:
        s.get(HistoricalRun, "target").job_id = "compile"
    assert observe(NOW+timedelta(minutes=16))["matched"] == 1
    assert client.get("/api/job-metrics").json()["label_issues"] == 0


def test_csv_lifecycle_restart_source_recovery_and_online_backup(tmp_path):
    from simulator.monitoring_smoke import run_smoke
    result = run_smoke(tmp_path / "smoke", days=2, now=NOW)
    assert result["completed_observations"] == 6
    assert result["source_failures_recovered"] == 1
    assert result["backup_observations"] == 6
    assert result["metrics_before_live_fixture"]["awaiting_outcomes"] == 0
    with pytest.raises(FileExistsError):
        run_smoke(tmp_path / "smoke", now=NOW)


def test_upgrade_adds_monitoring_table_without_changing_existing_state(tmp_path):
    from vedrfolnir.db import JobPrediction, SyncState, ModelCheckpoint
    import sqlite3
    path = tmp_path / "old.sqlite"
    engine = create_engine(f"sqlite:///{path}")
    factory = create_session_factory(engine)
    with factory.begin() as s:
        s.add(SyncState(key="history_cursor", value="preserve-cursor"))
        s.add(ModelCheckpoint(name="old-model", payload=b"preserve-checkpoint", seen_count=9))
    # Simulate the supported preceding schema, which lacks only the new table.
    JobPrediction.__table__.drop(engine)
    engine.dispose()
    with sqlite3.connect(path) as connection:
        before = list(connection.iterdump())
    engine = create_engine(f"sqlite:///{path}")
    factory = create_session_factory(engine)
    with factory() as s:
        assert s.get(SyncState, "history_cursor").value == "preserve-cursor"
        assert s.get(ModelCheckpoint, "old-model").payload == b"preserve-checkpoint"
        assert list(s.scalars(select(JobPrediction))) == []
    with sqlite3.connect(path) as connection:
        after = list(connection.iterdump())
    assert all(line in after for line in before)
    engine.dispose()
