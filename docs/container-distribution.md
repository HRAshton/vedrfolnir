# Prebuilt container qualification

[← Documentation](README.md) · [Release contract](release-contract.md)

The consumer `compose.release.yaml` uses an explicit qualified GHCR reference;
it never builds locally. Until a real image is published, use source Compose.
After publication, copy the version/digest from the GitHub Release:

~~~bash
# Set the actual released ghcr.io/hrashton/vedrfolnir@sha256:... in VEDRFOLNIR_IMAGE
# Configure a unique ETA_ADMIN_TOKEN; SOURCE_MODE=demo or files
# For files, point CSV_SOURCE_PATH to your normalized export directory
docker compose -f compose.release.yaml pull
docker compose -f compose.release.yaml up -d
~~~

Only Linux/amd64, one host/worker and demo/CSV are qualified targets. SQLite
uses persistent `/data`; read sources are mounted read-only. The official
image omits the MSSQL driver, runs as UID 10001 and checks HTTP liveness.
Use independent volumes per source mode, back up before upgrades and protect
remote access with an authenticating TLS proxy. Bindings default to loopback.

Verify the actual downloaded digest with the existing smoke:

~~~bash
python scripts/release_smoke.py --pull --image "$VEDRFOLNIR_IMAGE"
~~~

`--pull` rejects mutable tags. Smoke verifies UID, stable Python, absent MSSQL
driver, demo/CSV readiness, protected sync, restart, preserved observations,
SQLite backup/restore and graceful shutdown. A locally built image is useful
PR evidence but does not qualify a published digest. Docker is required.

The CI container job builds and tests relevant code/configuration changes;
documentation-only PRs report a successful job without paying the image cost.
The release workflow always qualifies the built and subsequently pulled image.
Container scanning always retains the full Grype JSON report. A separate
risk gate blocks High/Critical findings unless they match an explicit CVE,
package name, and exact version in
[`security/container-risk-acceptances.json`](../security/container-risk-acceptances.json).
Only High findings with no scanner-reported fix and a reviewed unexpired risk
record are accepted. Critical, newly fixable, new CVEs, changed package
versions, invalid/expired decisions, different distros or missing scanner data
fail. The unfiltered report and policy decision are saved as CI artifacts.

Acceptances cover Debian 13's nonroot UID 10001 single-worker demo/CSV
service: no privileged mounts, nsenter, ACL administration, or untrusted
Perl/ncurses utility processing. Reassess if these assumptions change.
Current acceptances expire **2026-10-28**, requiring review and renewal;
passing the policy is risk acceptance, not a claim of zero vulnerabilities.
Base/Python checksum updates require review and complete smoke.
Other architectures and SQL Server remain unqualified.

## Security-qualified build inputs

The image uses a pinned Debian 13 stable base and installs its current security
updates. It builds checksum-verified CPython 3.15.0 and Expat 2.9.0, linking
Python to the fixed external parser rather than the bundled 2.8.5. Runtime
smoke checks pyexpat version as well as interpreter/UID/driver scope. Review
Python and Expat source checksums together with base-digest updates.

This replaces bookworm findings shown by the image audit. Debian tracks
[glibc CVE-2026-5450](https://security-tracker.debian.org/tracker/CVE-2026-5450)
and [libtasn1 CVE-2025-13151](https://security-tracker.debian.org/tracker/CVE-2025-13151)
as fixed in trixie, while [Expat CVE-2026-77214](https://security-tracker.debian.org/tracker/CVE-2026-77214)
requires the newer parser. No blanket ignores or severity relaxation are added;
the fresh smoke and scan must qualify the exact new image.
