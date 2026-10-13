# Local scheduler eligibility investigation  -  October 13, 2026

Estimator base: `2a36b60a8b0a84d3be33bb04e1e6df546b6adfb6`, with the
eligibility diagnostics patch. No duration, calibration or River parameters
were changed. Final benchmarks used locally built **CPython 3.15.0**,
SQLAlchemy 2.0.54 and **real River 0.26.1**. Earlier 3.15.0rc3 runs produced
identical replay prediction rows; those are not substituted for stable runs.

## First-forecast eligibility

| Source / target | Scored plans | Scheduler eligible | Selected MAE | P90 absolute error | Signed bias |
|---|---:|---:|---:|---:|---:|
| Original synthetic CSV, plan start → terminal | 40 | 0 | 15.721 | 35.487 | -1.611 |
| New synthetic two-slot FIFO control, same target | 40 | 40 | 46.031 | 82.550 | +43.331 |
| Public Snakemake, first-task-start → last-task-finish proxy | 29 | 0 | 244.678 | 215.630 | +78.044 |

Errors are minutes. Different sources are **not** comparable accuracy cohorts.
River was selected for zero first forecasts in all three runs.

Every scored original CSV plan reports `pending_jobs`; 12 also report
`no_remaining_jobs` at submission. All 110 bundled plans contain at least
one admission after the plan's start. A timestamp recorded in a completed
trace is not runnable-work evidence before that timestamp. Later first-per-
phase cohorts have 13/13 quarter, 24/24 half and 18/18 late eligible forecasts.

The original base and patched scheduler were replayed under the same options.
All **400 snapshots** retained identical original CSV columns, including
predictions, eligibility and model selection. This patch explains fallback;
it does not improve the numerical estimator.

The separate control respects two finite slots and admits all jobs at plan
submission. Historical fallback MAE was 437.562; raw scheduler, calibrated
and selected MAE were each 46.031 on the same 40 first forecasts. Its load
is intentionally severe; even here the scheduler is imperfect and biased
late. This is a positive **mechanism/regression control**, not real accuracy
evidence or a threshold-tuning dataset.

Snakemake provenance: `wfcommons/WfInstances` commit
`d7bfead08a4dbf985ebbf427b3685a6512c259e2`, `snakemake/`, 34 accepted files.
All 34 have inconsistent workflow/task clocks and use the explicit task-span
proxy. Queue admission is absent. All 29 scored first forecasts report
`pending_jobs`; 10 also report `vm_inventory_unobserved`. This reproduces
previously examined public data, **not a new independent holdout**.

## Small fresh-seed pool diagnostic

Seeds 301-303; 40 historical and 60 live builds per run; 15 chronological
warmups; reconstructed history only; six runs / 270 scored first forecasts.
No parameters were tuned using these seeds. The selected-model non-regression
quality gate passed; it does not establish superiority to the heuristic.

| Synthetic scenario | Scored | Heuristic MAE | Raw / calibrated / selected MAE | Selected P90 error | Selected bias | Shadow River MAE |
|---|---:|---:|---:|---:|---:|---:|
| `multi_pool` | 135 | 4.455 | 4.100 | 9.169 | -1.084 | 5.599 |
| `multi_pool_drift` | 135 | 12.259 | 10.764 | 21.605 | -10.374 | 10.811 |

Shadow cohorts contain the same 135 forecasts in each row. **Zero River
selections** occurred; no first-forecast calibration gain occurred. Drift
remains strongly optimistic. These smaller runs do not replace the earlier
larger experiments or demonstrate real installation performance.

## Commands and limits

Local `bash scripts/local_check.sh full` passed on stable Python: Ruff,
**129 pytest tests with real River**, four quick simulator runs, wheel/sdist
build and installed-wheel version/API/static-resource verification. The
restricted runtime required a writable `TMPDIR` and `PYTHONPATH` pointing
to the active venv's dependency directory for the temporary wheel environment
(a nested venv does not inherit its parent's packages). Earlier bootstrap
attempts failed on missing native headers, a false-negative `pipe` configure
probe and that dependency-path assumption; these were resolved in the local
environment without changing project runtime requirements or skipping tests.

Run from the repository root with the stable Python environment activated:

~~~bash
python -m vedrfolnir.benchmarks --format csv-scheduler \
  --input examples/benchmarks --max-plans 80 --warmup 40 \
  --output benchmark-results/stable-eligibility
python examples/benchmarks/queue_control.py \
  --output benchmark-results/queue-control-input
python -m vedrfolnir.benchmarks --format csv-scheduler \
  --input benchmark-results/queue-control-input --max-plans 80 --warmup 40 \
  --output benchmark-results/stable-control
python -m vedrfolnir.benchmarks --format wfcommons-taskspan \
  --input datasets/WfInstances/snakemake --max-plans 34 --warmup 5 \
  --min-training 30 --output benchmark-results/stable-snakemake
python -m simulator.validation --scenarios multi_pool,multi_pool_drift \
  --seeds 301,302,303 --history-builds 40 --builds 60 --warmup-builds 15 \
  --modes reconstructed --skip-fixture --quality-gate \
  --output benchmark-results/stable-pool-diagnostic
~~~

The replay evaluator does not calculate prospective P90 upper-ETA coverage,
so coverage is **unmeasured**, not zero. The CSV evaluator still cannot replay
authoritative time-varying capacity. No real SQL Server installation or
Docker lifecycle was exercised. Next: expose individual queued-job start
and finish forecasts, then collect real queue/capacity snapshots for a pilot.
