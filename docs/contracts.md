# Source contract: SQL Server and CSV

[← Documentation](README.md) · [Setup](quickstart.md) · [Pool internals](architecture.md#runner-pool-capacity)

The contract is deliberately **small and additive**. Vedrfolnir is read-only against your source DB. You map private columns to standard aliases using three SELECT queries. Optional pool aliases make heterogeneous runners visible; no database schema changes or custom Python plugins are required.

## 1. Required sources

| SQL query / CSV filename | Required aliases | Optional recognized aliases |
|---|---|---|
| `historical_jobs` | `run_id,plan_id,job_id,status,started_at,finished_at` | `version,db_type,vm_name,sched_pool_id` |
| `live_jobs` | `run_id,plan_id,job_id,status,started_at,finished_at` | `queued_at,priority,version,db_type,vm_name,sched_pool_id,sched_capacity_slots` |
| `plans` | `plan_id,status,started_at,finished_at` | `priority` |
| `scheduler_pools` (optional) | `sched_pool_id,sched_capacity_slots` |  -  |

SQL: `queries/{name}.sql`. CSV: `{name}.csv` in the directory supplied by `CSV_DATA_DIR`. Headers must be present even when values are unknown (`NULL` in SQL; empty fields in CSV).

**ID semantics:** `plan_id` is a build/workflow execution; `run_id` uniquely identifies a *single job execution* globally; `job_id` is a recurring job *type* shared between runs. A retried job must have a new run ID. Preserve the same IDs between history and live views.

**Timestamps:** ISO-8601 UTC recommended. SQL Server naive `datetime2` is interpreted as UTC. `started_at` and `finished_at` of completed historical executions are mandatory and `finished_at > started_at`. For running live jobs, `started_at` is mandatory; queued jobs may have NULL `started_at`. Historical rows must have a terminal status. For plans `started_at` may be a creation time; terminal plans require `finished_at`. For live status/field validation see [source preflight](quickstart.md#b-connect-normalized-csv-files).

**Normalized statuses:** `queued`, `pending`, `running`, `passed`, `failed`, `timed_out`, `cancelled`, `skipped`. Recognized aliases include `waiting → queued`, `success → passed`, `failure → failed`, `timeout → timed_out`, `blocked → pending`, and `in_progress → running`. Unknown statuses are rejected before ingestion; [source validator](../src/vedrfolnir/source_validation.py) is authoritative.

**Visibility:** `live_jobs` must include all jobs of active plans (including already completed ones), plus globally queued/running work competing for capacity. `plans` must include active plans and recently completed terminal plans so stored forecasts can receive true completion labels. Source SELECTs are **separate reads**, not one atomic snapshot: keep them temporally consistent where possible.

## 2. Minimal SQL mappings

Replace table and column names with your schema. These examples illustrate aliases, **not guaranteed queries for your database**.

Historical backfill **must** retain the timestamp/run-ID cursor and deterministic order:

~~~sql
SELECT TOP (%(batch_limit)s)
    CAST(j.Id AS nvarchar(140)) AS run_id,
    CAST(j.BuildId AS nvarchar(140)) AS plan_id,
    CAST(j.Kind AS nvarchar(250)) AS job_id,
    j.Status AS status,
    j.StartedAt AS started_at,
    j.FinishedAt AS finished_at,
    j.RunnerName AS vm_name,
    j.RunnerPool AS sched_pool_id
FROM dbo.JobExecutions AS j
WHERE j.FinishedAt IS NOT NULL
  AND (j.FinishedAt > %(cursor_finished_at)s
       OR (j.FinishedAt = %(cursor_finished_at)s
           AND CAST(j.Id AS nvarchar(140)) > %(cursor_run_id)s))
ORDER BY j.FinishedAt, CAST(j.Id AS nvarchar(140));
~~~

Every returned page is checked before insertion: it must respect the requested
batch size, strictly increase `(finished_at, string run_id)` from the persisted
cursor through **every row**, and contain no completion beyond the app's UTC
clock. Preflight reports `historical_batch_limit_exceeded`,
`historical_cursor_order_invalid` or `historical_finish_in_future` with row
numbers, without raw identifiers. Invalid pages do not advance the cursor.
Previously committed pages remain committed and a later poll resumes there.

Tied completion IDs use lexical string ordering (`"10"` precedes `"2"`).
Ensure the SQL cursor predicate and ordering agree with that order; numeric,
case-insensitive or linguistic SQL collation can differ. Use canonical IDs and
an appropriate ordinal comparison in your private mapping, and test tied IDs
before deployment. The examples require adaptation to your database.
Completed rows must become visible in cursor order: rows arriving later
**behind** the saved completion cursor are not recovered by this backfill.
Such sources need an ingestion watermark/reconciliation strategy; these
checks cannot detect records the source never returns.

Optional aliases not selected use default `unknown` / `general` values. For live jobs, retain all required headers:

~~~sql
SELECT
    CAST(j.Id AS nvarchar(140)) AS run_id,
    CAST(j.BuildId AS nvarchar(140)) AS plan_id,
    j.Kind AS job_id,
    j.Status AS status,
    j.StartedAt AS started_at,
    j.FinishedAt AS finished_at,
    j.QueuedAt AS queued_at,
    j.Priority AS priority,
    j.RunnerName AS vm_name,
    j.RunnerPool AS sched_pool_id
FROM dbo.JobExecutions AS j
WHERE j.Status IN ('queued','running')
   OR EXISTS (
       SELECT 1 FROM dbo.Builds AS b
       WHERE b.Id = j.BuildId AND b.Status IN ('queued','running')
   );
~~~

For plans:

~~~sql
SELECT
    CAST(b.Id AS nvarchar(140)) AS plan_id,
    b.Status AS status,
    COALESCE(b.StartedAt, b.CreatedAt) AS started_at,
    b.FinishedAt AS finished_at,
    b.Priority AS priority
FROM dbo.Builds AS b
WHERE b.Status IN ('queued','running')
   OR (b.FinishedAt IS NOT NULL
       AND b.FinishedAt >= %(finished_since)s);
~~~

**Don't paste these examples unchanged.** The files already in `queries/` contain safer editable placeholders. Restrict your SQL account to SELECT; the software's SELECT-text check is not an authorization boundary.

## 3. Runner pools: four cases

**A. Homogeneous runner fleet**  -  omit `sched_pool_id` and `scheduler_pools.sql`. All jobs default to `general`; the legacy observed-VM scheduler remains in use.

**B. Separate pools, infer capacity**  -  select `RunnerPool AS sched_pool_id` in *historical and live* queries. Use distinct names only for distinct **execution resources** (e.g. `linux` and `android`). The pool scheduler infers concurrency from finished jobs and live running counts. This is an observed **lower bound**, not the true maximum.

**C. Authoritative dynamic capacity**  -  create an optional file:

~~~sql
-- queries/scheduler_pools.sql
SELECT
    p.PoolId AS sched_pool_id,
    p.CurrentSlots AS sched_capacity_slots
FROM dbo.RunnerPools AS p;
~~~

For CSV, add `scheduler_pools.csv`:

~~~csv
sched_pool_id,sched_capacity_slots
mssql,12
android,4
paused-ios,0
~~~

Explicit capacity is fetched on **every normal poll**. A declaration of `0` means *temporarily paused*; missing/NULL means *not known*. The capacity may also be projected in `live_jobs` as `sched_capacity_slots` for a pool, but any duplicate declarations must agree. A pool with no visible jobs can still appear in `scheduler_pools`.

**D. Shared runner, different test target**  -  if MSSQL and MySQL tests use the **same physical slots**, keep one `sched_pool_id` and put the tested engine in the existing `db_type` field. Do **not** split into virtual pools just because the test types differ.

| Property | How it's handled |
|---|---|
| Slot capacity | Declared value if present; otherwise observed concurrent-job peak |
| Live pool queue length | Re-read every poll |
| Historical peak | Recomputed about every 600s over a 30-day time window of fetched runs |
| Dispatcher | Priority first, FIFO among equal priorities within a pool |
| Disabled or unobservable pool | No fictitious slot allocation; plan-level heuristic fallback |
| River | One shared model with pool-derived numeric/hash features, **not** a model per pool |

## 4. What the contract deliberately does *not* do

- No `job_dependencies.sql` DAG ingestion yet. A `pending` job causes the heuristic fallback; omitted future jobs cannot be predicted.
- No configurable scheduling-policy SQL yet. Queue replay currently assumes priority/FIFO, not reservations, preemption, fairness quotas or backfilling.
- No automatic decoding of arbitrary `feat_num_*`, `feat_cat_*`, `out_*` or `meta_*` columns yet. Only the core aliases and implemented pool aliases are recognized.
- No authoritative VM inventory is required. `vm_name` is an observed execution identity; inferable concurrency is not a proof of free capacity.
- Source preflight validates a **bounded historical sample**, not all future source rows or the correctness of hidden scheduler rules.

## 5. Validate before deploying

~~~bash
python -m vedrfolnir.source_validation --mode files --csv-dir examples/data
python -m vedrfolnir.source_validation --mode mssql --query-dir queries
~~~

Use only the command appropriate to your source. The preflight prints row numbers and issue codes without echoing raw values. Invalid live pages do **not** replace the prior valid snapshot. Invalid historical pages do **not** advance the cursor. See [operations](operations.md#source-and-pool-diagnostics) for troubleshooting.
