# Runtime architecture and data flow

[← Documentation](README.md) · [Contract](contracts.md) · [Forecast metrics](forecasting.md)

Vedrfolnir is an experimental **single-process, single-host** service with an independent read-only data source (CSV or SQL Server) and an application SQLite database. It does **not** deploy runners or execute the jobs it predicts.

## Lifecycle of one poll

~~~text
CSV / read-only SQL Server source
  |  historical_jobs (paged cursor)  live_jobs + plans  optional scheduler_pools
  v
Source validation / normalization
  |  reject invalid history pages before advancing cursor
  |  reject invalid live snapshots before replacing saved live state
  v
SQLite: historical_runs, current_jobs, current_plans, scheduler_pools
  |
  +--> Identify terminal plans; label snapshots created before completion
  |    --> update model (at most first prediction per plan and phase)
  |
  +--> Duration estimator + VM/pool capacity cache --> queue projection
       --> optional saturated-queue correction
       --> River SHADOW estimate + promotion gate
       --> matched-cohort P90 upper ETA
       --> persist prediction + evidence metadata
             |
             +--> /api/plans, /api/pools, /api/metrics, browser UI
~~~

A background poll runs every `ETA_POLL_SECONDS` (120 seconds by default). The per-plan forecast throttle is `ETA_PREDICTION_INTERVAL_SECONDS` (300 seconds by default). These are separate clocks: polling can update data without emitting a new prediction for every plan.

The application saves point-in-time forecasts **before** labels become available. Terminal plan timestamps later provide actual remaining minutes and signed errors. River never trains on a future outcome for the prediction it made.

## Duration estimates

History of completed individual job executions provides medians/conditional remaining durations by job type, version, database type and (when enough data exists) observed VM. A running job's forecast accounts for elapsed execution time. Canceled/skipped jobs are excluded from duration calibration where appropriate.

With no usable queue projection - e.g. no observed VM inventory, unknown pool slots, a pool declared with zero slots, a blocked pending job, or an unrecognized status - the system retains the **historical-duration fallback**. This is not a substitute for a dependency DAG.

## Runner-pool capacity

When absent, `sched_pool_id` defaults to `general` and the existing VM-level queue estimator remains active. When pool aliases or declared pool rows make the workload pool-aware:

- Jobs are divided into **resource pools**; the scheduler cannot dispatch a pool-A job using pool-B slots.
- **Declared** `sched_capacity_slots` are current scheduler-supplied limits, fetched at each poll. Explicit zero means unavailable, SQL NULL means not declared/unknown.
- **Observed** concurrency is the maximum overlap of eligible **completed** job intervals and currently running jobs, per pool. The historical calculation covers a rolling 30-day window **subject to the bounded history actually fetched** by the scheduler. It is refreshed every `ETA_POOL_CAPACITY_REFRESH_SECONDS` (600 by default); live running/queued counts are inspected every poll.
- The observed maximum is a **lower bound**, not a known hardware or scheduler ceiling. Running counts can increase it immediately; the scheduler must not invent unseen capacity.
- Pool queue pressure and occupancy are added as numeric features; a bounded hash of pool identity feeds **one** shared River residual model. It does **not** fork a model per pool.

`GET /api/pools` lists each known pool with `capacity_source`, `observed_capacity_slots`, `declared_capacity_slots`, current running/queued counts, and last historical recomputation. The source of a bound should be considered when interpreting an ETA.

**Scheduler assumptions:** visible runnable jobs, priority first and FIFO within ties; no automatic discovery of hidden preemption, reservations, fairness quotas, job dependencies or future arrivals. Current pool mode simplifies within-pool slot allocation; the simulator tests that approximation against hidden VM speed/slot variation.

## Queue calibration and online River

The default ETA is the queue scheduler or historical fallback. Under sustained saturation, an optional **conservative bias correction** uses earlier completed forecasts with matching progress/load conditions. Raw and calibrated scheduler errors are retained separately by the offline benchmark.

The River checkpoint `scheduler_residual_river_v1` learns a bounded logarithmic residual correction (0.5x-2x). Completing the minimum training count does **not** activate it: River must outperform the baseline prospectively, on comparable completed-build predictions, before promotion. A subsequent regression revokes promotion.

Build-specific feature metadata keys prefixed `__` are excluded from River inputs. **Arbitrary SQL-prefixed feature extensions (`feat_num_*`, `feat_cat_*`) are not currently implemented.** There is no support yet for an SQL dependency-edge contract or dynamic queue-policy selection.

## Upper ETA and accuracy

A P90 upper ETA is estimated from the signed overrun distribution of **40-200 previous distinct completed plans** matching the forecast's phase, queue pressure regime and predictor method. If comparable evidence is insufficient, the upper bound is absent; no 90% guarantee is asserted. See [forecasting](forecasting.md).

Validation should report independent seed-level, chronologically held-out errors. [Observations](observations.md) describe where this design helps, fails or has not been independently verified.
