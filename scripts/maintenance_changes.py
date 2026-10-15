"""Compare tracked OCI inputs to the last successfully promoted GHCR edge image."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

IMAGE_INPUTS = (".dockerignore", "Dockerfile", "MANIFEST.in", "README.md",
                "pyproject.toml", "requirements/container-constraints.txt",
                "src", "queries", "examples")
EDGE = "ghcr.io/hrashton/vedrfolnir:edge"
LABEL = "org.vedrfolnir.image-input-sha256"
SHA = re.compile(r"^[a-f0-9]{64}$")
MISSING = re.compile(r"manifest unknown|manifest_unknown|name unknown|name_unknown|no such manifest", re.I)


def fingerprint_tree(data: bytes) -> str:
    if not data or not data.endswith(b"\0"):
        raise ValueError("Missing or malformed Git image-input tree")
    return hashlib.sha256(b"vedrfolnir-image-inputs-v1\0" + data).hexdigest()


def image_fingerprint() -> str:
    tree = subprocess.check_output(
        ["git", "ls-tree", "--full-tree", "-r", "-z", "HEAD", "--", *IMAGE_INPUTS])
    return fingerprint_tree(tree)


def classify_distribution_paths(paths: list[str]) -> str:
    """Classification is informative, not authorization to release."""
    if any(p in {"pyproject.toml", "README.md", "MANIFEST.in"} or p.startswith("src/")
           for p in paths):
        return "package-and-image"
    if any(p in {".dockerignore", "Dockerfile", "requirements/container-constraints.txt"}
           or p.startswith(("queries/", "examples/")) for p in paths):
        return "image-only"
    return "no-distribution"


def decide_build(prior: str | None, current: str, *, force: bool = False) -> tuple[bool, str]:
    if not SHA.fullmatch(current):
        raise ValueError("Invalid current image-input fingerprint")
    if force:
        return True, "Explicitly requested rebuild"
    if prior is None or not SHA.fullmatch(prior):
        return True, "Missing edge image or valid fingerprint"
    if prior != current:
        return True, "Shipped OCI inputs changed"
    return False, "No change to shipped OCI inputs"


def prior_edge() -> tuple[str | None, str]:
    result = subprocess.run(
        ["docker", "manifest", "inspect", EDGE], capture_output=True, text=True, check=False)
    if result.returncode:
        message = result.stderr + "\n" + result.stdout
        if MISSING.search(message):
            return None, "absent"
        raise RuntimeError("GHCR lookup failed (not a missing image): " + message[:400])
    subprocess.run(["docker", "pull", "--quiet", EDGE], check=True, capture_output=True)
    images = json.loads(subprocess.check_output(["docker", "image", "inspect", EDGE], text=True))
    if len(images) != 1:
        raise RuntimeError("Unexpected inspected image count")
    info = images[0]
    prior = (info.get("Config", {}).get("Labels") or {}).get(LABEL)
    digests = [x for x in info.get("RepoDigests", []) if x.startswith(EDGE[:-5] + "@sha256:")]
    return prior, digests[0] if digests else "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-edge", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    wanted = image_fingerprint()
    if not args.check_edge:
        print(wanted)
        return
    previous, published = prior_edge()
    changed, reason = decide_build(previous, wanted, force=args.force)
    print(json.dumps({"changed": changed, "reason": reason, "fingerprint": wanted,
                      "prior_image": published}, indent=2))
    if os.environ.get("GITHUB_OUTPUT"):
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as out:
            out.write(f"changed={str(changed).lower()}\nimage_inputs={wanted}\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as out:
            out.write(f"### Weekly OCI inputs\n\n- Rebuild: {changed}\n")
            out.write(f"- Reason: {reason}\n- Fingerprint: {wanted}\n")
            out.write(f"- Previous image: {published}\n")


if __name__ == "__main__":
    main()
