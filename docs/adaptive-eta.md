# Adaptive ETA algorithm (implementation reference)

[← Documentation](README.md) · [Architecture](architecture.md) · [Observations](observations.md)

This describes the implemented algorithm, **not** future contract ideas or guaranteed accuracy.

## 1. Duration and scheduler baseline

The service reads finished historical job durations and visible queued/running job status. It prefers job-type/VM history when available; a running job's remaining duration is conditioned on elapsed execution. With pool identities (`sched_pool_id`), it estimates pool-level median durations and projects priority/FIFO runnable work within each pool's observed/declared slot budget. Without pool information it preserves the older VM-level scheduler.

When jobs are explicitly pending, have an unsupported state or cannot be allocated to an observed/declared resource, the scheduler uses a historical-work heuristic rather than inventing a runnable slot.

**Capacity signals:**

- **Declared:** `sched_capacity_slots` from an optional live-job field or optional pool source; read every polling cycle. Explicit zero is paused; NULL is unknown.
- **Inferred:** maximum observed **overlap** of completed executions in a recent time window (not the sum of all VM peaks), plus observed concurrent live runs. Recompute past overlap every 600s by default; current activity each poll. History is bounded in the scheduler query, so this cannot guarantee the true maximum over all 30 days.
- **Shared River features:** pool count, numeric slots/running/queued, queue pressure, number with declared capacity and bounded hashed pool IDs. Pool IDs do **not** create independent models.

**Current assumed queue policy:** priority first, FIFO among equal priorities. There is no dynamic SQL policy switch, resource reservations, dependency DAG, preemption or future-arrival simulation.

## 2. Conservative saturated-queue bias correction

The scheduler knows only the currently visible work. A conservative multiplicative/absolute guard can add half the historical median *underprediction* if earlier **completed forecasts** consistently show systematic optimism in that progress phase under saturation. Eligibility requires ≥30 comparable completed builds, ≥70% positive underprediction in both the rolling and latest-20 groups, and at least 10% lower MAE after the candidate correction on the recent group.

The correction is bounded to at most **60 minutes**, **75% of baseline**, and the implementation's maximum ETA. It is not applied outside saturation. Corrections are withdrawn when recent evidence stops supporting them.

**Evidence:** a small two-seed gain failed to generalize to five independent unseen seeds. This correction is not claimed to solve unknown future arrivals. [Data and caveats](observations.md).

## 3. River in shadow mode

One persisted River checkpoint, `scheduler_residual_river_v1`, learns a **log residual ratio** between completed actual remaining time and the queue baseline saved at an earlier forecast time. The estimated multiplicative correction is constrained to **0.5×-2×**. Features whose names start with `__` are metadata only and excluded from training.

River starts by **training** from labeled completions, then can make **shadow** forecasts after `ETA_MIN_ML_TRAINING` observations (default 30). It is not automatically promoted.

River may replace the scheduler only if, for the relevant **progress phase and queue pressure regime**:

1. At least **40 distinct earlier completed builds** contain a usable River shadow forecast;
2. River beats the baseline on **paired MAE by ≥8%**;
3. River's P90 absolute error does **not** exceed the matching baseline tail;
4. The **most recent 20** comparable completions still show better MAE and no worse P90.

Promotion is rechecked as new labels arrive. If the evidence disappears, the selected ETA returns to the scheduler. On original holdout scenarios River selected **zero first forecasts**; abstention is expected when evidence is weak.

## 4. Point-in-time P90

Separately from the River promotion gate, an empirical upper ETA uses the **signed-overrun** distribution (`actual remaining − earlier predicted remaining`). It requires ≥40 distinct completed plans matching **phase, pressure regime and selected method**; retains up to 200 recent samples. A missing upper ETA is represented as NULL. An upper ETA does **not** establish a distribution-free guarantee, particularly under capacity drift.

## 5. What is observable versus hidden

Observable before forecast: normalized status/priority, job/plan IDs, timestamps, optional pool identity and declared limits, previous finished jobs, active queue, previous labels.

Not guaranteed observable: actual free hardware slots, hidden priority aging/eligibility, scheduler reservation policy, unreported dependencies, future builds, retries and failures. When the contract omits that information, a confident-looking model cannot recover it reliably.

No automatic arbitrary `feat_num_*` / `feat_cat_*` SQL extension parser is implemented yet. No source-side table creation or special ML hyperparameter configuration is required. Verify installed source/schema mapping and the error history **before** changing model settings.
