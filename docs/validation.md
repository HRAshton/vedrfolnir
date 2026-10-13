# Reproducible validation protocol

[← Documentation](README.md) · [Recorded findings](observations.md) · [Simulator internals](../simulator/README.md)

**Never mix benchmark targets.** The virtual-clock simulator tests Vedrfolnir's actual queue scheduler and River residual model on synthetic multi-job builds. TravisTorrent, Pegasus and SWF use separate historical-trace evaluators with different targets.

## 1. Installation and fast checks

Use CPython **3.15** from the repository root.

~~~bash
python -m pip install -e ".[dev]"
ruff check src tests
python -m pytest -q
python -m simulator.validation --quick --output benchmark-results/smoke
~~~

The quick simulator run exercises the pipeline but its tiny sample is **not an accuracy benchmark**. GitHub Actions workflows have been removed. Contributors run `bash scripts/local_check.sh quick` and `full` **locally**. Larger matrices are explicit and local, with recorded seeds and source provenance.

## 2. Multi-seed matrix: default and pool-specific workloads

For robust comparisons, use the same simulator seed and scenario parameters across methods, keep historical reconstruction modes matched, and reserve independent seeds for holdout. The simulator hides actual VM speed/slots and running-job residual work from the predictor.

~~~bash
python -m simulator.validation \
  --seeds 101,102,103,104,105 \
  --scenarios steady,burst,overload,drift \
  --history-builds 150 --builds 200 --warmup-builds 40 \
  --interval-seconds 1800 \
  --quality-gate --skip-fixture \
  --output benchmark-results/holdout
~~~

For the **new pool feature** (not yet fully evaluated across seeds):

~~~bash
python -m simulator.validation \
  --seeds 201,202,203,204,205 \
  --scenarios multi_pool,multi_pool_declared,multi_pool_drift \
  --history-builds 150 --builds 200 --warmup-builds 40 \
  --interval-seconds 1800 \
  --quality-gate --skip-fixture \
  --output benchmark-results/pool-holdout
~~~

A smaller preliminary run on **seeds 201-203** using **90** history builds,
**100** live builds, **20** warmups and both reconstruction modes has now
completed; see [observations](observations.md#5-additional-public-trace-benchmarks-and-pool-holdout-october-12-2026).
The 201-205 larger configuration above is **not** a completed test. Do not
treat its future results as untouched holdout for seeds 201-203. These matrices are **not** automatic on each push. Run them on a local workstation using the commands above; save reports under `benchmark-results/`. Do not dispatch GitHub Actions.

| Scenario | What it tests |
|---|---|
| `steady` | Gradual arrivals, 2-5 jobs/build |
| `burst` | Simultaneous arrivals, priority contention |
| `overload` | Fast arrivals, 4-7 jobs/build and large queues |
| `drift` | Congested live workload after changed VM count/contention |
| `drift_idle` | Historical lightly congested drift, useful for contrast |
| `multi_pool` | Two isolated runner pools; inferred observed capacity |
| `multi_pool_declared` | Same pool split, **declared simulator truth** passed as limits |
| `multi_pool_drift` | Two pools and historical/live performance/VM-count drift |

**Important scope restriction:** the standalone `python -m simulator` command supports VM/speed/slot parameters and reconstruct-history flags, but **does not** have a pool-mode CLI flag. Use `python -m simulator.validation --scenarios ...` for built-in pool scenarios.

## 3. Protocol and files

By default each scenario/seed tests **reconstruction OFF and ON**. In both, the underlying simulator's true arrivals and completions are identical. Reconstruction ON additionally exposes historical queue snapshots to River; it is **not** just an extra Boolean input feature.

Each run generates 200 live builds; with 40 warmups, 160 contribute one first-forecast error per seed. For 5 seeds × 4 scenarios × 2 modes that is 40 runs and **6,400 scored forecasts**; for 3 pool scenarios it is 30 runs and **4,800 scored forecasts**.

| Output | Contains |
|---|---|
| `report.md` | Scenario MAE/P90, paired seed differences and mid-build comparisons |
| `summary.json` | Aggregates, queue peaks, shadow counts and validation metadata |
| `runs.csv` | Per scenario/seed/mode results |
| `runs/<scenario>/seed-<n>/<mode>/first_predictions.csv` | Each build's earliest ETA, warmup flag and true completion |
| `runs/<scenario>/seed-<n>/<mode>/snapshots_by_phase.csv` | Earliest snapshot per held-out build/progress phase |
| `runs/<scenario>/seed-<n>/<mode>/summary.json` | Original run metadata, VM count, pool IDs and cache refreshes |
| `existing/bundled_csv_fixture` | Synthetic cross-runner smoke only (unless `--skip-fixture`) |

**Metrics:** MAE = average absolute time error; P90 absolute error = 90th-percentile **error magnitude**; signed bias = predicted minus actual, so **negative means optimism**. Report each for `legacy_minutes`, raw `scheduler_minutes`, `calibrated_minutes` and selected `predicted_minutes` on the **same** scored builds.

River shadow scores are valid only on the **matching shadow-ready subset**, compared to the raw scheduler on that *same* subset. Don't compare shadow-only MAE to the scheduler's all-build MAE. Build-level forecasts within one seed are correlated; bootstrap **independent seed-level paired deltas**, never individual job forecasts.

Mid-build metrics choose one earliest prediction in each phase for each held-out build, so long-running plans don't get extra weight simply for producing more polling snapshots.

## 4. Guardrails and interpretation

The optional `--quality-gate` compares selected ETA against the raw scheduler for each scenario/mode group with ≥3 seeds, and fails only when selected first-forecast MAE **or P90 absolute error** is more than 5% worse, subject to a minimum 0.25-minute tolerance. This is a **non-regression gate**: passing does **not** prove an improvement or deployment readiness.

Treat a result as convincing only when it improves seed-level paired metrics **without** a tail regression, holds on independent seeds, and uses only data available **before** each forecast. Check for genuine queueing and plan progress; a drift scenario with no queue does not validate congestion.

Do not tune repeatedly on final holdout seeds. We used seeds 42-46 for early development and 101-105 for one prior holdout; for novel pool improvements prefer **fresh seeds (such as 201-205)**.

## 5. Separate public traces

~~~bash
python -m vedrfolnir.benchmark_travistorrent --max-builds 1000 --warmup 100
python -m vedrfolnir.benchmark_quickstart --no-open --max-plans 135
python -m vedrfolnir.benchmarks --format swf \
  --input path/to/trace.swf.gz --output benchmark-results/swf
~~~

Public dataset downloads are optional. TravisTorrent measures build-start total duration (not observed job queues); Pegasus measures workflow-start makespan unless actual per-task timelines exist; SWF measures **single-job** queue behavior. Consult [dataset-specific instructions](benchmarks.md) and [recorded results](observations.md). None independently validates the adaptive multi-pool scheduler.

## 6. Claim boundaries

The virtual-clock simulator uses known, simplified priority/FIFO rules and simulated speeds/contention; it **does not** provide real scheduling-policy diversity, unseen future arrivals, dependency graphs, reservations, retries or infrastructure failures. Pass/fail of one synthetic experiment is not a universal ML ranking. Report source, target, scenario, seed range, historical reconstruction mode, warmup, count, MAE, P90, bias and the exact commit.


## True task-timeline replay

The new `wfcommons-timeline` and `csv-scheduler` public benchmark formats
run the **same ObservedScheduler used by the service**, unlike the
legacy `wfcommons` / `csv` duration-only replay. They enforce task
timestamps before accepting a WfCommons instance, never infer missing
`queued_at`, hide the future VM assignment and label completed plans
only after completion. The scorer reports how frequently the scheduler
could really project a plan, not just the error of fallback estimates.

See [timeline replay](timeline-replay.md) for examples, source eligibility,
limitations and the no-future-leak protocol. Do not mislabel WfCommons
pending-job fallback as proof of queue accuracy.


### Public source clock inconsistency

The first 34-file Snakemake replay attempt (October 12, 2026) failed
the new strict timeline check. A verified trace shows workflow-level
start **hours after the first individual task began**. Silently shifting
task timestamps to match would manufacture event timing. The distinct
`wfcommons-taskspan` mode therefore uses only first measured task start
to last measured task finish, and labels this reduced target explicitly.
Use `wfcommons-timeline` only for clock-consistent traces.
