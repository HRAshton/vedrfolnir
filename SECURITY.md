# Security

Vedrfolnir is experimental. Its dashboard and read-only API do **not** authenticate callers.
Bind to localhost as the default Compose configuration does, or place it behind an
authenticating reverse proxy. Do not expose it directly to the internet.

Only grant a read-only account for external MSSQL queries. Logging configuration files
are trusted code configuration: do not accept arbitrary uploads or untrusted handler classes.
Never commit `.env`, SQL credentials, production datasets or model checkpoints.
Model checkpoint deserialization uses pickle; only load trusted application-owned state.

Report vulnerabilities privately to the repository maintainers rather than in public issues.
