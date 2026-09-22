from datetime import datetime, timezone
from sqlalchemy import (
    create_engine, String, DateTime, Float, Integer, ForeignKey, JSON, LargeBinary,
    Text, Index, Boolean, UniqueConstraint, event
)
from sqlalchemy.orm import declarative_base, Mapped, mapped_column, sessionmaker
from .config import settings

Base = declarative_base()


def utcnow():
    return datetime.now(timezone.utc)


class HistoricalRun(Base):
    __tablename__ = "historical_runs"
    run_id: Mapped[str] = mapped_column(String(140), primary_key=True)
    plan_id: Mapped[str | None] = mapped_column(String(140), index=True)
    job_id: Mapped[str] = mapped_column(String(250), index=True)
    version: Mapped[str] = mapped_column(String(80), default="unknown")
    db_type: Mapped[str] = mapped_column(String(100), default="unknown")
    vm_name: Mapped[str] = mapped_column(String(160), default="unknown")
    sched_pool_id: Mapped[str] = mapped_column(String(80), default="general", server_default="general")
    status: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    duration_seconds: Mapped[float] = mapped_column(Float)
    __table_args__ = (Index("ix_runs_lookup", "job_id", "version", "db_type", "finished_at"),)


class CurrentPlan(Base):
    __tablename__ = "current_plans"
    plan_id: Mapped[str] = mapped_column(String(140), primary_key=True)
    status: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    priority: Mapped[int] = mapped_column(Integer, default=0)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CurrentJob(Base):
    __tablename__ = "current_jobs"
    run_id: Mapped[str] = mapped_column(String(140), primary_key=True)
    plan_id: Mapped[str | None] = mapped_column(String(140), index=True)
    job_id: Mapped[str] = mapped_column(String(250))
    version: Mapped[str] = mapped_column(String(80), default="unknown")
    db_type: Mapped[str] = mapped_column(String(100), default="unknown")
    vm_name: Mapped[str] = mapped_column(String(160), default="unknown")
    sched_pool_id: Mapped[str] = mapped_column(String(80), default="general", server_default="general")
    status: Mapped[str] = mapped_column(String(40))
    priority: Mapped[int] = mapped_column(Integer, default=0)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SchedulerPool(Base):
    __tablename__ = "scheduler_pools"
    sched_pool_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    sched_capacity_slots: Mapped[int | None] = mapped_column(Integer)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Prediction(Base):
    __tablename__ = "predictions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(String(140), index=True)
    predicted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    phase: Mapped[str] = mapped_column(String(20))
    model_version: Mapped[str] = mapped_column(String(40))
    used_model: Mapped[bool] = mapped_column(Boolean, default=False)
    baseline_minutes: Mapped[float] = mapped_column(Float)
    predicted_minutes: Mapped[float] = mapped_column(Float)
    p90_minutes: Mapped[float | None] = mapped_column(Float)
    features: Mapped[dict] = mapped_column(JSON)
    actual_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    actual_remaining_minutes: Mapped[float | None] = mapped_column(Float)
    error_minutes: Mapped[float | None] = mapped_column(Float)
    outcome_status: Mapped[str | None] = mapped_column(String(40))
    __table_args__ = (
        Index("ix_predictions_plan_time", "plan_id", "predicted_at"),
        Index("ix_predictions_eval", "phase", "actual_finished_at"),
    )


class JobPrediction(Base):
    __tablename__ = "job_predictions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(140), index=True)
    plan_id: Mapped[str | None] = mapped_column(String(140), index=True)
    job_id: Mapped[str] = mapped_column(String(250))
    sched_pool_id: Mapped[str] = mapped_column(String(80))
    observed_status: Mapped[str] = mapped_column(String(40))
    predicted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    forecast_available: Mapped[bool] = mapped_column(Boolean)
    prediction_method: Mapped[str | None] = mapped_column(String(40))
    predicted_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    predicted_finish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    predicted_wait_minutes: Mapped[float | None] = mapped_column(Float)
    predicted_remaining_minutes: Mapped[float | None] = mapped_column(Float)
    unavailable_reasons: Mapped[list] = mapped_column(JSON)
    evidence_warnings: Mapped[list] = mapped_column(JSON)
    capacity_evidence: Mapped[dict] = mapped_column(JSON)
    actual_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    outcome_status: Mapped[str | None] = mapped_column(String(40))
    wait_error_minutes: Mapped[float | None] = mapped_column(Float)
    finish_error_minutes: Mapped[float | None] = mapped_column(Float)
    outcome_issue: Mapped[str | None] = mapped_column(String(60))
    __table_args__ = (
        UniqueConstraint("run_id", "observed_status", name="uq_job_prediction_first_status"),
        Index("ix_job_prediction_pending", "outcome_status", "run_id"),
        Index("ix_job_prediction_status_finish", "observed_status", "actual_finished_at"),
    )


class ModelCheckpoint(Base):
    __tablename__ = "model_checkpoints"
    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    payload: Mapped[bytes] = mapped_column(LargeBinary)
    seen_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TrainingMarker(Base):
    __tablename__ = "training_markers"
    plan_id: Mapped[str] = mapped_column(String(140), primary_key=True)
    phase: Mapped[str] = mapped_column(String(20), primary_key=True)
    prediction_id: Mapped[int] = mapped_column(Integer, ForeignKey("predictions.id"))


class SyncState(Base):
    __tablename__ = "sync_state"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


def make_engine(url=None):
    engine = create_engine(url or settings.db_url, pool_pre_ping=True, future=True)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def sqlite_settings(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            if engine.url.database != ":memory:":
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()
    return engine


SCHEMA_VERSION = 1


def _upgrade_sqlite(engine):
    """Apply ordered, idempotent migrations to old and newly created databases."""
    with engine.begin() as connection:
        version = connection.exec_driver_sql("PRAGMA user_version").scalar_one()
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"SQLite schema version {version} is newer than supported {SCHEMA_VERSION}")
        if version < 1:
            # v1: add pool identifiers to databases created before pool support.
            # Check columns for compatibility with databases upgraded by v0.2.0.
            for table in ("historical_runs", "current_jobs"):
                cols = {row[1] for row in connection.exec_driver_sql(
                    f"PRAGMA table_info({table})")}
                if "sched_pool_id" not in cols:
                    connection.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN sched_pool_id "
                        "VARCHAR(80) NOT NULL DEFAULT 'general'")
            connection.exec_driver_sql("PRAGMA user_version = 1")


def create_session_factory(engine):
    if engine.dialect.name == "sqlite":
        # Refuse unknown newer schemas before creating any new tables.
        with engine.connect() as connection:
            version = connection.exec_driver_sql("PRAGMA user_version").scalar_one()
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"SQLite schema version {version} is newer than supported {SCHEMA_VERSION}")
    Base.metadata.create_all(engine)
    if engine.dialect.name == "sqlite":
        _upgrade_sqlite(engine)
    return sessionmaker(engine, expire_on_commit=False)
