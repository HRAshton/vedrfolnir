# Container runtime constraints

The official Linux/amd64 image installs exact Python runtime package versions
from `requirements/container-constraints.txt`. These pins were resolved on a
clean Linux CPython 3.15 GitHub Actions runner from the built Vedrfolnir wheel,
not from an editable development environment.

After intentional runtime dependency updates, regenerate on a clean Linux/amd64
CPython 3.15 host with project build dependencies installed:

```bash
python scripts/generate_container_constraints.py
python scripts/generate_container_constraints.py --check
```

The generator builds the actual wheel, installs it in a clean venv, and records
all resolved installed packages except Vedrfolnir and packaging bootstrap tools.
Review each changed package and its security implications before committing.
`--check` installs **under the committed constraints** and compares the exact
resolved inventory against the file. New upstream releases alone therefore do
not cause unrelated CI failures.

The Dockerfile uses `pip --constraint` and `pip check`; CI independently
checks the full installed package inventory of the built official image against
the committed constraints, then runs demo/CSV/restart smoke and the existing
image vulnerability gate. An unexpected extra/missing package or version
fails the check even if pip installation succeeded.

The optional MSSQL driver is not part of the official image lock or its CI
qualification; `INSTALL_MSSQL=true` remains an unqualified image variant.
Package consumers still get compatible dependency ranges from `pyproject.toml`.

Version-only constraints do not pin artifact hashes; they do not guarantee
bit-reproducible images. OS packages depend on the pinned Debian base and the
apt repository contents, separately from these Python constraints.
