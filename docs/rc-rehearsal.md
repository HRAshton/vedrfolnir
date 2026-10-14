# Release candidate rehearsal and remaining approvals

[← Documentation](README.md) · [Verification](verification.md) · [Release setup](release-setup.md)

The target is the first unused `0.3.0rcN`, initially `0.3.0rc1`. The package
source already holds the candidate version; do not create a final tag accidentally.
No RC is published by this PR. Complete owner protections, publisher setup,
security findings and qualified image gates before authorizing a protected tag.
Merge the rehearsal workflow with passing code checks before publishing the RC;
public consumer evidence is a post-publication qualification gate, not a
prerequisite for merging its implementation.

Review the GitHub Release body for Linux/amd64, Python 3.15, demo/CSV,
one worker/SQLite instance, loopback binding, backups, absent MSSQL support and
unproven real-scheduler accuracy. Record actual runs, tool versions and digests.
A reviewed tag must publish identical packages and one canonical OCI digest.

After publication, dispatch the **Consumer rehearsal** workflow on `master` with
`version` and the published canonical `image` digest. This separate workflow
performs a **read-only consumer rehearsal**. The **Release** workflow accepts
protected tag pushes only, keeping publishing checkouts out of the manual
dispatch's default-branch cache context. Rehearsal checks downloaded GitHub asset
hashes/sizes, manifest/source identity, package and OCI attestations, public PyPI
wheel bytes, a fresh PyPI install/CLI/API, and pulled demo/CSV image persistence,
backup/restore and shutdown. The verifier runs from protected `master`
and checks the published manifest's source SHA against the resolved immutable tag.
It does not run project code checked out from dispatch-selected tags.

Remaining manual gates: review README/consumer Compose commands against the
published version/digest; test authentication/public exposure and writable mounts;
review retained-data upgrades and retention under representative usage; rehearse
partial publication/conflicts on disposable versions or TestPyPI, never overwriting
production artifacts. No SQL Server or production ETA claim follows from synthetic
smoke. Close the RC exercise only after actual public download/pull evidence exists.
