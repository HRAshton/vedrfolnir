import os
from dataclasses import dataclass


def integer(name, default):
    return int(os.getenv(name, default))


@dataclass(frozen=True)
class Settings:
    db_url: str = os.getenv("ETA_DB_URL", "sqlite:///./eta-dev.sqlite")
    mode: str = os.getenv("SOURCE_MODE", "demo")
    poll_seconds: int = integer("ETA_POLL_SECONDS", 120)
    prediction_interval_seconds: int = integer("ETA_PREDICTION_INTERVAL_SECONDS", 300)
    import_batch: int = integer("ETA_IMPORT_BATCH", 2000)
    import_limit_per_poll: int = integer("ETA_IMPORT_LIMIT_PER_POLL", 10000)
    min_training: int = integer("ETA_MIN_ML_TRAINING", 30)
    pool_capacity_refresh_seconds: int = integer(
        "ETA_POOL_CAPACITY_REFRESH_SECONDS", 600)
    max_history: int = integer("ETA_MAX_HISTORY_PER_GROUP", 500)
    job_retention_days: int = integer("ETA_JOB_RETENTION_DAYS", 90)
    history_reconcile_days: int = integer("ETA_HISTORY_RECONCILE_DAYS", 7)
    history_reconcile_limit_per_poll: int = integer(
        "ETA_HISTORY_RECONCILE_LIMIT_PER_POLL", 2000)
    history_retention_days: int = integer("ETA_HISTORY_RETENTION_DAYS", 365)
    plan_retention_days: int = integer("ETA_PLAN_RETENTION_DAYS", 365)
    plan_unresolved_retention_days: int = integer(
        "ETA_PLAN_UNRESOLVED_RETENTION_DAYS", 730)
    retention_batch: int = integer("ETA_RETENTION_BATCH", 1000)
    admin_token: str = os.getenv("ETA_ADMIN_TOKEN", "")
    query_dir: str = os.getenv("MSSQL_QUERY_DIR", "queries")
    csv_dir: str = os.getenv("CSV_DATA_DIR", "examples/data")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_format: str = os.getenv("LOG_FORMAT", "json")
    log_config_file: str = os.getenv("LOG_CONFIG_FILE", "")

    def validate(self):
        # Fail closed on example credentials copied into real deployments.
        if self.admin_token.strip().upper() in {
            "CHANGE_ME_LONG_RANDOM_TOKEN", "CHANGE_ME", "CHANGEME",
            "REPLACE_ME", "YOUR_TOKEN",
        }:
            raise ValueError("ETA_ADMIN_TOKEN is an example placeholder; set a unique secret")
        if self.mode not in ("demo", "files", "mssql"):
            raise ValueError(f"Unsupported SOURCE_MODE: {self.mode}")
        for name in ("poll_seconds", "prediction_interval_seconds", "import_batch",
                     "import_limit_per_poll", "max_history",
                     "pool_capacity_refresh_seconds", "job_retention_days",
                     "history_reconcile_limit_per_poll", "history_retention_days",
                     "plan_retention_days", "plan_unresolved_retention_days",
                     "retention_batch"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0")
        if self.history_reconcile_days < 0:
            raise ValueError("history_reconcile_days must be >= 0")
        if (self.history_reconcile_days
                and self.history_retention_days <= self.history_reconcile_days):
            raise ValueError(
                "history_retention_days must exceed history_reconcile_days")
        if self.min_training < 0:
            raise ValueError("min_training must be >= 0")
        if self.mode == "mssql" and not self.query_dir:
            raise ValueError("MSSQL_QUERY_DIR must not be empty")


settings = Settings()
