# Read-only SQL Server mapping example

`sample_schema.sql` is an optional **write-containing demo fixture**. Run it
manually only in a disposable SQL Server database that you control (never on a
production source). The application itself reads only the four `queries/*.sql`
SELECTs through a **read-only SQL login**.

The included query mappings match `dbo.EtaDemoJobs`, `dbo.EtaDemoPlans`, and
`dbo.EtaDemoPools` created by that fixture. Queries preserve:
- The completed-history `TOP` limit, `(FinishedAt, RunId)` cursor and order.
- Completed jobs belonging to still-active plans (plus competing queue work).
- Active or recently completed plans for delayed outcome labeling.
- An optional declared two-slot runner pool.

The planned official demo/CSV image omits the MSSQL driver. This is an
optional source-build route; it requires separate integration validation
and does not establish official SQL Server support.

To run with source-build Compose, set `SOURCE_MODE=mssql`, `INSTALL_MSSQL=true`,
`SQL_QUERY_PATH=./examples/quickstart/sqlserver/queries` and configure the
`MSSQL_*` connection variables in private `.env`. Build and preflight with
`docker compose run --rm vedrfolnir vedrfolnir-check-source --mode mssql
--query-dir /srv/queries`. See [the full setup guide](../../../docs/quickstart.md).

This is a **schema mapping example**, not a universal query or evidence that
the ETA model fits your scheduler. SQL Server datetime2 values represent UTC
by convention; map your actual source timestamps accordingly.
