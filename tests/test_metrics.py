"""Regression tests for built-in error dashboard and SQLite PoC configuration."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from vedrfolnir.db import Base, Prediction, make_engine
from vedrfolnir.metrics import evaluated_plans, summarize, daily_errors


def test_one_early_forecast_per_plan_and_cancelled_excluded():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime(2026, 10, 11, 17, tzinfo=timezone.utc)
    with Session(engine) as s:
        for plan, offset, error, outcome in [
            ("p1", -20, 5, "passed"),
            ("p1", -15, 1, "passed"),  # duplicate refresh must NOT weight plan twice
            ("p2", -20, -7, "failed"),
            ("p3", -20, 99, "cancelled"),
        ]:
            s.add(Prediction(
                plan_id=plan, predicted_at=now + timedelta(minutes=offset),
                actual_finished_at=now, actual_remaining_minutes=-offset,
                predicted_minutes=-offset + error, error_minutes=error,
                baseline_minutes=-offset + 10, p90_minutes=-offset + 8,
                phase="start", model_version="test", used_model=False,
                outcome_status=outcome, features={}
            ))
        s.commit()
        rows = evaluated_plans(s)
        assert len(rows) == 2
        assert {p.plan_id: p.error_minutes for p in rows} == {"p1": 5, "p2": -7}
        summary = summarize(rows)
        assert summary["mae_minutes"] == 6
        assert summary["signed_bias_minutes"] == -1
        assert summary["baseline_mae_minutes"] == 10
        assert summary["p90_coverage"] == 1
        trend = daily_errors(rows, days=3, now=now)
        assert len(trend) == 3 and trend[-1]["plans"] == 2
        assert trend[-1]["mae_minutes"] == 6
    engine.dispose()


def test_file_sqlite_has_wal_and_foreign_keys(tmp_path):
    engine = make_engine(f"sqlite+pysqlite:///{tmp_path/'test.sqlite'}")
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "wal"
    engine.dispose()
