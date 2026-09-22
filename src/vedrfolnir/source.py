"""MSSQL read-only adapter. Only these SQL files know the user's schema."""
import os
import re
from pathlib import Path
from contextlib import contextmanager

REQUIRED = {
    "historical_jobs": {"run_id", "plan_id", "job_id", "status", "started_at", "finished_at"},
    "live_jobs": {"run_id", "plan_id", "job_id", "status", "started_at", "finished_at"},
    "plans": {"plan_id", "status", "started_at", "finished_at"},
    "scheduler_pools": {"sched_pool_id", "sched_capacity_slots"},
}


class MSSQLSource:
    def __init__(self, query_dir):
        self.query_dir = Path(query_dir)

    def _query(self, name):
        path = self.query_dir / (name + ".sql")
        sql = path.read_text(encoding="utf-8")
        if "REPLACE_ME" in sql or "YourJob" in sql or "YourPlan" in sql:
            raise ValueError(f"Edit {path} to match your private MSSQL schema first")
        statement = re.sub(r"(?m)^\s*--[^\n]*(?:\n|$)", "", sql).lstrip()
        if not statement.upper().startswith(("SELECT", "WITH")):
            raise ValueError(f"Query {name} must be a read-only SELECT / WITH query")
        return sql

    @contextmanager
    def connect(self):
        try:
            import pymssql
        except ImportError as exc:
            raise RuntimeError("Install optional SQL Server support: pip install -r requirements-mssql.txt (Docker: INSTALL_MSSQL=true)") from exc
        conn = pymssql.connect(
            server=os.environ["MSSQL_HOST"],
            user=os.environ["MSSQL_USER"],
            password=os.environ["MSSQL_PASSWORD"],
            database=os.environ["MSSQL_DATABASE"],
            port=int(os.getenv("MSSQL_PORT", "1433")),
            login_timeout=10,
            timeout=40,
            tds_version="7.4",
        )
        try:
            yield conn
        finally:
            conn.close()

    def fetch(self, name, parameters=None):
        sql = self._query(name)
        with self.connect() as conn:
            with conn.cursor(as_dict=True) as cursor:
                cursor.execute(sql, parameters or {})
                columns = {d[0].lower() for d in cursor.description or []}
                missing = REQUIRED[name] - columns
                if missing:
                    raise ValueError(f"Query {name} is missing output columns: {sorted(missing)}")
                return [dict(row) for row in cursor]

    def historical(self, cursor_finished_at, cursor_run_id, batch_limit):
        return self.fetch("historical_jobs", {
            "cursor_finished_at": cursor_finished_at,
            "cursor_run_id": cursor_run_id,
            "batch_limit": batch_limit,
        })

    def live(self, finished_since):
        plans = self.fetch("plans", {"finished_since": finished_since})
        jobs = self.fetch("live_jobs")
        return plans, jobs

    def pools(self):
        if not (self.query_dir / "scheduler_pools.sql").is_file():
            return []
        return self.fetch("scheduler_pools")

class CSVSource:
    """File-backed source using the same normalized aliases as the MSSQL adapter.

    Intended for small/medium local imports and integrations. The CSV files are
    reread on refresh; large production datasets should use indexed SQL queries.
    """

    def __init__(self, directory):
        import csv
        self.directory = Path(directory)

    def fetch(self, name):
        import csv
        path = self.directory / (name + ".csv")
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            missing = REQUIRED[name] - set(reader.fieldnames or ())
            if missing:
                raise ValueError(f"{path}: missing columns: {sorted(missing)}")
            return [{key: value if value != "" else None for key, value in row.items()}
                    for row in reader]

    def historical(self, cursor_finished_at, cursor_run_id, batch_limit):
        from .features import timestamp
        cursor = (timestamp(cursor_finished_at), str(cursor_run_id))
        rows = [r for r in self.fetch("historical_jobs")
                if r.get("finished_at") and
                (timestamp(r["finished_at"]), str(r["run_id"])) > cursor]
        rows.sort(key=lambda row: (timestamp(row["finished_at"]), str(row["run_id"])))
        return rows[:batch_limit]

    def pools(self):
        if not (self.directory / "scheduler_pools.csv").is_file():
            return []
        return self.fetch("scheduler_pools")

    def live(self, finished_since):
        from .features import timestamp
        threshold = timestamp(finished_since)
        plans = [p for p in self.fetch("plans")
                 if str(p["status"]).lower() not in {"passed", "failed", "timed_out", "cancelled", "skipped"}
                 or (p.get("finished_at") and timestamp(p["finished_at"]) >= threshold)]
        # As for MSSQL, files must include all jobs of active plans, plus any
        # globally queued/running jobs. This avoids incomplete plan features.
        jobs = self.fetch("live_jobs")
        return plans, jobs
