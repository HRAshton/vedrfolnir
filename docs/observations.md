# Experimental observations and parameter effects

New: [October 13 local eligibility investigation and bounded benchmarks](experiments/2026-10-13-eligibility.md).

Next implementation: [individual job ETA checks and separate-target replay results](experiments/2026-10-13-job-eta.md).

Monitoring implementation: [prospective capture, dashboard and synthetic lifecycle checks](experiments/2026-10-13-job-monitoring.md).

Workload health: [pool/job/load breakdowns and capacity-change checks](experiments/2026-10-13-job-workloads.md).

Source recovery: [cursor/clock fixes, abrupt-exit recovery and native release checks](experiments/2026-10-13-source-recovery.md).

[← Documentation](README.md) · [Reproduce](validation.md) · [Model assumptions](adaptive-eta.md)

**Evidence date: October 12, 2026.** These are recorded offline experiments - not forecasts for a user's actual production CI. This page preserves **both successes and failures** and separates synthetic multi-job queue ETA from unrelated public build-start/workflow-start tasks.

## Which results apply to which predictor?

| Scope | Data | Outcome measured | Can validate current pool-aware scheduler? |
|---|---|---|---|
| Synthetic simulator | Hidden seeded VM speeds/slots + full simulated job queue | First build ETA and mid-build updates | **Yes**, for its own simplified scheduling assumptions |
| TravisTorrent | Public recorded CI builds | Total duration predicted at build start | **No** individual queue/VM timing |
| WfCommons Pegasus | Public recorded workflow instances | Workflow-start makespan | **No** live runner/queue replay where task starts absent |
| SWF | Public scheduler logs | Single-job queue wait and runtime | Not a multi-job build ETA |
| Bundled CSV fixture | Generated test data | Benchmark smoke/integration | Not independent real-world evidence |

A 5-minute MAE on one scope must **not** be compared to a 5-minute MAE on another as if they predict the same target.

## 1. Original five-seed simulator baseline: seeds 42-46

Protocol: four scenarios, 150 historical builds, 200 live builds per seed, 40 chronological warmups, **five seeds and both history modes** (40 simulator runs, **6,400 scored first forecasts**). Figures below are pooled first-forecast results in minutes; rows use historical reconstruction ON. Legacy is the old workload heuristic; raw scheduler replays observed VM activity.

| Scenario | Legacy MAE | Raw scheduler MAE | Raw scheduler P90 abs. error | Raw scheduler bias |
|---|---:|---:|---:|---:|
| Steady | 3.123 | 2.365 | 5.381 |  -  |
| Burst | 30.751 | 5.436 | 12.710 | -2.585 |
| Overload | 34.183 | 19.306 | 45.505 | **-18.435** |
| Lightly congested VM drift (old workload) | 3.244 | 2.662 | 5.915 |  -  |

**Observations:** priority/FIFO VM replay markedly improved burst/overload error over the heuristic in this matching simulated scheduler. But overload remained **strongly optimistic** (negative bias), because jobs unknown at forecast time cannot be scheduled. The original drift workload had **zero queued jobs**, so it did not meaningfully test congested drift. In later versions the `drift` scenario was made congested and the old case retained as `drift_idle`.

ON/OFF reconstruction affected selected first-forecast MAE by approximately **0 to 0.03 minutes** across these scenarios; do not treat history reconstruction as a proven accuracy gain. Reconstruction also changes how many old plan snapshots River can learn from, not merely which input features are visible.

**River diagnostics on the same original five seeds:**

- Reconstructed **overload** shadow River MAE 14.195 vs raw scheduler 19.306, with better P90 on the *same* 800 scored builds, yet **zero River-selected first forecasts**. Shadow benefit alone does not establish a working promotion path.
- Steady and drift River shadows were generally **worse** than the scheduler. A few selections without reconstructed history worsened selected MAE by about 0.01-0.03 minutes.
- Consequently the subsequent River gate requires **40 phase/load-matched completed validations**, ≥8% MAE advantage and non-regressing tail plus last-20 checks. This is a safety requirement, not an assurance of improvement.

These outcomes come from the uploaded five-seed validation archive and the original `validation/report.md`. The original report used the *previous* drift definition; do not conflate it with the next table.

## 2. Early overload correction and independent holdout

A limited two-seed development comparison (80 historic + 100 live builds; 20 chronological warmups; seeds 42,43) showed **overload MAE 10.17 → 9.41 min** with reconstructed history, while P90 absolute error remained approximately 21.54 → 21.36 min. With history reconstruction OFF, MAE remained 10.17. This was **development evidence**, not sufficient proof of generalization.

An independent **five-seed holdout (101-105)** then used 150 historical builds, 200 live builds, 40 warmups, four scenarios and both history modes: **40 runs / 6,400 scored first forecasts**. Results below use **reconstructed historical queues**.

| Holdout scenario | Raw scheduler MAE | Calibrated MAE | Raw P90 absolute error | Calibrated P90 absolute error |
|---|---:|---:|---:|---:|
| Steady | 2.414 | 2.414 | 5.490 | 5.490 |
| Burst | 5.214 | 5.214 | 10.974 | 10.974 |
| Overload | 21.300 | 21.300 | 50.088 | 50.088 |
| **Congested** VM drift | 45.873 | 44.972 | 91.641 | 91.553 |

**Interpretation:** the overload gain from the smaller development experiment **did not generalize**. Drift calibration reduced MAE only slightly and tail error barely changed. The new River gate made **zero first-forecast selections** in this holdout; this is correct abstention from unproven ML, **not** an ML accuracy win. No parameter thresholds were retuned to fit the holdout seeds.

These results precede the automatic **multi-pool** feature. They should **not** be presented as accuracy measurements of the new pool scheduler.

## 3. Older public-trace benchmarks: unguarded River

A separate partially completed public benchmark workflow produced independently inspectable summaries before its run was cancelled. They measure the **older unconditional River selection**, not the adaptive queue-aware runtime.

| Dataset and target | Scored | Heuristic MAE | Unguarded River MAE |
|---|---:|---:|---:|
| TravisTorrent build-start duration | 900 | **7.334 min** | 10.622 min |
| WfCommons Pegasus workflow-start makespan | 81 | **39.891 min** | 617.528 min |

Unconditional River was **worse** on both. This motivated chronological *shadow* promotion for these stand-alone evaluators, but these are **not post-fix gated-run metrics**. An archived-forecast replay suggested no River promotions; fresh gated benchmarks are still needed before asserting a numerical result for the revised evaluator.

The public traces do not expose the complete multi-job VM queue and therefore do not validate the pool-aware scheduler.

## 4. Parameters, settings and what was learned

| Parameter or design choice | Current setting / treatment | Grounded observation or limitation |
|---|---|---|
| Queue-aware scheduling | Priority/FIFO visible-queue replay | Stronger than heuristic on matched synthetic burst/overload, not a production guarantee |
| Unseen future arrivals | Not predicted | Dominant source of synthetic overload optimism; cannot be solved exactly from present-state features |
| Congested drift | Replaced old drift workload | Reveals large errors (45.873 min raw MAE on unseen seeds) invisible in old lightly loaded drift |
| VM speed/slots | Hidden in simulator | Historical concurrent completions expose lower bounds; not true hardware maxima |
| Pool ID | Optional `sched_pool_id`; default `general` | Independent three-seed synthetic evaluation now recorded below; **no real-world multi-pool accuracy evidence yet** |
| Pool historical capacity | Max observed concurrent jobs; refresh 600 s; window up to 30 days | A **lower bound**. Refresh interval is a behavior default, **not** an optimized hyperparameter |
| Pool declared capacity | Optional dynamic `sched_capacity_slots` | Compared with inferred mode in new simulator; large-matrix results **not yet recorded** |
| Historical reconstruction | ON vs OFF in synthetic replay | Little first-forecast MAE effect in original cohort; changes learning exposure |
| River training floor | `ETA_MIN_ML_TRAINING=30` | Enables shadow readiness only; **not** selection |
| River promotion | ≥40 completed matched builds; ≥8% MAE, no P90 regression, latest-20 check | Prevents unproven unconditional ML; may abstain entirely |
| Saturation correction | ≥30 completed comparable builds, underprediction majority, capped | Two-seed gain failed to generalize on five unseen seeds |
| Empirical P90 upper ETA | ≥40 comparable completed builds, last 200 | Optional/observational; coverage after regime changes not established by a multi-installation study |
| Poll and forecast clocks | 120 s source poll, 300 s min per-plan forecast interval | Defaults, **not** accuracy-optimal settings verified across independent deployments |
| Source worker | One Uvicorn worker | Single-host SQLite architecture; multiple polling writers unsupported |

**Do not interpret “more ML data” or “more slots” as automatically better.** Capacity underestimation can make queue delay too large, while declaring capacity larger than actual can make it too small. Correct resource isolation and valid input timestamps are often more important than River tuning.

## 5. Additional public-trace benchmarks and pool holdout (October 12, 2026)

Two isolated GitHub Actions runs evaluated the unchanged `master` estimator
(sha `77ce0fa`): [public trace results](https://github.com/HRAshton/vedrfolnir/actions/runs/38301792704)
and the [follow-up pool-only simulation](https://github.com/HRAshton/vedrfolnir/actions/runs/38302285905).
Derived per-forecast CSV, summaries, and reports were uploaded as artifacts
(retained for 30 days). **The first workflow is marked failed only because the
synthetic validation CLI had an argparse help string containing an unescaped `%`;
the public-trace jobs and artifact upload had already succeeded.** The second
run fixed that CLI on its isolated benchmark branch and passed.

### Independent public traces (current gated stand-alone evaluators)

| Dataset | Task | Scored | Baseline MAE | River shadow MAE* | Selected River |
|---|---|---:|---:|---:|---:|
| KTH SP2 | Real single-job queue + runtime ETA | 340 | 266.822 | 265.808 | 0 |
| CTC SP2 | Real single-job queue + runtime ETA | 340 | 333.240 | 556.946 | 0 |
| Nextflow WfCommons | Real workflow-start makespan ETA | 96 | 111.487 | 159.423 | 0 |
| Pegasus WfCommons | Real workflow-start makespan ETA | 81 | 39.891 | 617.528 | 0 |

*River shadow figures compare against the baseline on **matching eligible
forecasts**. The CTC River cohort contains 322 jobs, with **342.099 min**
baseline MAE on precisely those 322. The other rows' cohorts are identical
to the scored group. All figures are **minutes**.

The gating rule withheld River in all four traces. For KTH it improved
shadow MAE by only ~0.4%, below the required 8%; for CTC, Nextflow, and
Pegasus it was worse. **Abstention is desirable, but does not make the
baseline accurate.**

The KTH and CTC baselines were *strongly optimistic*: mean signed
`predicted - actual` bias **−266.642** and **−311.856 min** respectively,
with P90 absolute errors **412.006** and **833.564 min**. Their historical
queue policies, requested resources, and future arrivals differ from the
simulator. These are **single-job** workload benchmarks, **not** evidence
for multi-job runner-pool forecasts. The real WfCommons traces are likewise
workflow-start benchmarks rather than live queue replay.

### New independently seeded synthetic pool comparison

Run `38302285905` scored **1,440 first forecasts**: seeds 201-203, three
scenarios, two historical-reconstruction modes, 90 historic + 100 live
builds and 20 warmups per run (240 scored forecasts per scenario/mode).
The original and selected models saw the same forecast-time inputs.

| Pool scenario | Reconstruction | Legacy MAE | Raw scheduler MAE | Selected MAE | Selected P90 abs. error |
|---|---|---:|---:|---:|---:|
| Two inferred pools | Off / On | 4.768 | 3.552 | 3.552 | 8.531 |
| Two declared pools | Off / On | 4.768 | 3.552 | 3.552 | 8.531 |
| Pool contention drift | Off | 20.262 | 16.316 | 16.316 | 32.294 |
| Pool contention drift | On | 20.262 | 16.316 | 14.457 | 30.683 |

Values in minutes. The quality gate passed and **no first-forecast River
promotions occurred**. The inferred and declared-capacity scenarios had
**identical selected MAE** in this experiment; there is **no established
incremental accuracy benefit from supplying authoritative capacity** under
these loads. The drift gain with reconstruction came from the conservative
saturation calibration: MAE **16.316 → 14.457**, improved on all three seeds,
but selected signed bias was still **−13.673 min**. Three synthetic seeds
are too few for broad confidence under workload variation.

**Release implication:** the new public experiments show an unresolved,
potentially large underprediction problem. A 1.0 release is not warranted;
an experimental preview must disclose that general-purpose queue reliability
is not yet established. The new synthetic pool test cannot substitute for
real multi-job, capacity-annotated traces.

## 6. Remaining validation priorities

1. Collect actual **multi-job plans with observable pool assignments,
   task start/end times, queue waits and preferably true scheduler capacity**;
   compare declared vs inferred capacity under genuinely capacity-constrained
   loads.
2. Add realistic future arrivals, scheduling restrictions and dependency
   blocking to the replay, then verify point-in-time input visibility.
3. Recheck end-to-end P90 coverage, data-source reconnect/restart and SQLite
   upgrades on a representative non-demo deployment.
4. Keep fresh independent seed groups (e.g. 204-208) for any future parameter
   retuning; never optimize repeatedly on the already-scored 201-203 group.



## New point-in-time task-timeline benchmark capability

An opt-in `wfcommons-timeline` replay mode now uses **actual per-task timestamps**
with the existing production queue scheduler and labels only after the
real workflow completes. `csv-scheduler` supports traces with *actual*
queue-admission timestamps and pool identities. Both report scheduler
availability/fallback separately from accuracy. See [task replay
protocol](timeline-replay.md).

**This is a measurement capability, not a performance result.** Most
older public WfCommons instances lack per-task timestamps, and even newer
complete execution timelines commonly omit queue admission. The loader
does not synthesize queue times: unstarted tasks are marked pending and
queue replay is not deemed available. A future independent run and
a genuinely admission-observed multi-job trace are still required.


### Public task-timeline ingestion finding (October 12, 2026)

The first [WfCommons Snakemake replay attempt](https://github.com/HRAshton/vedrfolnir/actions/runs/38309685439)
read 34 JSON files but **accepted zero** under strict full-workflow time
consistency. Inspection of a Snakemake RASflow 11-job trace showed
workflow launch at **05:11** whereas the first task starts at **02:44**
(the task span matches the reported duration to subsecond precision).
This is an upstream clock/alignment issue, **not** a measured ETA error.
Silently shifting the task times or treating that workflow clock as
authoritative would misrepresent observed scheduling.

The separate, explicitly named **task-span proxy** evaluates the observed
first-task-start → last-task-finish interval without altering timestamps.
It does **not** evaluate workflow launch/wait overhead and still has no
queue-admission evidence. Subsequent reports must retain these distinctions
and count rejected instances.


## Real per-task replay results (October 12, 2026)

The independent [timeline run](https://github.com/HRAshton/vedrfolnir/actions/runs/38310452776)
passed, but did **not** establish queue scheduler accuracy:

| Dataset and prediction target | Scored first forecasts | Scheduler eligible | Admission coverage | Selected MAE |
|---|---:|---:|---:|---:|
| Snakemake measured **first-to-last task span** | 29 | **0** | 0% | 244.678 min |
| StreamFlow real full workflow timeline | 1 | **0** | 0% | 775.740 min |
| Bundled synthetic multi-job CSV control | 40 | **0** | 100% | 13.413 min |

All 34 Snakemake instances used the explicitly labeled task-span
proxy: their workflow/task timestamp origins differed by over one
minute. Queue admission was absent and **never fabricated**. In
particular, a low error for historical fallback would not prove
anything about queue reconstruction. The synthetic CSV's zero
eligibility **despite recorded admission** is a P0 diagnostic
question; investigate first-forecast capacity/VM evidence and
visibility before optimizing River or claiming a queue improvement.
