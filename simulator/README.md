# Virtual-clock build ETA simulator

[← Project README](../README.md) · [Validation](../docs/validation.md) · [Measurements](../docs/observations.md)

The simulator generates **synthetic** builds/jobs, schedules them onto hidden-speed VMs, and presents only observable state to Vedrfolnir's actual duration estimator, queue scheduler and River residual model through an **in-memory SQLite** store. It does **not** execute CI jobs, require MSSQL, or wait for simulated durations in real time.

## Standalone run

After installing CPython 3.15 and `python -m pip install -e ".[dev]"`:

~~~bash
python -m simulator --history-builds 100 --builds 200 --seed 42
python -m simulator --history-builds 100 --builds 150 \
  --burst-size 150 --interval-seconds 600
python -m simulator --servers 3 --vms-per-server 30
python -m simulator --vm vm-a:1.6:1 --vm vm-b:0.8:2
~~~

The `--vm` syntax is `NAME:SPEED[:SLOTS]`. Defaults: **30 VMs/server**, hidden seeded speed modifiers and **1-3 job slots per VM**. Contention controls slowdown:

~~~text
effective service rate = VM speed / (1 + contention × (active jobs on VM − 1))
~~~

Jobs initially queue **without assigned VM names** and become associated with a VM only after dispatch; the predictor never sees simulator-private speed/capacity or future actual work.

Historical queue replay can be enabled/disabled:

~~~bash
python -m simulator --history-builds 100 --builds 200 \
  --reconstruct-historical-queue
python -m simulator --history-builds 100 --builds 200 \
  --no-reconstruct-historical-queue
~~~

Standalone outputs: `benchmark-results/simulator/summary.json` and `predictions.csv`.

## Multi-seed evaluation, including runner pools

Use the separate **validation matrix** for predefined pool scenarios. The standalone simulator CLI **does not expose a pool-mode switch**:

~~~bash
python -m simulator.validation --quick
python -m simulator.validation \
  --seeds 201,202,203,204,205 \
  --scenarios multi_pool,multi_pool_declared,multi_pool_drift \
  --history-builds 150 --builds 200 --warmup-builds 40 \
  --interval-seconds 1800 --quality-gate \
  --output benchmark-results/pool-holdout
~~~

| Scenario | Hidden scheduling world | What the predictor receives |
|---|---|---|
| `steady` | Moderate single-pool arrivals | Prior observed VM jobs/slots |
| `burst` | All builds start together | Only currently visible queue and finished history |
| `overload` | Arrivals exceed capacity | Prior queue observations, no future arrivals |
| `drift` | Capacity/contention changes under congestion | Only already observed changes |
| `multi_pool` | Two independent VM pools and job eligibility | Pool IDs, **inferred** observed concurrency |
| `multi_pool_declared` | Same two-pool workload | Pool IDs and **explicit** scheduler limits |
| `multi_pool_drift` | Pool/capacity changes across historical/live workload | Known-at-time data, inferred slots |

The simulator uses priority-first/FIFO scheduling and virtual-clock job completions; it is not an unbiased benchmark of all real scheduling policies. A declared limit is treated as authoritative for the corresponding synthetic case; an inferred peak is a **lower bound** and may understate actual available slots.

## How to interpret results

Compare **legacy heuristic**, **raw scheduler**, **calibrated scheduler** and **selected River-gated** forecasts using **the same held-out first forecasts**. The suite excludes the first chronological warmup builds, records additional first snapshot per build-progress phase, and evaluates River shadow predictions against the raw scheduler only for the **matching shadow-ready subset**.

The optional `--quality-gate` tests non-regression (>5% MAE/P90 absolute-error tolerance on ≥3 seeds), not guaranteed improvement. Ground truth VM speed, slot count and true remaining work never enter predictor features.

Reports include `report.md`, `runs.csv`, `summary.json` and per-scenario/seed CSV/JSON. See [protocol](../docs/validation.md) for command semantics and [observations](../docs/observations.md) for **recorded** rather than proposed accuracy results.

**Limitations:** simplified priority/FIFO dispatch, invisible future arrivals, no dependencies/preemption, simplified contention and seeded speed distributions. Do not transfer simulator MAE directly to real CI infrastructure.
