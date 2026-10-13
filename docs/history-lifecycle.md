# Historical reconciliation and storage lifecycle

[← Operations](operations.md) · [Configuration](configuration.md) ·
[Source contract](contracts.md)

These controls target unattended, single-host installations. They do **not**
change the SQL Server source, ML feature selection, or published evaluation
results. Apply them first on a backed-up SQLite database in a staging
environment.

## Recovering late-arriving completed runs

The original forward backfill retains the immutable `(finished_at, run_id)`
high-water mark in `sync_state.history_cursor` and imports up to
`ETA_IMPORT_LIMIT_PER_POLL` records in batches of `ETA_IMPORT_BATCH`.
This cursor is never rewound by reconciliation.

On each successful real-source poll, the importer also scans a recent
completion window. Default: **7 days**, up to **2,000 inspected source
records per poll**, independent of forward import. The scan has its own
`history_reconcile_cursor` and fixed `history_reconcile_until` checkpoint.
A large window spans multiple polls and survives process restarts. Completing
a scan clears its checkpoint; the next poll restarts the overlapping window.
Rows already stored are ignored using SQLite's unique `run_id` conflict
handling. Page validation, future-completion rejection and atomic
row/checkpoint commits apply to both cursors.

| Environment variable | Default | Meaning |
|---|---:|---|
| `ETA_HISTORY_RECONCILE_DAYS` | 7 | Completion-time lookback; set `0` to disable |
| `ETA_HISTORY_RECONCILE_LIMIT_PER_POLL` | 2000 | Max inspected records per poll, separate from forward budget |
| `ETA_IMPORT_BATCH` | 2000 | Max source rows per page |
| `ETA_IMPORT_LIMIT_PER_POLL` | 10000 | Max forward cursor records per poll |

**Important limits:** Records with completion timestamps already older than the
configured lookback are **not guaranteed** to be discovered. Neither are
updates to an existing `run_id`: imports are intentionally insert-only.
If incoming history within a scan grows faster than the reconciliation
budget, a scan may take longer than its lookback period. Increase the
lookback/budget based on real source volume and maximum expected delay.
The SQL source must return deterministic `(finished_at, run_id)` order
compatible with the application's lexical ID comparison. Use a DB index
on these source fields where permitted by your database administrators;
the app only runs SELECT queries.

For a one-time correction older than the window, **back up the SQLite
database first**. Prefer an isolated re-import/evaluation volume or
temporarily enlarge the lookback and its per-poll budget, leaving the
history-retention period greater than the lookback. Do not manually rewind
the production forward cursor without an audited recovery plan. Check
`GET /api/status` for `history_reconcile_in_progress` (a scan is
incomplete) and `history_last_reconciled` (new rows added last poll);
an in-progress flag alone is not a lag-time estimate.

## Bounded retention policy

Each successful poll labels current outcomes and then prunes expired
historical rows with a limit of **max(`ETA_RETENTION_BATCH`, newly imported
historical rows + newly reconciled historical rows)**. Workflow prediction
cleanup remains capped at `ETA_RETENTION_BATCH` per poll. Both run in the
same SQLite transaction after training and outcome matching; the existing
job-level first-observation policy is separate.

This import-aligned history cleanup prevents a large initial source backfill
(for example, 10,000 records per poll) from outpacing a smaller base cleanup
batch (for example, 1,000), provided the aged rows are not protected by
active work or unresolved labels. A large one-time batch can still temporarily
occupy SQLite space during the poll; SQLite's file may not shrink after
deletion. If records are protected or new, their growth is intentional and
the database must be sized accordingly. The import/cleanup rates should be
observed in `/api/status`.

| Stored evidence | Default age | What is deleted / preserved |
|---|---:|---|
| `HistoricalRun` | 365 days since `finished_at` | Old rows are removed **except** runs still in the current snapshot, linked to active plans, or with an unresolved `JobPrediction` label |
| Resolved `Prediction` | 365 days since actual finish | Delete old workflow snapshots **except** training-linked predictions and those for active plans |
| Unresolved `Prediction` | 730 days since prediction | Old snapshots for **non-active** plans may be removed even without a terminal label; active plans remain protected |
| `JobPrediction` | 90 days | Existing job monitoring policy: completed, departed unresolved; current active runs protected |
| `TrainingMarker` and its referenced forecast | No automatic expiry | Preserve idempotency and a labeled training witness for each trained plan/phase |
| `ModelCheckpoint`, `SyncState` | No automatic expiry | Keep trained model state and recovery cursors |

| Environment variable | Default |
|---|---:|
| `ETA_HISTORY_RETENTION_DAYS` | 365 |
| `ETA_PLAN_RETENTION_DAYS` | 365 |
| `ETA_PLAN_UNRESOLVED_RETENTION_DAYS` | 730 |
| `ETA_RETENTION_BATCH` | 1000 |
| `ETA_JOB_RETENTION_DAYS` | 90 |

`ETA_HISTORY_RETENTION_DAYS` must exceed a nonzero reconciliation
lookback. Invalid or non-positive batch/age values fail validation.
The protected training witnesses and repeatedly active/unresolved entities
mean **there is no strict upper byte-size bound** on the SQLite database.
The remaining calibration/evaluation history becomes shorter as snapshots
expire, which can affect model selection and P90 readiness. The River model
checkpoint remains unchanged; no automatic retraining from purged rows.

Before enabling cleanup on an existing production-like state, take and verify
an [SQLite backup](operations.md), and archive that backup off-host if
longitudinal evaluation or audit evidence matters. Keeping only a recent
operational window is not equivalent to maintaining an evaluation archive.
Deletions free reusable SQLite pages but do not necessarily shrink the
on-disk file; schedule maintenance/VACUUM only with application access
stopped and a verified backup. Never use `docker compose down -v`
as a cleanup method.

`GET /api/status` exposes `retention_last_pruned` counts and the current
reconciliation-window state; these **reset on service restart**, are not
cumulative audit counters and must not be treated as a backup inventory.
