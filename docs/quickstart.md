# Quick start: demo → own data

[← Documentation](README.md) · [Source contract](contracts.md) · [Operations](operations.md)

**Current source-build route:** the Dockerfile builds CPython 3.15.0
from a source archive with a pinned SHA-256 checksum in a dedicated builder
stage. The first image build is more CPU-intensive;
subsequent local builds can reuse the Docker cache. No release-candidate
Python interpreter is substituted.

Vedrfolnir needs **Docker + Compose v2** for the container route, or **CPython 3.15** for the local route. It stores model state, predictions and imported history in its **own SQLite database**. MSSQL is a **read-only input source**, not the application database.

Public package/image publication is pending. Future PyPI installs and
prebuilt GHCR deployment will be documented after qualification; the commands
below use a source checkout. The planned official image supports Linux/amd64
and demo/CSV, without the MSSQL driver. See the
[release contract](release-contract.md) for support and publication gates.

## A. Run the synthetic demo

From the **repository root**, with Docker and Compose v2:

~~~bash
cp .env.example .env
# Windows PowerShell: Copy-Item .env.example .env
# Generate a token with: python -c "import secrets; print(secrets.token_urlsafe(32))"
# Edit .env and replace ETA_ADMIN_TOKEN=CHANGE_ME_LONG_RANDOM_TOKEN
docker compose build vedrfolnir
docker compose run --rm vedrfolnir vedrfolnir doctor
docker compose up -d
curl http://localhost:8080/health
curl http://localhost:8080/api/status
~~~

The example admin token is **rejected at startup**. Store a unique value
outside source control. The one-off `doctor` command checks configuration
without starting a poller. The dashboard is at `http://localhost:8080`;
the OpenAPI reference is at `http://localhost:8080/docs`.
The default **demo** source is synthetic. `/health` proves process
liveness only; `/api/status` reports source synchronization.

### Local Python alternative (CPython 3.15)

From a repository checkout:

~~~bash
python -m pip install -e ".[dev]"
# Set SOURCE_MODE=demo and a unique ETA_ADMIN_TOKEN in .env
# For separate state, add ETA_DB_URL=sqlite:///./local-demo.sqlite3
vedrfolnir doctor
vedrfolnir serve --host 127.0.0.1 --port 8080
~~~

`serve` loads simple `KEY=VALUE` settings from `.env` (process
environment takes precedence) and starts one worker. `doctor` validates
settings and local file paths, **not** SQL connectivity or source row
contents. Use `vedrfolnir-check-source` for actual read-only preflight.
A wheel ships the application and dashboard; the full repository provides
Compose and source deployment assets.

## B. Connect normalized CSV files

The working [synthetic CSV fixture](../examples/quickstart/csv/README.md)
contains four historical runs, a plan with completed/running/queued jobs and
a declared `builders` pool. Timestamps are intentionally fixed for
reproducibility; this is an **input-contract example**, not a real-time
workload or an ETA accuracy benchmark.

1. Stop your running demo with `docker compose down` (no `-v`) so
   localhost port 8080 is free.
2. In the private `.env`, set `SOURCE_MODE=files` and
   `CSV_SOURCE_PATH=./examples/quickstart/csv`. The latter changes the
   **host-side read-only mount**, not `CSV_DATA_DIR` inside the container.
3. Use an isolated Compose project, and hence an independent SQLite volume:

~~~bash
docker compose -p vedrfolnir-csv build vedrfolnir
docker compose -p vedrfolnir-csv run --rm vedrfolnir vedrfolnir doctor
docker compose -p vedrfolnir-csv run --rm vedrfolnir \
  vedrfolnir-check-source --mode files --csv-dir /srv/examples/data
docker compose -p vedrfolnir-csv up -d
curl http://localhost:8080/api/status
curl http://localhost:8080/api/jobs
curl http://localhost:8080/api/pools
~~~

4. Preflight must report `"ok": true` and sample counts of history=4,
   live=3, plans=1, pools=1. If not, check [required aliases and
   timestamps](contracts.md) rather than disabling validation.
5. For a real source, supply UTF-8 `historical_jobs.csv`,
   `live_jobs.csv` and `plans.csv` with headers, plus optional
   `scheduler_pools.csv`. Export all current jobs of active plans **and**
   globally queued/running jobs. Replace CSV snapshots atomically between
   polls, and point `CSV_SOURCE_PATH` at your own export directory.

The older `examples/data/` fixture has historical data but intentionally
little live activity. For direct Python runs, set
`CSV_DATA_DIR=examples/quickstart/csv` and a separate
`ETA_DB_URL=sqlite:///./local-csv.sqlite3` in `.env`.

**Important:** never mix demo and real histories in one SQLite volume.
`docker compose down` preserves state; `down -v` destroys it.
Use [backup/restore guidance](operations.md) before changing storage.

## C. Connect SQL Server (read-only, optional source build)

MSSQL is outside the initial official image support contract. Install
`.[mssql]` for local Python or rebuild with `INSTALL_MSSQL=true`; the driver
alone does not establish compatibility with your SQL Server deployment.

SQL Server supplies input only: SQLite remains the application database.
The [SQL Server example](../examples/quickstart/sqlserver/README.md)
provides a complete, internally consistent mapping.

1. **Optional sandbox:** execute
   `examples/quickstart/sqlserver/sample_schema.sql` manually in a
   disposable SQL Server database that you own (for example using SSMS or
   `sqlcmd`). It creates test tables and rows; **never execute it on a
   production database**. Vedrfolnir itself does not execute this script.
2. Use an account limited to **SELECT** permissions. For the sandbox tables
   use `examples/quickstart/sqlserver/queries/`. For your own schema edit
   `queries/*.sql` in the repository. Retain `TOP(batch_limit)`, ordered
   `(finished_at, run_id)` paging, full visibility of active-plan jobs
   and optional runner pool limits. See the [source contract](contracts.md).
3. In private `.env`, set `SOURCE_MODE=mssql`, `INSTALL_MSSQL=true`,
   `MSSQL_HOST`, `MSSQL_DATABASE`, `MSSQL_USER` and `MSSQL_PASSWORD`
   (and port if needed). Set
   `SQL_QUERY_PATH=./examples/quickstart/sqlserver/queries` **only**
   when using the disposable example schema. Otherwise leave it at
   `./queries` to use your actual mappings.
4. Stop other instances bound to port 8080. Use a separate project and
   a fresh SQLite volume for the SQL Server source:

~~~bash
docker compose -p vedrfolnir-sql build vedrfolnir
docker compose -p vedrfolnir-sql run --rm vedrfolnir vedrfolnir doctor
docker compose -p vedrfolnir-sql run --rm vedrfolnir \
  vedrfolnir-check-source --mode mssql --query-dir /srv/queries
docker compose -p vedrfolnir-sql up -d
curl http://localhost:8080/api/status
curl http://localhost:8080/api/plans
docker compose -p vedrfolnir-sql logs --tail=50 vedrfolnir
~~~

The preflight **contacts SQL Server** and checks source aliases and
timestamps; `doctor` only checks local configuration and query files.
Ensure SQL Server is reachable **from inside the container**. The source
queries must be read-only, but query checks alone are **not** a security
boundary; enforce database permissions as well. Unedited `REPLACE_ME`
queries are deliberately rejected.

## D. Unattended ingestion: retention and late records

The default source cursor processes completed runs in timestamp/run-ID order.
A separate 7-day reconciliation scan (2,000 records/poll) can recover late
inserts **within** that recent completion window, without blocking the
forward cursor. Historical runs older than 365 days and ordinary resolved
workflow snapshots older than 365 days become eligible for batched cleanup;
unresolved workflow snapshots use a longer 730-day window. Active state and
training-linked evidence remain protected. See the
[storage lifecycle and backup guidance](history-lifecycle.md) before enabling
this policy on retained installations. Reconciliation cannot correct arbitrary
older gaps or changed rows with an existing run ID.

## E. What to expect after connection

| Stage | Expected observation | If not |
|---|---|---|
| Input preflight | `ok: true` and no hard errors | Fix SQL aliases, timestamps or ID uniqueness ([contract](contracts.md)) |
| Polling | `/api/status` shows `data_source_sync: recent` | Check source and logs ([operations](operations.md)) |
| Historical backfill | `history_count` increases as pages import | Check cursor ORDER BY, date filter and `ETA_IMPORT_*` |
| Active jobs | `/api/plans` contains predictions for visible active plans | Check active plan/job visibility |
| Runner capacity | `/api/pools` displays observed or declared slots | Check `sched_pool_id`, running-job intervals, optional limits |
| Learning | `river_training_snapshots` increases **after plans finish** | River cannot train on unlabeled live builds |
| Uncertainty | P90 may initially be absent | Needs ≥40 **comparable completed forecasts**, not simply 40 historical job rows |

A successful source check certifies only that the **sampled input matches the contract**, not that your scheduler's ETA assumptions hold. See [forecast interpretation](forecasting.md).

## E. Shutdown

~~~bash
docker compose down        # stops containers, keeps named SQLite volume
~~~

The named volume is persistent; do not use `down -v` unless deliberately discarding application history/checkpoints.
