from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from vedrfolnir.db import Base, HistoricalRun
from vedrfolnir.features import DurationEstimator, quantile, make_plan_features, progress_phase, timestamp


def fixture_job(now, status, started=0, id="run-1"):
    return SimpleNamespace(
        run_id=id, plan_id="p1", job_id="compile", version="2.3", db_type="postgresql",
        vm_name="vm-1", status=status, priority=0,
        started_at=now-timedelta(minutes=started) if status == "running" else None,
        queued_at=now-timedelta(minutes=10), finished_at=None,
    )


def test_quantile_interpolation():
    assert quantile([2, 4, 8], .5) == 4
    assert quantile([2, 4], .5) == 3
    assert quantile([], .5) is None


def test_running_remaining_is_conditional_not_median_minus_elapsed():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    with Session(engine) as s:
        for i, duration in enumerate([10, 10, 11, 12, 12, 25, 30, 35, 40, 45]):
            s.add(HistoricalRun(
                run_id=f"r{i}", plan_id="p1", job_id="compile", status="passed",
                started_at=now-timedelta(minutes=duration), finished_at=now,
                duration_seconds=duration*60, version="2.3", db_type="postgresql", vm_name="vm-1"
            ))
        s.commit()
        estimator = DurationEstimator(s)
        running = fixture_job(now, "running", 20)
        result = estimator.remaining(running, now)
        assert result.group == "job_version_db"
        assert result.sample_count == 5
        assert result.p50 == 15  # survivors: [5,10,15,20,25]
        assert result.p50 > 0
        queued = fixture_job(now, "queued")
        assert estimator.remaining(queued, now).p50 == 18.5
        plan = SimpleNamespace(plan_id="p1", started_at=now-timedelta(minutes=30), priority=0)
        feat, baseline = make_plan_features(plan, [running, queued], [running, queued], estimator, now)
        assert feat["plan_total_jobs"] == 2
        assert feat["plan_queued_count"] == 1
        assert feat["plan_running_count"] == 1
        assert baseline >= 15
        assert progress_phase(feat) == "start"


def test_sql_templates_accept_leading_comments_and_reject_writes(tmp_path):
    from vedrfolnir.source import MSSQLSource
    q = tmp_path / "historical_jobs.sql"
    q.write_text("-- read-only\nSELECT 1 AS run_id")
    assert MSSQLSource(tmp_path)._query("historical_jobs").startswith("--")
    q.write_text("-- dangerous\nDELETE FROM dummy_table")
    import pytest
    with pytest.raises(ValueError, match="read-only"):
        MSSQLSource(tmp_path)._query("historical_jobs")
