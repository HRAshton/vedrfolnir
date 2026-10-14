# Security

Vedrfolnir is experimental. Its dashboard and read-only API do **not** authenticate callers.
Bind to localhost as the default Compose configuration does, or place it behind an
authenticating reverse proxy. Do not expose it directly to the internet.

Only grant a read-only account for external MSSQL queries. Logging configuration files
are trusted code configuration: do not accept arbitrary uploads or untrusted handler classes.
Never commit `.env`, SQL credentials, production datasets or model checkpoints.
Model checkpoint deserialization uses pickle; only load trusted application-owned state.

Report vulnerabilities privately to the repository maintainers rather than in public issues.

## Disclosure and supported versions

Use [GitHub private vulnerability reporting](https://github.com/HRAshton/vedrfolnir/security/advisories/new)
when the owner has enabled it. Verify the private-report form is available;
if it is not, the private disclosure route is not configured and the owner
must enable it before publication. Do not put exploits or private source
records in public issues. Enabling this reporting form is a release gate.

Until a qualified public candidate exists, only the current default-branch
source is maintained; old-repository previews receive no promised security
updates. After publication, the latest candidate is supported for experimental
use until superseded. This is not a production-support SLA.

Dependency review blocks new high/critical advisories; the resolved Python
environment audit reports all known findings. No blanket license block is
configured: review material license changes in dependency PRs. Exceptions
must identify the advisory, affected version, justification, owner and expiry
in a reviewed PR; the default contains no ignores. Renewals require review.
