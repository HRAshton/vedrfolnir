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
    assert manifest["version"] == version
    assert manifest["image"] == image
    assert manifest["source_sha"] == sha
    for name, item in manifest["artifacts"].items():
        assert Path(name).name == name and name not in {".", ".."}, name
        assert re.fullmatch(r"[0-9a-f]{64}", item["sha256"]), name


def main(version, image):
    validate_tag("v" + version, version)
    assert sys.version_info[:2] == (3, 15) and sys.version_info.releaselevel == "final", sys.version
    assert re.fullmatch(r"ghcr\.io/hrashton/vedrfolnir@sha256:[0-9a-f]{64}", image)
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
        for name, item in manifest["artifacts"].items():
            asset = root / name
            assert asset.stat().st_size == item["size"]
            assert hashlib.sha256(asset.read_bytes()).hexdigest() == item["sha256"]
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
        assert hashlib.sha256(public_wheel.read_bytes()).hexdigest() == manifest["artifacts"][public_wheel.name]["sha256"]
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
