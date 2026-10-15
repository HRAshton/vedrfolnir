# Container runtime constraints

The runtime dependency set used by the published Linux/amd64 image should be
fully pinned after resolution with **real CPython 3.15**, not guessed from
project minimum versions or frozen from a developer's editable environment.

From a clean Python 3.15 host with the project's build dependencies installed:

```bash
python scripts/generate_container_constraints.py
python scripts/generate_container_constraints.py --check
```

The generator builds the actual Vedrfolnir wheel, installs it in a clean
environment, and records all resolved installed package versions except the
project and Python's packaging bootstrap tools.

**This draft introduces the reproducible generator and policy tests; it does not
yet claim an enforced lock.** Before merging, generate and review
`requirements/container-constraints.txt` on Linux/amd64, update the
Dockerfile to install with `--constraint`, and add a PR check that verifies
the resulting installed inventory matches the file. Runtime dependency changes
must regenerate this lock; package users still receive the compatible version
ranges from `pyproject.toml`.

OS packages are separately tied to the pinned Debian image plus its apt
repository contents; this Python lock does not freeze all OS packages or
guarantee bit-reproducible container builds.
