"""Resolve Linux CPython 3.15 image dependency constraints in an isolated venv.

Run on the release-target platform after installing project build dependencies.
Review generated versions before committing; --check validates against the committed pins.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib

PACKAGE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.!+_-]*$")
SKIP = {"pip", "setuptools", "wheel", "vedrfolnir"}


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def format_lock(installed: list[dict], required: set[str]) -> str:
    versions = {}
    for row in installed:
        name, version = row.get("name"), row.get("version")
        if not isinstance(name, str) or not PACKAGE.fullmatch(name):
            raise ValueError("Unexpected package name in environment")
        if not isinstance(version, str) or not VERSION.fullmatch(version):
            raise ValueError("Unexpected package version in environment")
        normalized = normalize(name)
        if normalized in versions:
            raise ValueError("Duplicate installed distribution: " + normalized)
        versions[normalized] = version
    if not required.issubset(versions):
        raise ValueError("Missing runtime dependencies: " + ", ".join(sorted(required - versions.keys())))
    if not any(x not in SKIP for x in versions):
        raise ValueError("Empty dependency resolution")
    return (
        "# Linux/amd64 CPython 3.15 constraints, regenerate from a fresh wheel install.\n"
        "# python scripts/generate_container_constraints.py\n"
        + "".join(f"{name}=={versions[name]}\n" for name in sorted(versions) if name not in SKIP)
    )


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output", type=Path, default=Path("requirements/container-constraints.txt"))
    cli.add_argument("--check", action="store_true", help="Check the pinned wheel resolution")
    cli.add_argument("--verify-inventory", type=Path, help="Compare pip list JSON with the lock")
    args = cli.parse_args()
    if args.check and args.verify_inventory:
        cli.error("--check and --verify-inventory cannot be combined")
    if (args.check or args.verify_inventory) and not args.output.is_file():
        raise SystemExit("Container constraints missing; regenerate and review")
    assert sys.version_info[:2] == (3, 15), "Use the target Python 3.15 runtime"
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    required = {normalize(re.split(r"[<>=!~; \[]", item, 1)[0])
                for item in project["project"]["dependencies"]}
    if args.verify_inventory:
        installed = json.loads(args.verify_inventory.read_text(encoding="utf-8"))
        if format_lock(installed, required) != args.output.read_text(encoding="utf-8"):
            raise SystemExit("Container installed package inventory differs from the lock")
        print("Container installed package inventory matches the lock")
        return
    with tempfile.TemporaryDirectory(prefix="container-constraints-") as folder:
        root = Path(folder)
        dist = root / "dist"
        subprocess.run([sys.executable, "-m", "build", "--wheel", "--outdir", str(dist)], check=True)
        wheels = list(dist.glob("vedrfolnir-*.whl"))
        if len(wheels) != 1:
            raise ValueError("Expected one Vedrfolnir wheel")
        env = root / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(env)], check=True)
        python = env / "bin" / "python"
        install = [str(python), "-m", "pip", "install"]
        if args.check:
            # Avoid upstream releases changing the resolution during validation.
            install += ["--constraint", str(args.output.resolve())]
        subprocess.run([*install, str(wheels[0])], check=True)
        subprocess.run([str(python), "-m", "pip", "check"], check=True)
        installed = json.loads(subprocess.check_output(
            [str(python), "-m", "pip", "list", "--format=json"], text=True))
    desired = format_lock(installed, required)
    if args.check:
        if args.output.read_text(encoding="utf-8") != desired:
            raise SystemExit("Container constraints are absent or stale; regenerate and review")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(desired, encoding="utf-8")
        print("Wrote", args.output)


if __name__ == "__main__":
    main()
