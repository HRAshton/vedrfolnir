# Job workload health and capacity changes  -  October 13, 2026

Base: `73f4b4956cb8162df962efe801d8dfaa7053fd50` (prospective monitoring).
Local runtime: stable CPython 3.15.0, real River 0.26.1, SQLAlchemy 2.0.54.

## Change and sampling

`GET /api/job-metrics/by-workload?group_by=pool` partitions the same latest
2,000 completed observations per status as headline job metrics. Supported
dimensions are `pool`, `job_type` and `load`. Cohorts preserve queued/running/
pending target separation, eligible/evaluated counts, exclusions, unavailable
reasons, MAE, P90 absolute error and signed bias. Existing summary fields keep
their behavior; the new endpoint adds no database columns or model training.

Load is local pool queue pressure captured with the first observation, not
current load reconstructed after completion. Queued jobs / effective slots
gives idle (<0.5), busy (0.5-<2) and saturated (≥2). Zero capacity is paused;
missing/invalid evidence is unknown. Inferred slots remain lower bounds.
Later capacity changes do not rewrite saved forecast values or cohort membership.

The dashboard breakdown follows the selected job prediction target and shows
eligibility, scored sample sizes and errors by the selected dimension. It uses
the full recent sample independently of the daily chart window. Fewer than
40 scored observations get a small-sample label; larger samples remain
descriptive. Rare groups may be absent from this bounded global sample.

## Executed validation

`bash scripts/local_check.sh full` passed: Ruff, **182 tests**, four simulator
smoke runs, wheel/sdist build and installed-wheel API/version/dashboard
resource checks. The restricted runtime used a writable TMPDIR and active
venv dependency path in PYTHONPATH for nested wheel verification. One
Starlette TestClient/httpx deprecation warning; no tests failed or skipped.

The new tests cover:

- Pool and job-type error isolation, target separation, unavailable first
  forecasts, administrative/invalid exclusions, and count reconciliation
  against headline metrics.
- Prediction-time load evidence, threshold boundaries, zero/missing/invalid
  capacity, and the shared global sample limit. Old groups are not backfilled
  with older records to improve their apparent sample sizes.
- Real CSV polling with a declared capacity increase from one to two slots:
  current queued wait changes from 6 minutes to zero while the stored first
  forecast remains 6 minutes. Reducing back to one slot restores a 5-minute
  wait at the next minute's clock. Delayed outcomes are scored against the
  original forecast; the first job's actual 1-minute wait produces +5 minutes
  signed error. Captured saturated cohorts remain saturated after capacity
  changes and terminal completion.
- Pause/resume with a service restart: queued deadlines are withheld during
  pause, running completion estimates remain available, and resumed eligible
  forecasts do not replace unavailable first observations. Those completed
  queued observations remain visible with zero scored forecasts.
- Unknown limits fall back to an observed lower bound with a warning. Negative
  declared capacity rejects the poll, preserves prior snapshot/captures and
  withholds live ETAs until a valid poll recovers. Null limits and a missing
  optional pool CSV both remove the previous declared limit. Missing live CSV
  and stale persisted clocks withhold ETAs.

`scripts/check_dashboard.cjs` passed with jsdom against local Uvicorn and the
existing eight-day synthetic monitoring fixture. It covers pool/job/load
selector changes, target isolation, small-sample labels and empty breakdowns,
plus existing table/chart/dialog/refresh/stale/error recovery checks. No
script/DOM errors. The DOM harness tests behavior and API integration, not
native browser layout, dialog rendering or accessibility.

## Reproduce and limits

~~~bash
python -m pytest -q tests/test_job_workloads.py tests/test_capacity_changes.py
bash scripts/local_check.sh full
~~~

For the optional DOM check, use the default eight-day fixture and commands in
the [monitoring validation report](2026-10-13-job-monitoring.md#reproduce).
All test sources are synthetic and isolated; no real SQL Server/source data
was accessed or written. Existing public trace evidence is unchanged. These
are regression and monitoring checks, not an independent accuracy experiment.
No estimator tuning, job River promotion or job P90 coverage is claimed.

Docker lifecycle and real-browser visual checks remain outstanding from the
prior RC checklist. No release/tag/deployment or GitHub Actions was dispatched.
