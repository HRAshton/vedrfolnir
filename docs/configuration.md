# Configuration and deployment settings

[← Documentation](README.md) · [Step-by-step setup](quickstart.md) · [Exact source aliases](contracts.md)

## Environment variables

Copy `.env.example`, keep it outside version control, and set a strong `ETA_ADMIN_TOKEN`. Docker Compose passes the settings shown below to the container.

| Variable | Default | Role |
|---|---|---|
| `SOURCE_MODE` | `demo` | `demo`, `files`, or `mssql`; **don't change modes on one data volume** |
| `ETA_DB_URL` | `sqlite:///./eta-dev.sqlite` locally | Application SQLite; Docker supplies `sqlite:////data/eta.sqlite3` |
| `CSV_DATA_DIR` | `examples/data` locally | Normalized CSV directory; container uses `/srv/examples/data` |
| `MSSQL_QUERY_DIR` | `queries` locally | SQL query directory; container uses `/srv/queries` |
| `INSTALL_MSSQL` | `false` (Docker build arg) | Install optional `pymssql` into the image |
| `MSSQL_HOST/PORT/DATABASE/USER/PASSWORD` | port 1433 | Read-only SQL Server connection |
| `ETA_POLL_SECONDS` | 120 | Source import/live refresh interval |
| `ETA_PREDICTION_INTERVAL_SECONDS` | 300 | Minimum elapsed time between new forecasts for one plan |
| `ETA_POOL_CAPACITY_REFRESH_SECONDS` | 600 | Historical pool-concurrency recomputation; live counts still update every poll |
| `ETA_MIN_ML_TRAINING` | 30 | Minimum River training snapshots for shadow readiness, **not** automatic promotion |
| `ETA_IMPORT_BATCH` | 2,000 | Maximum historical rows per SQL/CSV cursor page |
| `ETA_IMPORT_LIMIT_PER_POLL` | 10,000 | Historical rows imported in a poll |
| `ETA_MAX_HISTORY_PER_GROUP` | 500 | Maximum recent duration samples for local estimation |
| `ETA_JOB_RETENTION_DAYS` | 90 | Retain job observations after completion, or capture for departed unresolved jobs; current active jobs are protected |
| `ETA_HISTORY_RECONCILE_DAYS` | 7 | Overlap recent completed history to discover records inserted behind forward cursor; `0` disables |
| `ETA_HISTORY_RECONCILE_LIMIT_PER_POLL` | 2000 | Separate bounded source-page replay budget per poll |
| `ETA_HISTORY_RETENTION_DAYS` | 365 | Age limit for historical runs, with current/unlabeled-job exceptions |
| `ETA_PLAN_RETENTION_DAYS` | 365 | Age limit for resolved workflow snapshots; training-linked evidence pinned |
| `ETA_PLAN_UNRESOLVED_RETENTION_DAYS` | 730 | Expire unmatched old snapshots only after a longer window, excluding active plans |
| `ETA_RETENTION_BATCH` | 1000 | Maximum eligible history/plan rows removed per table each successful poll |
| `ETA_ADMIN_TOKEN` | unset | Bearer token for protected manual sync |
| `LOG_LEVEL` | `INFO` | Stdout log severity |
| `LOG_FORMAT` | `json` | `json` or `text` |
| `LOG_CONFIG_FILE` | empty | Trusted Python `dictConfig` JSON path |

The default Docker Compose configuration maps `ETA_POOL_CAPACITY_REFRESH_SECONDS` and most runtime settings from `.env`. For local direct execution, variables must be present **when Python imports the configuration module**.

## Docker and source mounts

`compose.yaml` runs **one** Python service worker, listens only on `127.0.0.1:8080` on the host, and mounts:

| Host / Docker item | Container path | Role |
|---|---|---|
| Named volume `vedrfolnir-data` | `/data` | Mutable SQLite, model and forecast state |
| `./queries` | `/srv/queries` (read-only) | Private schema mapping SELECTs |
| `./examples/data` | `/srv/examples/data` (read-only) | File-source CSV |
| `./logging` | `/srv/logging` (read-only) | Optional trusted logging config |

The planned official demo/CSV image omits the MSSQL driver; see the
[release contract](release-contract.md). The following options apply to source builds.

To use a different CSV directory in Docker, change the bind mount to point to it. To connect MSSQL, set `INSTALL_MSSQL=true` and rebuild. The source database is accessed through read-only SELECT queries; the *application* state remains SQLite.

**Caution:** source switches (`demo` → `files` / `mssql`) require separate application databases/volumes to prevent synthetic history mixing with real observations. `docker compose down -v` destroys the application volume; it is not a routine restart.

## Logging

Default output is one JSON object per log line on stdout, viewable with:

~~~bash
docker compose logs -f vedrfolnir
~~~

`LOG_FORMAT=text` switches to plain console logging. The optional `LOG_CONFIG_FILE` lets operators supply a Python logging `dictConfig` file (a bundled example exists in `logging/`). That custom config **replaces** the normal log setup. Only use trusted configuration files: dictConfig may instantiate arbitrary handler classes. Avoid logging credentials, raw features and job metadata.

## Network and air-gapped use

The built-in read endpoints have **no built-in login**; the admin sync endpoint requires a bearer token. Before exposing the service beyond localhost, put an **authenticated, TLS-protected reverse proxy** in front of it and restrict network access.

For air-gapped deployment, build the Python 3.15 image with any optional MSSQL driver on an approved connected host, transfer it securely, then run without pulling or rebuilding:

~~~bash
docker compose build vedrfolnir
docker image save -o vedrfolnir-image.tar vedrfolnir:local
# Transfer image, compose.yaml, .env, queries/ and logging/
docker image load -i vedrfolnir-image.tar
docker compose up -d --no-build --pull never
~~~

Ensure the destination also has the proper source files/mounts and durable volume configuration. See [operations](operations.md) for backing up and restoring SQLite safely.


The retention and late-history replay tradeoffs, exceptions, and operator backup
procedure are detailed in [storage lifecycle](history-lifecycle.md).

## Docker host-source overrides

Compose mounts source data **read-only**. Set optional variables in the
private `.env` to select a host directory without editing `compose.yaml`:

| Compose variable | Host default | Path in container |
|---|---|---|
| `CSV_SOURCE_PATH` | `./examples/data` | `/srv/examples/data` |
| `SQL_QUERY_PATH` | `./queries` | `/srv/queries` |

Use `CSV_SOURCE_PATH=./examples/quickstart/csv` for the synthetic CSV
walkthrough or `SQL_QUERY_PATH=./examples/quickstart/sqlserver/queries`
for the disposable SQL Server example. See the detailed
[setup](quickstart.md) and [CSV](../examples/quickstart/csv/README.md) /
[SQL Server](../examples/quickstart/sqlserver/README.md) examples.
Container paths (`CSV_DATA_DIR`, `MSSQL_QUERY_DIR`) do not change.
A mount change requires recreating the service container; use distinct
SQLite volumes per source mode, not `docker compose down -v`.

## Local Python CLI (0.3.0rc1 candidate)

From the repository checkout on CPython 3.15:

~~~bash
python -m pip install -e ".[dev]"
# Copy .env.example to .env and choose a unique ETA_ADMIN_TOKEN
vedrfolnir doctor
vedrfolnir serve --host 127.0.0.1 --port 8080
~~~

`doctor` validates the configuration, rejects example admin tokens,
checks required CSV or SQL files and flags unedited SQL query placeholders.
It does not connect to SQL Server, start polling or validate actual source
rows. Use `vedrfolnir-check-source` for read-only data preflight.

`serve` reads simple `KEY=VALUE` pairs from `.env` (existing process
environment takes precedence) and starts one localhost Uvicorn worker.
Use a separate `ETA_DB_URL` for each local source mode. The wheel
contains application code and dashboard only, not Compose, sample queries,
or example data; use a repository checkout for deployment assets.
