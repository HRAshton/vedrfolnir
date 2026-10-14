# Distribution qualification

[← Documentation](README.md) · [Release contract](release-contract.md)

Build from a clean checkout using stable CPython 3.15 and development extras:

~~~bash
python scripts/qualify_packages.py --dist dist --mssql
~~~

Use an empty output directory. The command builds wheel and sdist with isolated
backend dependencies, validates metadata using Twine, checks contents for private
state/caches, installs both into independent non-editable environments and checks
CLI version, doctor, dashboard resources and real HTTP/OpenAPI serving. `--mssql`
separately tests driver installation/import; it does not validate SQL Server.
`SHA256SUMS` describes the exact qualified distributions. Do not rebuild those
files before publishing them; release jobs transfer the same checked artifacts.

On October 13, 2026, the PyPI JSON endpoint for `vedrfolnir` returned 404.
That is not ownership or reservation: confirm availability and owner control
immediately before configuring the exact repository/workflow/environment as a
Trusted Publisher. A pending publisher does not reserve the name. There is no
configured publisher or advertised public package yet. A disposable TestPyPI
rehearsal uses a distinct publisher and never combines indexes for dependencies.

Qualification is Linux/Python 3.15 only. Other OSes and real MSSQL integration
remain unqualified. Publishing and artifact provenance have separate gates.
