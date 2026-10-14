# Release and support contract

[← Documentation](README.md) · [Development](development.md) · [Operations](operations.md)

## Version and publication rules

`src/vedrfolnir/__init__.py` holds the authoritative PEP 440 `__version__`.
Setuptools derives package metadata from it; installed metadata, runtime,
CLI and FastAPI versions must agree.

| Release | Package version | Immutable Git / OCI tag |
|---|---|---|
| Candidate | `X.Y.ZrcN` | `vX.Y.ZrcN` |
| Final | `X.Y.Z` | `vX.Y.Z` |

Images use `ghcr.io/hrashton/vedrfolnir`; the digest is their canonical
identity. Never move release tags or replace an existing public version with
different bytes. RCs are prereleases and never update `latest`; stable aliases
move only after immutable publication succeeds.

Testing is read-only and cannot publish. Publication requires an explicit,
protected release tag on an approved default-branch commit and an approved
release environment. No publication from PR contexts or arbitrary merges.
Confirm PyPI name ownership before configuring Trusted Publishing; pending
publishers do not reserve names. Availability and consumer commands must be
verified against real published artifacts.

## Supported scope

- Initial official container: **Linux/amd64**, demo and normalized CSV, without
  the optional MSSQL driver. One host, one instance and one worker per SQLite
  database; other architectures and high availability are unqualified.
- Packages require **CPython 3.15 only** (`>=3.15,<3.16`). Native Windows/macOS
  installs and SQL Server compatibility are unqualified. `.[mssql]` and
  `INSTALL_MSSQL=true` are optional source-build experiments until a separate
  disposable SELECT-only integration test qualifies them.
- Experimental ETAs have no production accuracy guarantee. Read API/UI
  endpoints are unauthenticated; keep loopback binding and require an
  authenticating TLS proxy for remote access. Persistence, backups, model
  trust and upgrade assumptions live in [operations](operations.md).

## Release gates and evidence

Require green Python 3.15/real River checks, clean wheel/sdist installs with
API/resources/entry points, qualified **pulled-image** demo/CSV startup,
restart and persistence, SQLite migration/backup/restore coverage, security
checks and source/version/tag equality. Protect checks and release refs before
enabling narrowly scoped OIDC publication. Commands live in
[development](development.md); accuracy evidence remains a separate gate.

Version-specific notes belong in the
[GitHub Release body](https://github.com/HRAshton/vedrfolnir/releases), not
per-version Markdown files. Record the reviewed source SHA, executed checks,
Python/River versions, wheel/sdist hashes, OCI digest, support limits and
verification instructions there. Attach distributions, `SHA256SUMS` and an
SBOM of the distributed image; attest the exact artifacts/digest. These are
subsequent publication gates, not claims that downloads exist or are secure.
