# Release roadmap and evidence gates

[← Documentation](README.md) · [Preview notes](releases/v0.2.0.md) · [Results](observations.md)

## 0.2.0  -  experimental preview

Usable by early adopters for experiments, **not production ETA promises**.
Release gate: Python 3.15 tests and package build, Docker demo and
read-only CSV source startup/API/restart, schema upgrade and SQLite
backup/restore tests. Version, artifacts and prerelease tag match.
These checks do not validate an unfamiliar real SQL Server deployment.

## 0.3.0  -  multi-job workload accuracy

A strict WfCommons per-task timeline reader and production-scheduler replay
adapter are implemented for external evaluation, but WfCommons lacks
queue-admission timestamps and some Snakemake workflow/task timestamps
contradict each other. A measured **task-span proxy** is available, but
cannot establish full launch-to-terminal ETA. The remaining evidence gate is an independent
chronological run plus genuine multi-job traces with observed task admission,
execution, pool/VM assignment and ideally authoritative capacity. Compare scheduler-only, simple
statistical correction and shadow River using the **same** prospective
completed-build subset. Report bias, MAE, P90 absolute error and P90
upper ETA coverage across loads and time. Evaluate declared vs inferred
capacity under actual contention. Generalization across new traces - not
synthetic tuning alone - is the gate.

## 0.4.0  -  integration and reliability

Exercise a representative real SQL/CSV installation across import,
forecast, terminal labels, downtime/reconnect, stale snapshots and
changed pool limits. Demonstrate tested backups, supported migrations,
restart idempotency, checkpoint upgrades and bounded state growth.
Stabilize source aliases, API behavior and operational procedures.

## 1.0  -  stable within documented assumptions

Requires external multi-job accuracy evidence **and** tested operational
reliability. P90 coverage must be measured prospectively with adequate
samples, not inferred from the same calibration cohort. Missing
dependencies, capacity or unknown scheduler policies must produce
honest warnings/fallback. No arbitrary release date.

Not on the critical path: multiple per-pool ML models, hyperparameter
search, UI redesign, or advanced queue-policy simulation without
input evidence.


## Local execution and acceptance criteria

Each milestone requires **locally executed**
Python 3.15 checks and a report of actual commands, commit SHA, error
statistics and limitations - not a GitHub Actions check.

| Priority | Work package | Local acceptance evidence |
|---|---|---|
| P0 | Explain first-forecast scheduler eligibility | Shared API/replay fallback codes, first-per-phase reason counts, and a separate finite-capacity positive control. Staggered admission remains a negative control; no future timestamps are fabricated. |
| P0 implemented | Individual queued-job start and finish forecasts | Read API with observed queue/capacity, persisted source freshness and explicit unsupported cases; replay scores queued wait/finish separately from running finish. A pending sibling does not block admitted work. See [job API](job-forecasts.md). |
| P0 implemented | Prospective job recording and dashboard | Successful polls save first observations per run/status; delayed labels, distinct wait/finish cohorts, daily error trends, source health, CSV export and configurable retention. Synthetic CSV lifecycle covers restart, source recovery and backup/restore. |
| P1 implemented | Job workload health and capacity-change regression coverage | Captured pool/job-type/load cohorts, eligibility and error counts, small-sample labels; real CSV tests cover capacity increase/reduction, pause/resume, restart and rejected/stale snapshots. No independent accuracy claim. |
| P0 next | Real pilot and operational RC checks | Verify actual queue policy, source clocks and deployment; report availability, bias and tail errors on chronological held-out work. Docker startup/API/import/shutdown remains a release gate. |
| P0 | Compare scheduler vs simple correction vs River | Chronological matched plans with baseline, raw, calibrated, selected and shadow errors; shadow only on the matching subset. |
| P1 | Real multi-job queue data | Independently collected runs with actual task admission, start/finish, pool/VM and capacity observations, with provenance and clock audit. Never fabricate admissions from start times. |
| P1 | Tail reliability and capacity drift | Measured P90 upper-ETA coverage by cohort, signed bias and P90 absolute error; inferred vs declared capacity under real contention. |
| P2 | Integration and upgrade | Non-demo SQL/CSV lifecycle, delayed labels, interrupted sync, backup/restore, migrations, security and sustained operation. |
| P2 implemented locally | Interrupted history recovery and clock guards | Ordered/bounded/completed-page validation; rollback/resume and abrupt subprocess exit during SQLite cursor write; future workflow labels/training deferred; native CSV startup/API/restart/graceful shutdown. Docker/actual SQL/sustained deployment remain gates. |
| Release | 0.3, 0.4, 1.0 | Explicit manual approval after local test/accuracy/operational gates. |

The independent October 12 research run had 29 scored Snakemake
**task-span** first forecasts, 0 scheduler-eligible; a single StreamFlow
full-timeline plan, 0 eligible; and 40 scored synthetic CSV plans with
admission observed but still 0 eligible. This is an observability
investigation, **not evidence of scheduler improvement**. Published v0.2.0
is unchanged.
