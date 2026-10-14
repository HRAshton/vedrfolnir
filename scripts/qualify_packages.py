"""Build, audit and install the exact wheel/sdist that will be distributed."""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile


def audit_members(names):
    for name in names:
        path = PurePosixPath(name)
        assert not path.is_absolute() and ".." not in path.parts, name
        assert not any(part in {".git", ".venv", "datasets", "benchmark-results", "__pycache__"}
                       for part in path.parts), name
        assert path.name not in {".env", "credentials.json"}, name
        assert path.suffix not in {".sqlite", ".sqlite3", ".db", ".pickle", ".pkl", ".pyc"}, name


def audit_artifact(path, expected):
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            metadata_paths = [n for n in names if n.endswith(".dist-info/METADATA")]
            assert len(metadata_paths) == 1
            metadata = archive.read(metadata_paths[0])
            assert "vedrfolnir/static/dashboard.html" in names
            assert "vedrfolnir/__init__.py" in names
    else:
        with tarfile.open(path) as archive:
            names = archive.getnames()
            prefix = f"vedrfolnir-{expected}/"
            assert prefix + "src/vedrfolnir/static/dashboard.html" in names
            assert prefix + "pyproject.toml" in names
            metadata = archive.extractfile(prefix + "PKG-INFO").read()
    audit_members(names)
    info = BytesParser().parsebytes(metadata)
    assert info["Name"] == "vedrfolnir" and info["Version"] == expected
    assert info["Requires-Python"] == "<3.16,>=3.15"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def qualify(dist, build=True, mssql=False):
    from vedrfolnir import __version__

    assert sys.version_info[:2] == (3, 15), sys.version
    dist = dist.resolve()
    if build:
        assert not dist.exists() or not any(dist.iterdir()), "Use an empty distribution directory"
        subprocess.run([sys.executable, "-m", "build", "--outdir", str(dist)], check=True)
    wheel = dist / f"vedrfolnir-{__version__}-py3-none-any.whl"
    sdist = dist / f"vedrfolnir-{__version__}.tar.gz"
    assert sorted(p.name for p in dist.iterdir()) == sorted([wheel.name, sdist.name])
    hashes = {p.name: audit_artifact(p, __version__) for p in (wheel, sdist)}
    subprocess.run([sys.executable, "-m", "twine", "check", str(wheel), str(sdist)], check=True)
    verifier = Path(__file__).with_name("verify_distribution.py")
    with tempfile.TemporaryDirectory(prefix="package-qualification-") as folder:
        root = Path(folder)
        shutil.copyfile(verifier, root / "verify_distribution.py")
        for name, artifact in (("wheel", wheel), ("sdist", sdist)):
            environment = root / name
            subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True)
            python = environment / "bin/python"
            subprocess.run([str(python), "-m", "pip", "install", str(artifact)], check=True)
            subprocess.run([str(python), "verify_distribution.py", __version__], cwd=root, check=True)
        if mssql:
            environment = root / "mssql"
            subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True)
            python = environment / "bin/python"
            subprocess.run([str(python), "-m", "pip", "install", f"{wheel}[mssql]"], check=True)
            subprocess.run([str(python), "-c", "import pymssql; print(pymssql.__version__)"], check=True)
    (dist / "SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in hashes.items()))
    print(json.dumps({"version": __version__, "sha256": hashes, "mssql_install_tested": mssql}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--mssql", action="store_true")
    args = parser.parse_args()
    qualify(args.dist, build=not args.no_build, mssql=args.mssql)
