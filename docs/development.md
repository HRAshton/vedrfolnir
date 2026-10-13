# Development, tests and releases

[← Documentation](README.md) · [Architecture](architecture.md) · [Validation](validation.md)

**Supported developer runtime: CPython 3.15 only.**

~~~bash
python -m pip install -e ".[dev]"
ruff check src tests
python -m pytest -q
python -m build
~~~

For SQL Server experiments install `.[mssql,dev]`. Run pytest with real River in the local release checks; do not replace it with a stub when checking ML behavior.

## Source layout

| Directory | Responsibility |
|---|---|
| `src/vedrfolnir/source.py`, `source_validation.py` | Read-only CSV/MSSQL ingestion, aliases, validation |
| `src/vedrfolnir/db.py`, `service.py` | SQLite storage, source polling, labels, forecast orchestration |
| `src/vedrfolnir/pooling.py`, `adaptive.py` | Observed/declared capacity, queue replay, saturated bias and River gate |
| `src/vedrfolnir/features.py`, `uncertainty.py`, `metrics.py` | Duration/plan features, empirical P90, measured errors |
| `src/vedrfolnir/api.py`, `static/` | API endpoints and existing browser UI |
| `simulator/` | Seeded virtual-clock scheduler, validation and per-build CSV |
| `src/vedrfolnir/benchmarking/` | Separate public-trace loaders/evaluators |
| `tests/` | Contract, scheduler, River integration, simulation and regression tests |

## Reproducibility principles

1. Distinguish **prediction-time available inputs** from hidden future labels. No outcome-derived features in a forecast.
2. Train after completion, never at the time of an as-yet-unseen result.
3. Keep identical scored builds for comparison among legacy/raw/calibrated/selected predictors.
4. Evaluate shadow River only against the baseline on **the same eligible subset**.
5. Bootstrap **independent seeds**, not correlated jobs/polls; keep a separate unseen holdout.
6. Record wall-clock times, workload scenario, simulator seed, historical reconstruction mode and Git SHA with published benchmark metrics.

**CI plan:** add hosted checks for PRs and pushes to `master` in the next
release-engineering PR. No hosted workflow exists yet. Until then validate
on a local CPython 3.15 environment. Run `bash scripts/local_check.sh quick` for
Ruff and real River tests; `full` adds quick simulation, isolated wheel
and sdist; the explicit `docker` mode builds the image and tests
startup/restart. Historical Actions evidence belongs to the old repository
and does not establish validation of this recreated repository.

## Dependency and storage compatibility

`pyproject.toml` is the authoritative requirement specification. The optional `.[mssql]` extra installs pymssql. Runtime depends on River 0.26.x and SQLAlchemy 2.0.x; the package currently declares `>=3.15,<3.16`.

SQLite schema initialization uses `create_all` plus an explicit versioned migration (SQLite `PRAGMA user_version` v1) for legacy `sched_pool_id` columns. Each further schema change must add an ordered, tested versioned step; do not silently assume a new schema. River pickle compatibility across dependency upgrades is not guaranteed; never load an untrusted model.

There is no committed cross-platform lockfile in this experimental release; release engineering should resolve and verify dependencies on Python 3.15 and pin deployment images as needed.

## Scope limitations and contribution workflow

Pool isolation and priority/FIFO scheduling exist; DAG dependencies, user-configurable queue policies, reservations/preemption and generic `feat_*` extensions are not implemented. Prefer tests and explicit fallbacks over claims that the algorithm inferred invisible constraints. See [source contracts](contracts.md) and [measured limitations](observations.md).

## Release and PR policy

`src/vedrfolnir/__init__.py` is the single authoritative package version;
setuptools derives distribution metadata from its `__version__` literal and
FastAPI uses the same value. Change that source value for each release and
reinstall the package before checking metadata equality.

Use Conventional Commits for PR titles and commit subjects, preferably one
line with no body. Current history uses `feat:`, `test:`, `build:`, `docs:`
and `chore:` prefixes. Release engineering may also use `ci:`, `security:`
and `release:`. Squash subjects must retain the PR's conventional title;
GitHub-generated merge commits are allowed.

The first candidate is `0.3.0rc1` / tag `v0.3.0rc1`. Hosted CI and future
PyPI/GHCR publishing are authorized directions, but require their own
qualification and protection milestones. Publication will run only from
explicit, protected release tags with environment approval, never from PR
contexts or arbitrary merges. No tag or publication is created by this PR.
See the [release contract](release-contract.md) for platforms, image flavors,
artifact names, tags and gates. Local quick/full checks and Docker smoke
remain required evidence alongside future hosted checks.

See [validation](validation.md) and [roadmap](roadmap.md).
