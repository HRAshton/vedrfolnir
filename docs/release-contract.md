# Release and support contract

[← Documentation](README.md) · [Development](development.md) · [Candidate notes](releases/v0.3.0.md)

## Identity and publication

The first release candidate is **0.3.0rc1**, tagged **v0.3.0rc1**. It is
unreleased: this recreated repository has no published releases yet. Historical
v0.2.0 notes describe the old repository. PyPI name ownership/availability is
unverified; do not configure a publisher until it is confirmed.

`src/vedrfolnir/__init__.py` contains the authoritative PEP 440 version literal.
Setuptools reads it through `tool.setuptools.dynamic.version`; runtime
`__version__`, installed package metadata and FastAPI's OpenAPI version must
agree. Reinstall after version edits before running metadata checks.

| Release | Package version | Protected Git tag / immutable OCI tag |
|---|---|---|
| Candidate | `X.Y.ZrcN` | `vX.Y.ZrcN` |
| Final | `X.Y.Z` | `vX.Y.Z` |

The intended OCI namespace is `ghcr.io/hrashton/vedrfolnir`. Version tags
must not be overwritten; the image digest is the canonical image identity.
RCs are GitHub prereleases and never move `latest`. Any preview alias must
remain separate from stable aliases. Final-release convenience aliases may
move only after all immutable publication paths succeed.

Hosted PR/push CI is planned next. Future publishing requires an explicit,
protected release tag on an approved default-branch commit and protected
environment approval. Pull-request contexts and arbitrary merges never
publish. This contract adds no workflows, release tags, credentials or
publication. PyPI/GHCR availability must be verified before consumer install
commands are advertised.

## Supported deployment and limits

- Initial official OCI target: **Linux/amd64**, one host, one application
  instance and one Uvicorn worker per SQLite database. Other architectures
  and native Windows/macOS pip installs are unqualified.
- Pip runtime: **CPython 3.15 only** (`>=3.15,<3.16`). Clean installation with
  real River and the advertised dependencies remains a release gate; testing
  other interpreters does not expand support.
- Initial official image: demo and normalized CSV sources, built with
  `INSTALL_MSSQL=false`. It contains no optional MSSQL driver and does not
  advertise SQL Server support. `.[mssql]` / `INSTALL_MSSQL=true` remain
  optional source-build routes for experiments with SELECT-only accounts.
  There is no promised `-mssql` image flavor: one may be added only after
  separate driver/platform and disposable SQL Server integration validation.
- The source-build `compose.yaml` remains the developer/current deployment
  route. A future consumer Compose file must use a qualified version or
  digest via `image:` without `build:`. Prebuilt images are not available yet.
- Read API, dashboard and OpenAPI endpoints have no authentication. Bind HTTP
  to `127.0.0.1` by default; remote access requires an authenticating TLS
  reverse proxy. The admin token protects sync, not read endpoints. Keep
  source credentials private and source mounts read-only.
- SQLite stores application history, forecasts and model state in persistent
  local storage (`/data` in the container). Source SQL Server is input only.
  Keep each source mode in separate state. Compose shutdown preserves the
  named volume; `down -v` destroys it. Before upgrades, use SQLite's backup
  API or stop the service before copying the database; test restoration.
  See [operations](operations.md) for schema/backup procedures. Never load
  untrusted pickle checkpoints; dependency upgrades may break compatibility.
- Experimental ETA accuracy on arbitrary production schedulers is unproven.
  No high availability, multiple polling writers, generic DAG dependencies,
  reservations/preemption or unknown future-arrival prediction is promised.
  Packaging qualification does not replace chronological accuracy evidence.

## Artifact names and identity

For the first candidate, all artifacts must refer to the same approved Git SHA.
Names below are the agreed future publication contract, not existing downloads.

| Artifact | Candidate name / reference |
|---|---|
| Wheel | `vedrfolnir-0.3.0rc1-py3-none-any.whl` |
| Source distribution | `vedrfolnir-0.3.0rc1.tar.gz` |
| OCI image | `ghcr.io/hrashton/vedrfolnir:v0.3.0rc1` |
| Canonical OCI identity | `ghcr.io/hrashton/vedrfolnir@sha256:<qualified-digest>` |
| Checksums | `SHA256SUMS` (SHA-256 for downloadable files except itself) |
| Container SBOM | `vedrfolnir-0.3.0rc1-container.spdx.json` (SPDX JSON from distributed image) |
| Release notes | `RELEASE_NOTES.md` (GitHub prerelease body; maintained in `docs/releases/v0.3.0.md`) |

Future release notes must record the source SHA, Python/River versions,
executed validation, package hashes, image digest, support limits and provenance
verification instructions. SBOM and attestation generation belong to subsequent
supply-chain work; their presence does not certify absence of vulnerabilities.

## PR and release gates

Use Conventional Commits for PR titles and commits, preferably a single-line
subject with no body; preserve that subject when squashing. GitHub-generated
merge commits are allowed. Example: `build: define release metadata and support contract`.

Before publication, require fresh Python 3.15/real River checks, wheel and sdist
builds plus clean installs, API/resource/console-script verification, pulled-image
demo/CSV startup/restart/persistence smoke, schema upgrade/backup/restore checks,
security checks and source/version/tag equality. Add required status checks only
after hosted jobs exist and pass. Protect release tags before enabling publishing;
use narrowly scoped OIDC publishing permissions rather than persistent tokens.
