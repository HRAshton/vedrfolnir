# Real task-timeline replay: external validation

[← Validation](validation.md) · [Observed results](observations.md) · [Source contracts](contracts.md)

Vedrfolnir previously had two distinct public benchmark modes: workflow-start
makespan estimation without job scheduling, and a legacy duration-only
multi-job progress evaluator. **Neither executed the current queue scheduler.**

The new optional `wfcommons-timeline` and `csv-scheduler` formats replay
the **production ObservedScheduler** at point-in-time fixed offsets. They
compare the historical heuristic, raw observed scheduler, conservative
saturation correction, and River shadow/selected outputs **for exactly the
same plan snapshots**. All models learn only from *completed jobs/plans*.

## WfCommons task timelines

The open [WfInstances dataset](https://github.com/wfcommons/WfInstances)
contains some instances with real **per-task** `executedAt` and
`runtimeInSeconds` (notably newer Snakemake and StreamFlow),
but many older Pegasus/Nextflow/Makeflow instances omit task timestamps.

Only **complete** instances (all execution tasks represented, every task
has a measured start and runtime, valid workflow timing) are accepted.
The strict `wfcommons-timeline` mode also rejects inconsistent workflow
and task clock times, rather than silently shifting execution timestamps.

**Observed source issue:** a public Snakemake trace reports workflow
launch at 05:11 while its first measured task begins at 02:44 on the
same date, a discrepancy of roughly 148 minutes. That prevents an honest
workflow-launch-to-finish replay using both clocks. For analysis of
**task execution only**, `wfcommons-taskspan` explicitly uses the
measured first-task-start → last-task-finish window *without moving the
original task timestamps*. This is **not** a full workflow ETA and should
not be compared directly to workflow-start scores.
Partial runtimes or only an end-to-end workflow timestamp are **rejected**,
not silently given manufactured start times.

Obtain these files into a directory using your own approved dataset download:

```bash
# After a sparse checkout of wfcommons/WfInstances into datasets/WfInstances:
python -m vedrfolnir.benchmarks \
  --format wfcommons-taskspan \
  --input datasets/WfInstances/snakemake \
  --max-plans 34 --warmup 5 --min-training 30 \
  --offsets 0,60,300,900,1800,3600 \
  --output benchmark-results/snakemake-timeline
```

**Crucial limitation:** WfFormat task execution timestamps are **not** job
submission/queue-admission timestamps. The loader keeps `queued_at=NULL`.
At prediction time, unstarted tasks remain **pending**, not queued. The
production scheduler falls back when such pending tasks make a complete
queue projection impossible. Therefore even excellent error numbers from
this dataset would **not prove queue-wait or capacity prediction accuracy**.

Some workflow family samples may have too few complete instances for River
promotion or P90 calibration. Never report a promoted model without
verified completed-build evidence. Use `wfcommons-start` for
specification-only instances; its score is a *different target*.

## CSV with genuine admission and pool data

If a real installation can export job-level queue-admission timestamps and
actual outcomes, prepare the existing normalized `plans.csv` and
`jobs.csv` [benchmark contract](benchmarks.md), including optional
`sched_pool_id`. Then:

```bash
python -m vedrfolnir.benchmarks \
  --format csv-scheduler \
  --input path/to/normalized-traces \
  --max-plans 200 --warmup 40 \
  --offsets 0,60,300,900,1800,3600 \
  --output benchmark-results/real-queue
```

Use this mode for **direct multi-job queue replay evidence**. The engine
learns observed pool/VM capacity from completed executions; it does **not**
read future VM assignments. The CSV loader does not have time-varying
authoritative pool capacity snapshots: observed capacity is the only
supported mode in this evaluator, and results must say so.

## Interpret benchmark output carefully

`summary.json` includes: scored first predictions,
historical heuristic, raw scheduler, calibrated scheduler, selected ETA,
River shadow **on the same eligible subset**, scheduler eligibility/fallback
counts, observed queue-admission coverage, and first-per-phase metrics.
`predictions.csv` includes per-snapshot eligibility,
pending-job count, selected method, phase, regime and pool source. Compare
the raw and selected ETA on the **identical** held-out plans. A fallback
score is not evidence that queue replay works.

**No accuracy result is claimed until a trace is actually run**. Do not
combine workflow-timeline, single-job SWF or workflow-start scores into
one pooled MAE.

### Eligibility diagnostics and a positive synthetic control

Each replay row includes `scheduler_fallback_reasons`; CSV exports encode
the list as JSON. `summary.json` counts reasons for the scored first forecast
per plan and separately for each first-per-phase cohort. Blockers can overlap,
so counts need not sum to the number of fallback plans. The HTML report also
shows first-forecast eligibility and reasons. Codes match the live
[forecast API](forecasting.md).

The original bundled CSV has staggered admissions. Its 100% *recorded*
admission coverage does not mean all jobs were queued at plan start: a
future-admission job is still pending then. Keep that fixture as a negative
control. Generate a separate, capacity-consistent positive control with all
jobs admitted at submission and a shared two-slot FIFO queue:

~~~bash
python examples/benchmarks/queue_control.py \
  --output benchmark-results/queue-control-input
python -m vedrfolnir.benchmarks --format csv-scheduler \
  --input benchmark-results/queue-control-input --max-plans 80 --warmup 40 \
  --output benchmark-results/queue-control
~~~

This is **synthetic eligibility/regression evidence**, not independent accuracy
validation. Capacity and future placements are hidden from the predictor until
observable; the control's ground-truth dispatch respects two finite slots.

### Separate job targets

Scheduler replay also writes `job_predictions.csv` and `job_metrics` for
first observations per job and status after plan warmup. Queued wait,
queued finish and running finish errors are separate targets. Outcomes are
attached at job completion, and an unavailable first observation is not
replaced with a later eligible one. Pending jobs have no invented deadline.
The [job API](job-forecasts.md) uses this same projection/helper logic.

Sampling remains at fixed plan-relative offsets, so short queue waits or
running phases between snapshots may be missed. This is not a census of
admission-time forecasts. Jobs in a plan are correlated; job counts do not
provide independent confidence samples. Neither workflow River correction
nor workflow P90 calibration is applied to job targets.
