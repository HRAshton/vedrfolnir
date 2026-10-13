# Operations, security and troubleshooting

[← Documentation](README.md) · [Setup](quickstart.md) · [Configuration](configuration.md)

**Release posture:** experimental single-host application with one background polling/training worker and local SQLite. Test in an isolated environment before depending on ETAs for production deadlines.

## Health, status and API

~~~bash
curl http://localhost:8080/health
curl http://localhost:8080/api/status
curl http://localhost:8080/api/plans
curl http://localhost:8080/api/pools
curl http://localhost:8080/api/metrics
curl http://localhost:8080/api/metrics/by-workload
curl 'http://localhost:8080/api/metrics/daily?days=30'
~~~

| Endpoint | Interpretation |
|---|---|
| `/health` | HTTP process is running; it does **not** validate source connectivity |
| `/api/status` | Source mode, imported history, active plans, River snapshot count, last success/error, source freshness |
| `/api/plans` | Latest ETA, prediction method, evidence flags and optional P90 for active plans |
| `/api/plans/{plan_id}/history` | Point-in-time predictions plus completed outcomes when known |
| `/api/pools` | Capacity source (`declared`, `observed`, `unknown`), lower bound vs declared, running/queued |
| `/api/metrics` | Chronological first-start forecast error over completed plans |
| `/api/metrics/by-workload` | Observed metrics split by queue regime and selected method |
| `/api/jobs` | Current individual job start/finish ETAs with snapshot freshness and eligibility |
| `/api/job-metrics` | Saved first-job forecast errors, sample counts, unavailable cohorts and awaiting outcomes |
| `/api/job-metrics/daily` | Daily job errors; queued wait, queued finish and running finish remain separate |
| `/api/job-metrics/by-workload` | Job errors/availability by captured pool, job type or queue pressure; small cohorts remain descriptive |
| `/api/job-forecasts/export` | Retained first-job observations and matched outcomes as CSV |

The built-in dashboard at `/` refreshes every 15 seconds and marks failed
refreshes stale. Successful polls record first job observations automatically;
no dashboard visit is required. See [job monitoring](job-forecasts.md) for
retention, error definitions and missing-outcome diagnostics. The additive
`job_predictions` table is included in normal SQLite backups. Job P90 coverage
is not calibrated; P90 absolute error describes observed misses only.

`data_source_sync=recent` means the last poll succeeded recently (within three poll intervals), **not** that upstream rows are correct/fresh. `stale`, `error` or `not_yet_synced` warrant inspection.

The manual sync endpoint is protected with `ETA_ADMIN_TOKEN`:

~~~bash
curl -X POST http://localhost:8080/api/admin/sync \
  -H "Authorization: Bearer YOUR_TOKEN"
~~~

Never put a real token into a screenshot, issue, example repository, or shell transcript you share.

## Deployment troubleshooting

| Problem | Resolution |
|---|---|
| Startup rejects `ETA_ADMIN_TOKEN` | Replace the example placeholder in private `.env` with a unique token, then recreate the service container. |
| `doctor` reports missing CSVs | Provide all required CSV filenames and headers. In Compose, `CSV_SOURCE_PATH` selects the **host directory**; `CSV_DATA_DIR` stays `/srv/examples/data` inside the container. |
| SQL `REPLACE_ME` remains | Map your real tables/columns in `queries/*.sql` or select the disposable sample SQL Server mapping. `doctor` checks paths; `vedrfolnir-check-source` checks source connectivity and data. |
| Works on the host, fails in Compose | Run `docker compose run --rm vedrfolnir vedrfolnir doctor` to check mounts/settings **inside** the container. Confirm the SQL server is reachable from that network. |
| `Storage belongs to SOURCE_MODE` | Do not mix demo/files/MSSQL on one SQLite volume. Use another Compose project or an independent local `ETA_DB_URL`; back up state first. |
| Localhost port 8080 busy | Stop the prior application/Compose project before starting a new example. Keep the localhost bind; use an authenticated TLS reverse proxy for remote access. |

The [CSV](../examples/quickstart/csv/README.md) and
[SQL Server](../examples/quickstart/sqlserver/README.md) examples use
**static synthetic data**; passing validation does not establish forecast
accuracy or real-time source freshness.

## History and storage health

Recent late-completion corrections are imported using a separate,
transactionally checkpointed overlap scan, while forward backfill continues.
`GET /api/status` reports `history_reconcile_days`,
`history_reconcile_in_progress`, `history_last_reconciled` and
`retention_last_pruned`. The last two counters cover only the most recent
successful poll, **not** the lifetime state of the database. Old runs, resolved
workflow predictions and departed unmatched workflow predictions have
configurable age policies; training marker references and active work are
intentionally preserved.

See [history reconciliation and retention](history-lifecycle.md) for exact
cutoffs, backup/archive requirements, replay limitations and storage-size
caveats. A bounded reconciliation lookback is not a replacement for an
isolated historical audit when source entries arrive much later.

## Source and pool diagnostics

| Symptom | Likely cause / action |
|---|---|
| Preflight `ok: false` | Missing aliases, duplicate run IDs, invalid status/timestamp, inconsistent declared pool slots. Check row-number error codes and fix source mapping. |
| Source poll `error` | SQL connection/permission, query placeholder, invalid live page, or history cursor. Check structured logs; keep last valid snapshots. |
| Historical count stuck | Historical query must use timestamp+run-ID cursor, `TOP(batch_limit)` and ordered results. Increasing the poll limit cannot repair a broken cursor. |
| `historical_cursor_order_invalid` | A page replays an old key or is not strictly ordered. Check tied string IDs and SQL collation; the rejected page is not committed. |
| `historical_batch_limit_exceeded` | The adapter ignored the requested page/budget limit; fix its `TOP`/pagination behavior. |
| `historical_finish_in_future` | A completed-history timestamp exceeds the app clock. Check UTC/timezone mapping and clock synchronization before retry. |
| No active plans | `plans.sql` or `live_jobs.sql` omits active or completed jobs, or source is legitimately idle. |
| Pool capacity `unknown` | No declared limit or observed running concurrency. The predictor falls back rather than inventing workers. |
| Pool capacity `observed` | Historical peak is only a **lower bound**; more true capacity may exist. |
| Pool declared `0` | The source scheduler says this pool is paused: no new queued work should be projected there. |
| Wrong pool split | `db_type` means test configuration, **not necessarily distinct runner capacity**. Pool IDs should reflect real execution constraints. |
| ETA optimistic in overload | Future queue arrivals/reservations/hidden policy not observable; audit priority and pool contract. |
| `p90_finish_at` null | Fewer than 40 **matching, completed first forecasts** for phase/regime/method; this is not an error. |
| River training count positive, `river_used=false` | Expected if its out-of-sample completed-build promotion gate has not passed. |
| SQLite busy/errors | Ensure **one** Uvicorn worker and one app instance using that SQLite DB; use persistent local volume. |

Logs:

~~~bash
docker compose logs --tail=200 vedrfolnir
docker compose logs -f vedrfolnir
~~~

Use `LOG_LEVEL` and `LOG_FORMAT` to control detail. Avoid publishing raw logs without checking for sensitive metadata.

## Backups and upgrades

The Docker named volume `vedrfolnir-data` contains `/data/eta.sqlite3`, saved forecasts/history and pickled River model state. **Back it up before any code or source-mode change.**

A safe, simple backup procedure:

1. Stop the application with `docker compose stop vedrfolnir` to quiesce SQLite writes.
2. Copy the database using an SQLite-aware backup tool **or** copy the entire quiescent `/data` directory including any SQLite WAL/SHM sidecars from the named volume.
3. Verify the backup on a **separate** volume and restore from that tested copy before upgrades if needed.
4. Resume using `docker compose up -d` after the backup/upgrade.

For non-disposable data, never treat `docker compose down -v` as normal restart/upgrade. It deletes the named volume.

**Schema scope:** startup creates missing tables. The pool feature includes a *narrow additive SQLite migration* adding `sched_pool_id` to historical/live jobs and creating the optional `scheduler_pools` table. This does **not** mean arbitrary future database migrations are supported. Upgrade tests should use a backup/copy of a representative existing volume.

**River compatibility:** checkpoints are Python pickle artifacts and should never be loaded from untrusted sources. A River/Python dependency upgrade might make an older checkpoint unreadable. Test such upgrades on cloned data and plan to retrain/reset model checkpoints from retained history if needed; don't delete historical records just to fix a checkpoint.

Rows and their history cursor commit in one transaction per page. A failed
page rolls back together; earlier pages survive an interrupted poll. Restart
resumes from the last committed cursor. Workflow outcomes are labelled and
learned only when their completion time is at or before the poll clock;
future-dated outcomes also stay out of recent-completion and calibration
samples. This prevents new premature labels but does not reverse learning
already performed by older code on incorrect clocks.

## Local release checks

On CPython 3.15 with development dependencies installed:

~~~bash
bash scripts/local_check.sh full
python -m scripts.native_release_smoke
bash scripts/local_check.sh docker
~~~

The native check starts two successive Uvicorn processes on an available
localhost port, using a fresh temporary SQLite database and synthetic CSVs.
It checks APIs, protected sync, preserved history/job captures and graceful
shutdown logs. It does **not** validate an image, non-root container access,
volume mounts or Docker networking.

The Docker gate builds/runs the actual image in demo and files modes. Its
containers and volumes have unique names for that invocation. It checks job
monitoring APIs and preserved first observations, stops/starts the container,
requires graceful shutdown logs, and rejects forced-kill/OOM exits. Uvicorn's
normal post-cleanup SIGTERM exit (Docker code 143) is accepted. Run this gate
on a host with Docker; passing native checks is not a substitute.

**Data source identity:** changing between `demo`, `files` and `mssql` on an existing SQLite volume is intentionally blocked to prevent synthetic and real labels mixing. Use a fresh volume and retain backups of the old one.

## Security and availability

- Compose binds port **127.0.0.1:8080** by default. Read API and built-in UI have **no app-level authentication**; use an authenticated TLS reverse proxy and network ACLs before exposing externally.
- SQL Server login must have **SELECT-only rights**. A textual SELECT check is not a permission boundary.
- Keep `.env`, credential files, SQL query text and model pickle files out of public artifacts. Custom Python logging `dictConfig` must be trusted.
- Use one application worker; multiple worker instances share neither in-memory locks nor capacity-cache state safely.
- Monitor disk capacity and SQLite backups. There is no HA clustering, distributed lock or transactional multi-query source snapshot.

## Practical acceptance criteria for a new integration

A useful first deployment should demonstrate: clean source preflight; consistent polling; valid identities/time semantics; correct pool grouping; at least several completed *whole plans* so errors become visible; and measured MAE/bias by workload. A P90 bound and River activation are **not** required for initial operation, and neither should be fabricated during cold start.

For real scheduling correctness or generalizable accuracy, more than this is required: independent traces with actual queue and VM observations and verified real priority/dependency semantics. See [observations](observations.md).
