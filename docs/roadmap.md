# Release roadmap and evidence gates

[← Documentation](README.md) · [Release contract](release-contract.md) · [Results](observations.md)

## Distribution roadmap (revised October 13, 2026)

Target: a trustworthy experimental `0.3.0rc1` distribution without a large
release framework. Keep runtime behavior, migration, chronology, delayed-label,
retention and restart evidence intact. One concern per PR; prefer existing
scripts and hosted features. Version-specific notes belong in GitHub Releases.

| Phase | Work / PR title | Gate |
|---|---|---|
| 0 | Verify merged CI | Green current `master` run; stable check names, real Python 3.15/River, clean artifact installs; failing regressions fail closed |
| 1 | `docs: consolidate release notes and contributor guides` | Recursive links/fragments pass; no Markdown-size or per-version-file tests |
| 3 | `security: protect releases and automate dependency review` | Active branch/tag rulesets, established required checks, dependency updates/review/scans and private disclosure route |
| 4 | `build: qualify Python distributions` | Confirm PyPI ownership, metadata/content audit, clean wheel/sdist installs and optional-extra checks |
| 5 | `build: qualify prebuilt container distribution` | Linux/amd64, image-only consumer Compose, exact pulled digest demo/CSV restart/persistence and backup smoke |
| 6 | `ci: publish approved versioned releases` | One workflow, protected tags/environment, OIDC, least privilege, immutable artifacts, safe partial-failure handling |
| 7 | `security: attest releases and generate container SBOM` | Checksums, image SBOM and provenance verified against exact artifact/digest identities |
| 8 | `release: qualify v0.3.0rc1` (or next unused RC) | Approved publication, actual consumer pip/pull rehearsal and persistence/verification; no RC `latest` |
| 9 | `security: publish OpenSSF Scorecard results` | Weekly restricted job, public authenticated results and SARIF; no perfect-score gate |

Phase 2 is independent optional maintenance: simplify benchmark wrappers only
when public CLI compatibility is preserved; centralize fixtures/configuration
only when validation is retained. Neither should delay release for marginal
cleanup. Do not duplicate the existing CI work or add separate recovery,
canary and rollback workflows. See the [release contract](release-contract.md)
for stable rules; per-PR checks remain consistent, with image/release gates on
relevant changes and every release.

### CI baseline audit

At this audit, [PR #1](https://github.com/HRAshton/vedrfolnir/pull/1) is merged
at `fde447a94a21cd7e045004e361d706a2b30cb382`.
[PR #2](https://github.com/HRAshton/vedrfolnir/pull/2) is still open; its head
`fc7cade6258c577ed4eede780347352ced871eea` has
[green hosted CI](https://github.com/HRAshton/vedrfolnir/actions/runs/38471381977):
stable CPython 3.15.0, real River 0.26.1, 231 tests, independent wheel/sdist
installs and HTTP/resource/entry-point checks. Stable job names are
**Python and package validation** and **Shell and workflow validation**.
PR/push triggers have no path filters; the token is read-only, Actions are
SHA-pinned, checkout credentials are not persisted and the River stub is
explicitly disabled. The old test forbidding workflows is removed. A local
intentional-failure probe made the quick gate fail (1 failed, 231 passed).

The live `master` still lacks that workflow and has no push CI run. Therefore
**green merged-master CI and required-check enforcement remain pending**;
Phase 1 proceeds stacked on #2. After #2 lands, record its actual merged SHA
and green `master` run before configuring required checks. Do not interpret
the roadmap's maintainer-reported merge as verification of a stale snapshot.
Historical preview context lives in [observations](observations.md#historical-repository-preview).

## Accuracy and operational evidence

Distribution checks do not establish scheduler accuracy. Preserve the following
research and operational milestones independently of the distribution PRs.

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

Each accuracy milestone requires executed Python 3.15 checks and a report
of actual commands, commit SHA, error statistics and limitations. Hosted CI
provides automated regression and packaging checks on its validated PR head;
passing CI alone does not establish accuracy. The [release contract](release-contract.md)
defines separate distribution gates.

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
investigation, **not evidence of scheduler improvement**. Historical v0.2.0
publication refers to the old repository; the recreated repository has no releases yet.
