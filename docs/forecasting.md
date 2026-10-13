# Forecasts, uncertainty, API and accuracy

[← Documentation](README.md) · [How it works](architecture.md) · [Measured results](observations.md)

## Target and prediction methods

For individual queued-job start/finish forecasts, see the separate
[job API](job-forecasts.md). Job targets do not reuse workflow River/P90 calibration.

The forecast target is **minutes remaining until the plan reaches a terminal state** (`passed`, `failed`, `timed_out`, etc.), **not** minutes until guaranteed success. Each plan consists of observable job executions. The application records new forecasts as time progresses (default throttle: one per five minutes).

`GET /api/plans` returns current active plans and their latest prediction. `GET /api/plans/{plan_id}/history` returns saved snapshots and, after completion, errors.

New forecasts expose `scheduler_available` and `scheduler_fallback_reasons`.
An eligible queue projection has `true` and `[]`; fallback has `false` and
one or more diagnostic codes. Older saved forecasts have `null` for both
fields because their exact eligibility was not recorded. These fields do
not assert accuracy, and no storage migration is needed.

| Fallback code | Meaning |
|---|---|
| `pending_jobs` | A known job is blocked or has not yet been admitted |
| `vm_inventory_unobserved` | Legacy VM scheduling has no observed VM inventory |
| `pool_capacity_unknown` | Queued work lacks observable pool capacity |
| `pool_paused` | Queued work belongs to a declared zero-slot pool |
| `projection_incomplete` | Remaining work has no projection for another reason |
| `no_plan_jobs` / `no_remaining_jobs` | No jobs / no queued or running jobs to project |
| `unsupported_job_status` | A job has an unrecognized status |

Multiple blockers can coexist. A timestamp present in a recorded trace does
not make a job runnable before that timestamp. No fallback reason changes
the historical estimate or permits River promotion.

A prediction has a selected method:

| `prediction_method` | What it means | Key assumption |
|---|---|---|
| `historical_baseline` | Per-job duration heuristic; fallback for unobservable queue | Historical runs resemble current work |
| `queue_scheduler` | Visible queued/running jobs replayed on observed VMs or runner pools | Priority/FIFO and observed capacity resemble the real scheduler |
| `river_residual_correction` | Bounded River correction to the scheduler, selected only after validation | Prior prospective improvements generalize |

Historical time spent running is taken into account. No method can know **unseen future high-priority arrivals, outages or invisible dependent jobs**.

A resource pool represents an execution constraint (e.g. Android vs Linux), while `db_type` can represent the database being tested. The same runner may execute different DB tests, so these concepts should not automatically be conflated.

## A predicted ETA is not a confidence score

Example prediction fields (illustrative names and values):

~~~json
{
  "plan_id": "build-123",
  "predicted_remaining_minutes": 28.0,
  "predicted_finish_at": "2026-10-12T20:00:00+00:00",
  "p90_finish_at": null,
  "prediction_method": "queue_scheduler",
  "evidence_level": "limited",
  "evidence_warnings": ["pool_capacity_inferred_not_declared", "p90_not_yet_calibrated"],
  "p90_calibration_samples": 16,
  "river_used": false
}
~~~

This JSON is a **partial example**, not the entire API response. `evidence_level` and warnings are **diagnostics of data visibility**, not statistically calibrated confidence. An `observational` label does **not** certify accuracy.

### Empirical P90

P90 here is an **upper finish-time estimate**, not the P90 of absolute errors (the latter is an evaluation statistic). It is constructed by adding the 90th-percentile **positive signed overrun** (`actual − predicted`) from a comparable, historical cohort to the current point forecast.

The cohort must include **at least 40 distinct previously completed plans**, one first prediction per plan with the same:

- progress phase (start, quarter, half or late);
- observed queue-load regime (idle, busy or saturated);
- selected prediction method (scheduler, baseline or River).

Only the **most recent 200** eligible completed plans are considered. The upper ETA cannot precede the point ETA. If insufficient eligible outcomes exist, `p90_finish_at` is `null`, even if the installation has thousands of historical **individual jobs**.

**Coverage is empirical, not promised.** Future job arrivals, VM churn and changing work distributions can invalidate the P90. Do not interpret the P90 as a guaranteed 90% completion-by deadline.

## Historical error metrics

`GET /api/metrics` summarizes up to 2,000 recent eligible **completed plans**, evaluating the first start-phase forecast per plan. Multiple refreshes of one long build do not artificially increase the sample count.

| Metric | Interpretation |
|---|---|
| MAE (minutes) | Mean `abs(predicted − actual)`; lower is better |
| Median absolute error | Typical forecast miss |
| P90 absolute error | 90th-percentile magnitude of forecast miss; **not** the ETA upper bound |
| Signed bias | `predicted − actual`; **negative = systematically optimistic** |
| Empirical P90 coverage | Fraction actually finishing before an available P90 upper ETA; only scored where a bound exists |

`GET /api/metrics/daily?days=30` splits completed outcomes by day.

`GET /api/metrics/by-workload` reports MAE, signed bias, P90 absolute error and observed coverage grouped by pressure regime and predictor method. It includes counts and flags groups with fewer than 40 observed builds as descriptive-only. These are **observational samples**, not randomized tests.

`GET /api/pools` reports each discovered pool's observed lower-bound capacity, declared limit (if any), current running/queued jobs and capacity-source label. `GET /api/status` reports source freshness (`recent` / `stale` / `error` / `not_yet_synced`), history count and current River training snapshots.

## Why River may not be selected

River first learns in **shadow mode**, using labels revealed only after the relevant plan has finished. After `ETA_MIN_ML_TRAINING` snapshots it may *make shadow estimates*, but promotion requires at least **40 comparable completed builds**, ≥8% better paired MAE, no worsening of P90 absolute error and a positive last-20 window for both MAE and P90.

It is healthy for `river_used: false` to persist for long periods. Repeatedly training does not prove out-of-sample benefit. Queue calibration and River selection are explained in [adaptive internals](adaptive-eta.md).

## Unsupported scheduling behavior

There is currently **no dependency-edge SQL contract**, configurable queue policy or automatic decoding of arbitrary feature-prefixed columns. A known `pending` job is not treated as executable; unreported future jobs and unknown pool capacity may force fallback. Unseen reservations, backfilling, preemption and priority aging can cause systematic errors.

Use the [source contracts](contracts.md) to make available resource pools and queue timestamps visible; measure actual [per-workload error](observations.md) before trusting ETA deadlines.
