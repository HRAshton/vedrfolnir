# Individual queued and running job forecasts

[← Documentation](README.md) · [Source contract](contracts.md) · [Workflow forecasts](forecasting.md)

`GET /api/jobs` predicts individual job dispatch and terminal finish times.
A job already marked **queued** can be forecast even when a later stage of
its workflow is **pending**. The whole-workflow forecast remains conservative
about those blocked stages. Standalone jobs with no `plan_id` are supported.

~~~bash
curl 'http://localhost:8080/api/jobs?plan_id=build-123&limit=100'
curl 'http://localhost:8080/api/jobs/job-execution-456'
~~~

The list includes queued, running and pending jobs, ordered by `run_id`.
Optional `plan_id` filters the response; it **does not remove competing jobs**
from the queue projection. `limit` defaults to 200 (maximum 1,000), and
`offset` defaults to zero. The scheduler still reads the complete current
snapshot. A detail request returns one current job, including terminal jobs
still present in that snapshot; unknown/removed IDs return HTTP 404.

## Response and freshness

Both endpoints return `generated_at`, `source_observed_at`,
`source_age_seconds`, `data_source_sync`, `source_mode`, `total_jobs` and
`pool_capacities`, plus `jobs` or `job`. Pool rows distinguish declared
limits from observed concurrency lower bounds and include running/queued
counts. No endpoint call writes forecasts, retrains River, or changes the
poller's capacity cache.

`source_observed_at` is the time the app **stored the normalized live
snapshot**, not a claim about the upstream database's own freshness.
Jobs, declared pools and this timestamp are read together in a SQLite
transaction. The timestamp is persisted with successful snapshot replacement
and survives process restart. Invalid snapshots do not advance it.

Snapshots older than three polling intervals, snapshots dated in the future,
an outstanding synchronization error, or a missing snapshot clock withhold
numerical ETAs. The response still provides stored job observations and
explicit reasons. Existing older state becomes eligible after a successful
poll; no destructive database migration is required. Demo data is identified
by `source_mode: demo`.

Example **partial** queued-job prediction (illustrative values):

~~~json
{
  "forecast_available": true,
  "unavailable_reasons": [],
  "prediction_method": "queue_scheduler",
  "predicted_start_at": "2026-10-13T15:06:00+00:00",
  "start_time_source": "projected",
  "predicted_finish_at": "2026-10-13T15:16:00+00:00",
  "predicted_wait_minutes": 6.0,
  "predicted_remaining_minutes": 16.0,
  "capacity_source": "declared",
  "evidence_warnings": ["future_job_arrivals_not_modeled", "job_p90_not_calibrated"],
  "p90_finish_at": null,
  "river_used": false
}
~~~

For a running job, `start_time_source` is `observed`, the start is its measured
past start, wait is zero, and `historical_duration` estimates remaining runtime.
Running work can still receive a finish estimate if its pool is now paused;
queued work in that pool cannot receive a dispatch estimate.

## Assumptions and unavailable reasons

Dispatch uses the same priority-first/FIFO allocation and historical duration
estimator as the workflow scheduler. Capacity is held constant for projection;
future arrivals, preemption, reservations and fairness quotas are not modeled.
Inferred concurrency is a lower bound, not an authoritative ceiling. Sparse
or missing duration history remains explicitly flagged.

| Reason | Why no ETA is emitted |
|---|---|
| `pending_job` | This job is not yet admitted/runnable |
| `queue_admission_unknown` / `queue_admission_in_future` | Admission is missing or not yet observed |
| `queue_order_unknown` | A competing queued job lacks its admission timestamp |
| `pool_capacity_unknown` / `pool_paused` | No usable slot capacity or a declared zero limit |
| `vm_inventory_unobserved` | Legacy VM mode lacks observed inventory |
| `pool_vm_affinity_unsupported` | Pool-slot projection cannot honor a queued VM restriction in the affected pool |
| `running_start_unknown` / `job_start_in_future` | No trustworthy measured start for running work |
| `job_terminal` / `unsupported_job_status` | The job is complete or its status is unsupported |
| `projection_incomplete` | No dispatch/finish projection was produced |
| `source_stale` / `source_error` / `source_not_yet_synced` | Current source evidence is unavailable |

Unknown ordering or affinity blocks queued forecasts in the affected resource
pool, rather than unrelated independent pools. Future-dated admissions/starts
are not used to invent current queue occupancy or capacity.

**Workflow River corrections and workflow P90 bounds are not job estimates.**
This API never transfers those calibrations to a different prediction target.
Job-level uncertainty coverage has not been measured.

## Evaluate and prepare a real pilot

The production-scheduler replay now also writes `job_predictions.csv`, reports
`job_metrics`, and shows job metrics in the offline HTML report. It scores the
first observation per `run_id` **and observed status** after plan warmup.
Queued wait, queued finish and running finish errors remain separate. A later
eligible forecast does not replace an unavailable first observation. Labels
are attached only when the job actually finishes; cancelled/skipped outcomes
are excluded. Jobs within one workflow are correlated, not independent trials.

## Automatic monitoring

Every successful poll saves the **first observation per `run_id` and observed
status** (`queued`, `running`, `pending`) with its snapshot clock, capacity
evidence and eligibility reasons. An unavailable first observation remains
unavailable even if a later snapshot becomes predictable. HTTP GET requests
remain read-only; their current projections may differ from the saved first
forecast. Globally unique execution IDs must not be reused for retries.

Later completed history, or a terminal live snapshot, supplies actual start
and finish times. Only outcomes finished by the poll clock are matched.
Cancelled/skipped jobs and invalid completion/start clocks are labelled but
excluded from error statistics. Job identity mismatches remain unmatched with
an explicit `outcome_issue`. The recorder does not train a job River model.
The signed error is **predicted minus actual** minutes measured from capture;
negative bias means the forecast was too early. Queued wait, queued finish
and running finish are separate cohorts; a running observation has no wait
error. Both successful and failed execution outcomes can be scored.

Open `/` for live ETAs, source health, MAE, P90 absolute error, signed bias,
sample counts, daily trends and recent matched outcomes. It refreshes every
15 seconds; failed refreshes mark the displayed view stale. Job IDs open saved
observations. Workflow metrics remain in their own section. P90 absolute
**error** is not P90 completion coverage; job coverage remains unmeasured.

| Endpoint | Result |
|---|---|
| `GET /api/job-metrics` | Latest 2,000 completed observations per status, including unavailable/excluded counts, plus retained/pending/label-issue counts |
| `GET /api/job-metrics/daily?days=30` | UTC completion-day error buckets from that same bounded sample; 1-90 days |
| `GET /api/job-metrics/by-workload?group_by=pool` | Separate status cohorts grouped by `pool` (default), `job_type` or `load`; eligibility, exclusions, reasons and wait/finish errors |
| `GET /api/job-forecasts?run_id=…&limit=200` | Latest saved first observations; maximum 1,000; `completed_only=true` sorts matched outcomes by completion |
| `GET /api/job-forecasts/export?limit=10000` | Latest retained observations as CSV; maximum 10,000; optional `run_id` |

Empty cohorts contain null errors, not zeros. Daily gaps are left blank. The
dashboard's headline cohort uses the full recent sample; the chart limits it
to the selected days. Correlated jobs are not independent accuracy trials.
Export text that could execute as a spreadsheet formula is prefixed with an
apostrophe; JSON retains original identifiers. CSV contains execution metadata
and should receive the same access protection as the other read endpoints.

The dashboard's **Error by workload** section follows the prediction target
selected above the chart. Group by runner pool, job type (`job_id`), or pool
load. All groups partition the same latest 2,000 completed observations per
status used by the headline metrics; this is **not** 2,000 samples per group.
Rare groups may have no rows in that recent sample. The breakdown uses the
full sample independently of the chart's day window. Eligible/evaluated counts
include unavailable first observations; cancelled/invalid outcomes are shown
as exclusions. Scored counts apply to the selected error target. Fewer than
40 scored observations are marked small samples; larger cohorts remain
descriptive and do not establish accuracy or independence.

Group membership comes from the saved forecast. Later source corrections,
capacity changes or completions do not reclassify old forecasts. Pool load is
the saved local pool's queued-job count divided by its effective slots:
`idle` below 0.5, `busy` from 0.5 to below 2, `saturated` at 2 or above.
Zero capacity is `paused`; missing/invalid evidence is `unknown` (including
older legacy VM forecasts without pool evidence). These are queue-pressure
labels, not CPU utilization. Inferred capacity remains a lower bound. This
breakdown does not fit new per-pool models or transfer workflow corrections.

`ETA_JOB_RETENTION_DAYS` defaults to 90. Completed records expire relative to
completion; departed unresolved records expire relative to capture. Current
active jobs are protected, including long-running work. Cleanup happens during
successful polls. This retention applies to the new additive `job_predictions`
table, not existing source history or workflow forecasts. Back up SQLite before
upgrade. No old forecasts are fabricated from already completed history.

Missing source outcomes remain visibly awaiting labels until expiry; a job
that starts and finishes between polls needs completed historical timing to
be evaluated. The source snapshot clock measures app ingestion, not upstream
freshness. Keep source provenance and queue-policy documentation for a pilot.
For additional on-demand captures, combine pages only when their
`source_observed_at` agrees. Keep private records outside source control.

## Synthetic lifecycle smoke

~~~bash
python -m simulator.monitoring_smoke --output benchmark-results/monitoring-smoke
~~~

Creates a **new** isolated database and chronological CSV fixtures, hides future
outcomes, checks first capture/delayed matching, repeated polls, reconnection
after a missing source, service restarts and SQLite online backup. It leaves
two active fixture jobs for dashboard inspection. This is a pipeline check,
not independent evidence of forecast accuracy. Existing output directories
are rejected to protect state. Serve it locally with CPython 3.15:

~~~bash
SOURCE_MODE=files CSV_DATA_DIR=benchmark-results/monitoring-smoke/source \
ETA_DB_URL=sqlite:///benchmark-results/monitoring-smoke/monitor.sqlite \
python -m uvicorn vedrfolnir.api:app --host 127.0.0.1 --port 8080
~~~

The fixture uses files mode; all measurements in this example are synthetic.
