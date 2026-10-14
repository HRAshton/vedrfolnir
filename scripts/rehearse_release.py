"""Read-only qualification of what users download from PyPI, GitHub and GHCR."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from scripts.validate_release import validate_tag

REPOSITORY = "HRAshton/vedrfolnir"


def validate_manifest(manifest, version, image, sha):
    """Reject untrusted release asset names before any filesystem access.

    Explicit exceptions remain active even if Python is run with -O.
    """
    if not isinstance(manifest, dict):
        raise ValueError("Invalid release manifest")
    for field, expected in (("version", version), ("image", image), ("source_sha", sha)):
        if manifest.get(field) != expected:
            raise ValueError("Release manifest identity mismatch: " + field)
    assets = manifest.get("artifacts")
    if not isinstance(assets, dict) or not assets:
        raise ValueError("Release manifest must list artifacts")
    for name, item in assets.items():
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", name)
                or name in {".", ".."}):
            raise ValueError("Unsafe release asset name: " + repr(name))
        if (not isinstance(item, dict)
                or not isinstance(item.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
                or type(item.get("size")) is not int or item["size"] < 0):
            raise ValueError("Invalid release asset metadata: " + name)


def main(version, image):
    validate_tag("v" + version, version)
    if sys.version_info[:2] != (3, 15) or sys.version_info.releaselevel != "final":
        raise ValueError("Consumer rehearsal requires stable CPython 3.15")
    if not re.fullmatch(r"ghcr\.io/hrashton/vedrfolnir@sha256:[0-9a-f]{64}", image):
        raise ValueError("Expected a canonical immutable GHCR image digest")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    with tempfile.TemporaryDirectory(prefix="consumer-rehearsal-") as folder:
        root = Path(folder)
        subprocess.run(["gh", "release", "download", "v" + version, "--repo", REPOSITORY,
                        "--dir", str(root), "--pattern", "*.whl", "--pattern", "*.tar.gz",
                        "--pattern", "SHA256SUMS", "--pattern", "container.spdx.json",
                        "--pattern", "release-manifest.json", "--pattern", "qualification.json"], check=True)
        subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=root, check=True)
        manifest = json.loads((root / "release-manifest.json").read_text())
        validate_manifest(manifest, version, image, sha)
        # Enumerate downloaded files rather than constructing paths from
        # attacker-controlled manifest keys.
        downloaded = {asset.name: asset for asset in root.iterdir()
                      if asset.name not in {"SHA256SUMS", "release-manifest.json"}}
        if downloaded.keys() != manifest["artifacts"].keys():
            raise ValueError("Release manifest and downloaded assets disagree")
        for name, asset in downloaded.items():
            item = manifest["artifacts"][name]
            if (asset.is_symlink() or not asset.is_file()
                    or not asset.resolve().is_relative_to(root.resolve())):
                raise ValueError("Unsafe or missing release asset: " + name)
            if (asset.stat().st_size != item["size"]
                    or hashlib.sha256(asset.read_bytes()).hexdigest() != item["sha256"]):
                raise ValueError("Release asset integrity mismatch: " + name)
            subprocess.run(["gh", "attestation", "verify", str(asset), "--repo", REPOSITORY,
                            "--signer-workflow", REPOSITORY + "/.github/workflows/release.yml"], check=True)
        subprocess.run(["gh", "attestation", "verify", "oci://" + image, "--repo", REPOSITORY,
                        "--signer-workflow", REPOSITORY + "/.github/workflows/release.yml"], check=True)
        environment = root / "consumer"
        subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True)
        python = environment / "bin/python"
        # Download from the one normal PyPI index and compare the actual public wheel bytes.
        subprocess.run([str(python), "-m", "pip", "download", "--no-deps", "--only-binary=:all:",
                        f"vedrfolnir=={version}", "--dest", str(root / "pypi")], check=True)
        public_wheel = next((root / "pypi").glob("*.whl"))
        if (public_wheel.name not in manifest["artifacts"] or
                hashlib.sha256(public_wheel.read_bytes()).hexdigest()
                != manifest["artifacts"][public_wheel.name]["sha256"]):
            raise ValueError("Public PyPI wheel differs from qualified release asset")
        subprocess.run([str(python), "-m", "pip", "install", f"vedrfolnir=={version}"], check=True)
        shutil.copyfile(Path(__file__).with_name("verify_distribution.py"), root / "verify_distribution.py")
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        subprocess.run([str(python), "verify_distribution.py", version], cwd=root, env=env, check=True)
        subprocess.run([sys.executable, "-m", "scripts.release_smoke", "--pull", "--image", image], check=True)
        print(json.dumps({"consumer_rehearsal": "passed", "version": version, "image": image, "source_sha": sha}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    main(args.version, args.image)
