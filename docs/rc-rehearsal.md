# Release candidate rehearsal and remaining approvals

[← Documentation](README.md) · [Verification](verification.md) · [Release setup](release-setup.md)

The target is the first unused `0.3.0rcN`, initially `0.3.0rc1`. The package
source already holds the candidate version; do not create a final tag accidentally.
No RC is published by this PR. Complete owner protections, publisher setup,
security findings and qualified image gates before authorizing a protected tag.

Review the GitHub Release body for Linux/amd64, Python 3.15, demo/CSV,
one worker/SQLite instance, loopback binding, backups, absent MSSQL support and
unproven real-scheduler accuracy. Record actual runs, tool versions and digests.
A reviewed tag must publish identical packages and one canonical OCI digest.

After publication, dispatch the **Release** workflow with `version` and the
published canonical `image` digest. Dispatch performs a **read-only consumer
rehearsal**; it cannot enter publishing jobs. It checks downloaded GitHub asset
hashes/sizes, manifest/source identity, package and OCI attestations, public PyPI
wheel bytes, a fresh PyPI install/CLI/API, and pulled demo/CSV image persistence,
backup/restore and shutdown. It checks the actual tagged source, not a later master.

Remaining manual gates: review README/consumer Compose commands against the
published version/digest; test authentication/public exposure and writable mounts;
review retained-data upgrades and retention under representative usage; rehearse
partial publication/conflicts on disposable versions or TestPyPI, never overwriting
production artifacts. No SQL Server or production ETA claim follows from synthetic
smoke. Close the RC exercise only after actual public download/pull evidence exists.
