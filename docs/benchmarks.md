# Real-world ETA benchmarks

[← README](../README.md)

Benchmarks replay *historical timestamps*, **not jobs or Docker containers**. They need no database server, web UI, MSSQL, or GPU. Install **CPython 3.15** dependencies first (`python -m pip install -e .`). These trace evaluators do **not** validate the live pool-aware scheduler. See [observed results](observations.md).

## One-click benchmark: TravisTorrent (10,000 real CI builds)

**Windows:** use `scripts/benchmarks/travistorrent.bat` with **Python 3.15** installed.
**Linux/macOS:** install the project's Python 3.15 requirements, then run `./scripts/benchmarks/travistorrent.sh`.

Cross-platform command:

```bash
python -m vedrfolnir.benchmark_travistorrent
```

The first run uses the [TravisTorrent public Figshare archive](https://figshare.com/articles/dataset/TravisTorrent/19314170)
(CC BY 4.0), cached in `datasets/travistorrent/`. The full archive listed by Figshare
is about 395 MB; allow for additional processing time/disk space. Downloads use the
public Figshare API, inspect candidate CSV archives and verify file size/MD5 if provided.
No Git, MSSQL, Docker or GPU needed. The runner opens
`benchmark-results/travistorrent/report.html` when finished, and also writes
`summary.json` and `predictions.csv`.

Use a previously downloaded `.csv`, `.csv.gz`, or `.zip` containing the official CSV:

```bash
python -m vedrfolnir.benchmark_travistorrent --data "C:/path/final-2017-01-25.csv.zip" --offline
```

Quick diagnostic benchmark, useful before the full 10,000:

```bash
python -m vedrfolnir.benchmark_travistorrent --max-builds 1000 --warmup 100 --min-training 50
```

`--max-builds` limits **distinct build IDs**, not source rows. The importer scans the
complete dataset to identify the earliest eligible builds even if CSV rows are out
of timestamp order. Every selected build generates exactly one start-time prediction,
and its label becomes available only after its recorded completion. History and River
update chronologically. The report compares historical baseline vs selected
(River or fallback), with MAE, P90 absolute error, bias, error distribution, worst
forecasts and data-ingestion statistics. It records when River was actually used.

### Important limits for TravisTorrent

TravisTorrent is **one row per build job**, but `tr_duration` is the **overall build
duration** shared across rows of that build. The adapter deduplicates by `tr_build_id`;
it *does not* sum or invent job durations. The fields `tr_jobs` and `tr_job_id` allow
estimating job **count**, not individual job complexity. Features at build start
include repository, language, PR flag and job count; **final status, actual job
runtime and log-derived statistics are never supplied to the predictor**.
Its target is time to a terminal build result, not necessarily a successful build.
The resulting benchmark measures **build-start duration prediction** only. It does
not validate queue wait, per-job duration modeling or live progress ETA.

The original 2017 data uses `gh_build_started_at` (not `tr_started_at`); the importer supports both names. The official field contract is documented by [TestRoots](https://testroots.github.io/travistorrent-site/page_dataformat/).
The adapter has offline regression tests against that schema and the paper's published example build metadata. A partially completed public-trace workflow produced **older, unguarded River** accuracy measurements for 900 builds; these are documented with their exact target and limitations in [observations](observations.md). A fresh end-to-end run of the **gated** evaluator is still needed for post-fix numerical claims.
Third-party raw data is downloaded by users and is not bundled in this repository.

## One-click benchmark: WfCommons Pegasus (real workflows)

WfCommons provides actual multi-task workflow executions in [WfFormat](https://github.com/wfcommons/WfFormat).
Pegasus WfInstances includes **real workflow start timestamps and overall makespans**, but typically **no start timestamps for individual tasks**. The one-click command therefore benchmarks **ETA at workflow start**, using only its static DAG/task structure. It cannot benchmark ETA updates during execution, VM contention or queue waiting.

**Windows:** double-click `scripts/benchmarks/wfcommons.bat` (requires Python and Git).

**Linux/macOS:** run `./scripts/benchmarks/wfcommons.sh`.

Or use the cross-platform CLI after `python -m pip install -e .`:

```bash
python -m vedrfolnir.benchmark_quickstart
```

The first run downloads the public Pegasus trace collection, then evaluates up to
135 workflows and opens the generated HTML report in your default browser. Evaluation
replays recorded timestamps; it **does not execute any workloads**.

Outputs under `benchmark-results/wfcommons-pegasus/`:

- `report.html`: self-contained offline dashboard with baseline-vs-selected model
  MAE, median/P90 errors, error distribution, progress-phase accuracy, and worst
  individual plan-start predictions.
- `summary.json`: machine-readable aggregated metrics, record counts, and runtime.
- `predictions.csv`: every individual forecast and its actual remaining time.

To use a previously downloaded dataset, including on an air-gapped system:

```bash
python -m vedrfolnir.benchmark_quickstart --data datasets/WfInstances --offline
```

For a quicker smoke run:

```bash
python -m vedrfolnir.benchmark_quickstart --max-plans 30 --warmup 5 --min-training 5
```

`river_used_plans = 0` means the **selected ETA stayed on the baseline**; River may still have been evaluated in **shadow mode** on an eligible subset. Compare `river_only` to `baseline_on_river_subset` on those same items, not to the all-workflow baseline. No selections alone are not evidence of better ML accuracy. Smaller trace subsets
may have too few completed historical workflows to train River meaningfully.

### If the loader says no WfCommons executions were found

Previously the runner could silently accept an empty or incomplete Git sparse
checkout and fail with `No valid WfCommons execution instances found`.
The new downloader **always configures the Pegasus sparse checkout and checks for
actual JSON files**. The parser now explains how many JSON files were found and why
representative files were rejected.

If you already have a partial `datasets/WfInstances` folder, rerun the quickstart.
For a badly interrupted clone, remove only the **downloaded** `datasets/WfInstances`
folder and retry. Do not delete benchmark results or application data volumes.

The source collection is [wfcommons/WfInstances](https://github.com/wfcommons/WfInstances).
No public trace files are included in this repository. The one-click runner prefers the workflow-start format when the overall makespan is available, and falls back to task timelines otherwise. Pegasus uses `workflow.execution.executedAt` and `makespanInSeconds` for plan-start evaluation. If a supplied dataset really includes per-task `executedAt` and `runtimeInSeconds`, the original progress-aware format remains available via `--format wfcommons`. Never infer task start times from aggregate runtimes.

## Alternative: actual queue waits (single jobs)

Download an SWF trace from the [Parallel Workloads Archive](https://www.cs.huji.ac.il/labs/parallel/workload/logs.html), then run:

```bash
python -m vedrfolnir.benchmarks \
  --format swf --input /path/to/trace.swf.gz \
  --max-plans 400 --output benchmark-results/swf
```

SWF has submission, waiting and running times, but **one job per workload**: it measures queue-aware **job ETA**, not multi-job plan ETA. Results from the two benchmarks must not be combined into one score.

## Offline adaptive simulator validation

To compare the *production scheduler* and shadow River model without
production data, run:

```bash
python -m simulator.validation --quick
python -m simulator.validation --seeds 42,43,44,45,46 --history-builds 150 --builds 200 --warmup-builds 40
```

See [validation protocol](validation.md). Synthetic scheduler results are
separate from actual TravisTorrent, WfCommons or SWF trace results and
should not be compared directly.

## Synthetic smoke test (not a real benchmark)

```bash
python -m vedrfolnir.benchmarks --format csv --input examples/benchmarks
```

Bundled CSVs are generated and designed to test the runner. Don't present their error metrics as real-world forecasting accuracy.

## Bring your own historical executions

For CI workloads use a build as the plan and build steps such as checkout, compile, package, and deploy as jobs. Other workflows use the same file format.

Use two UTF-8 CSV files in one directory:

```text
plans.csv: plan_id,started_at,finished_at,priority,status
jobs.csv: run_id,plan_id,job_id,queued_at,started_at,finished_at,status,version,db_type,vm_name,priority
```

`priority` and `status` are optional (default 0 and passed). The last six job fields and `queued_at` are optional; missing queue timestamps mean *unknown queue status*. All timestamps are ISO-8601. Cancelled/skipped plans aren't scored. Then:

```bash
python -m vedrfolnir.benchmarks --format csv --input /path/to/traces
```

## How evaluation works

The **Pegasus start-only benchmark** predicts once at workflow start using only the workflow specification; measured makespan is revealed at completion. River updates only after that completion. The current evaluator keeps River in shadow mode until enough completed, prospective forecasts validate it against the baseline. The report excludes the first 20 workflow starts by default for warm-up. It cannot score mid-run forecasts.

The **timestamped workflow/CSV/SWF benchmark** uses recorded execution times. Its estimator sees job durations only after jobs finish; River learns only after plan completion. Predictions use fixed elapsed offsets (0, 60, 300, 900, 1800, 3600 seconds by default), never percentages derived from future finish times. Summary scores one start-time prediction per plan; all snapshots remain in `predictions.csv`.

CLI options: `python -m vedrfolnir.benchmarks --help`. Public traces are not bundled; check upstream terms before redistribution.

## Accuracy scope

The Pegasus plan-start benchmark trains a **separate River regression against DAG features** and reports baseline-vs-model errors for observed workflow makespans. It **does not exercise the live service's queue-aware feature extraction**. Task runtime values and workflow makespan are withheld as model inputs until the workflow has completed. Scores cannot be interpreted as queue ETA or mid-run ETA accuracy.


## Interpretation and evidence hygiene

**Do not combine these source-specific scores with multi-job scheduler MAE.** The original recorded public-trace results used an older, unconditional River selection rule and showed worse MAE than the baseline. The revised chronological gate needs a fresh independent run. See [parameter observations](observations.md) and [simulator protocol](validation.md). All automatic heavyweight GitHub benchmark jobs are disabled; full validation is opt-in.


## True multi-job scheduler replay (new experimental evaluator)

Earlier `wfcommons` / `csv` progress modes used the legacy duration-only
heuristic and **did not run** Vedrfolnir's production queue replay.
To compare the actual observed scheduler and River correction at
chronological snapshots, use:

~~~bash
python -m vedrfolnir.benchmarks --format wfcommons-taskspan \
  --input datasets/WfInstances/snakemake --max-plans 34 --warmup 5 \
  --output benchmark-results/real-task-timelines

python -m vedrfolnir.benchmarks --format csv-scheduler \
  --input path/to/plans-and-jobs-csv --max-plans 200 --warmup 40 \
  --output benchmark-results/real-queue
~~~

`wfcommons-timeline` requires consistent workflow launch and per-task clocks.
`wfcommons-taskspan` measures only **first observed task start → last task
completion** without changing task timestamps. It is **not full workflow
makespan**. Both modes **require actual executedAt for every task** and
reports how many public JSON files were accepted or rejected.
Most public WfCommons families **do not** meet that requirement.
WfFormat does not supply job queue admission: the evaluator keeps
unstarted tasks **pending**, never fabricates queue waits, and reports
scheduler fallback. Only `csv-scheduler`, with real `queued_at` timestamps,
can directly measure reconstructed queued-job scheduling.

Both modes compare heuristic, raw scheduler, calibrated and selected ETA
on the **same first forecasts**, and expose method eligibility, shadow
cohort counts and per-progress-phase accuracy. See the
[full protocol](timeline-replay.md) and [observations](observations.md).
