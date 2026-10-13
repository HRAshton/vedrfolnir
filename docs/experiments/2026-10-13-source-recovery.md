# Source recovery and release checks  -  October 13, 2026

Base: `e3434e077674458c443925a5db1b355b2fafbec1` (job workload health).
Runtime: stable CPython 3.15.0, real River 0.26.1, SQLAlchemy 2.0.54.

## Correctness findings and changes

The importer previously checked only that the final key exceeded the saved
cursor. An internally out-of-order page could leave the cursor behind its
maximum returned key and later skip unseen rows. Every historical page now
must fit its requested budget and strictly increase `(finished_at, string
run_id)` from the saved cursor through every row. Future-dated completions
are rejected before insertion, preventing a clock error from jumping the
cursor past later legitimate completions. Preflight shares these checks and
reports only row numbers/codes. The rejected page does not commit; previously
completed pages remain intact. Existing SQLite schema is unchanged.

Workflow completion previously allowed future-dated terminal plans to label
predictions and train River immediately. Completion now must be at or before
the poll clock. Recent-completion features and labelled calibration/shadow
samples also exclude future finishes. The integration test now explicitly
checks no early labels/training, then advances the clock and verifies exactly
one training event. Existing job duration/outcome clock guards are preserved.
This does not undo training already performed by older code on incorrect clocks.

The Docker smoke now checks job APIs and retained first observations, uses
unique container/volume names, and explicitly tests stop/start plus graceful
shutdown logs. Uvicorn's normal post-cleanup SIGTERM exit (143 in Docker) is
accepted; forced-kill/OOM exits are rejected. No existing volume is selected
by a fixed smoke-test name. **The modified Docker gate was not executed here.**

## Executed local checks

`bash scripts/local_check.sh full` passed: Ruff, **194 tests**, four simulator
smoke runs, wheel/sdist build and installed-wheel API/version/static-resource
checks. The restricted runtime used a writable TMPDIR and the active venv
dependency path in PYTHONPATH for nested wheel verification. One Starlette
TestClient/httpx deprecation warning; no failures or skips.

New recovery tests cover:

- Invalid internal order, replayed old keys, oversized pages and future
  completions: no page rows or cursor committed.
- A second-page source failure: the first two rows/cursor remain committed;
  a restarted service resumes with the remaining three rows, without loss
  or duplicate storage.
- Injected failure at history insertion or cursor write: both rows and
  cursor roll back together and a retry imports all rows.
- **Abrupt subprocess exit** during cursor write after row insertion, without
  Python rollback/connection cleanup: SQLite WAL recovery exposes neither
  the uncommitted row nor cursor; a restarted service imports it successfully.
- Tied timestamps with lexical IDs `1`, `10`, `2`, `3`, bounded per-poll
  pagination, and recovery of a valid completion after a future page rejection.
- Read-only preflight diagnostics without raw private identifiers.

`python -m scripts.native_release_smoke` passed against real Uvicorn processes:
fresh synthetic CSV startup/import, health/version/dashboard/workflow/job
APIs, all workload dimensions, unauthenticated sync rejection and authenticated
sync success. The second process reopened the same temporary database:
**6 history rows and 2 original job observations were preserved**. Both
processes logged completed lifespan shutdown. The helper uses an available
localhost port, temporary source/database, and invocation-specific admin token.
Its bounded shutdown wait uses `Popen.poll()` because this custom local Python
lacks `select.poll` for subprocess timed waits. Normal post-cleanup SIGTERM
return codes were verified alongside shutdown logs.

## Reproduce and remaining gates

~~~bash
python -m pytest -q tests/test_import_recovery.py tests/test_service.py \
  tests/test_source_validation.py tests/test_ingestion.py
bash scripts/local_check.sh full
python -m scripts.native_release_smoke
# On a host with Docker:
bash scripts/local_check.sh docker
~~~

No Docker/Podman executable or daemon socket is available here. Native
checks do not verify image construction, container permissions, mounts or
Docker networking. Downloaded Chromium still crashes even on `--version`;
native desktop/mobile rendering remains unverified. Previous DOM/API checks
remain the available dashboard evidence. Actual SQL Server and long-running
deployment checks also remain outstanding.

The page guard cannot recover rows the source inserts later behind its
completion cursor or detect records never returned. SQL ordering/collation
must agree with canonical lexical IDs; see [source contracts](../contracts.md).
The crash test verifies SQLite transaction recovery for this local file/WAL
configuration, not arbitrary storage hardware/power failures.

No accuracy retuning, new independent accuracy benchmark, GitHub Actions,
release/tag, deployment or publication to PyPI was performed. The historical
v0.2.0 tag and package version in the old repository were unchanged.


## Follow-up: bounded late-history recovery

The original limitation above accurately describes the v0.2.0 importer's
single forward cursor. A subsequent operational PR adds an independent
checkpointed overlapping replay window. It improves recovery for late
**insertions within a configured completion-time lookback**, not arbitrary
older backfills or changes to an already-imported run ID. The new policy and
retention tradeoffs are documented in [history lifecycle](../history-lifecycle.md).
