"""Read-only policy for automatic patch/RC publication candidates.

This module never creates a Git tag, changes application versions or publishes.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

BASE_VERSION = r"v(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)\.(?P<patch>0|[1-9][0-9]*)"
VERSION = re.compile(r"^" + BASE_VERSION + r"rc(?P<rc>[1-9][0-9]*)$")
STABLE = re.compile(r"^" + BASE_VERSION + r"$")
ALLOW = {"requirements/container-constraints.txt"}


def next_candidate(last_tag: str, existing_tags: set[str]) -> str:
    match = VERSION.fullmatch(last_tag)
    if not match:
        raise ValueError("Only an existing prerelease series can be advanced automatically")
    new_tag = "v{major}.{minor}.{patch}rc{rc}".format(
        **{**match.groupdict(), "rc": int(match["rc"]) + 1})
    if new_tag in existing_tags:
        raise ValueError("Candidate tag already exists; never republish a version")
    return new_tag


def release_key(tag: str) -> tuple[int, int, int, int, int] | None:
    """Sort version tags numerically, with stable releases after their RCs."""
    match = VERSION.fullmatch(tag)
    if match:
        return (int(match["major"]), int(match["minor"]), int(match["patch"]),
                0, int(match["rc"]))
    match = STABLE.fullmatch(tag)
    if match:
        return (int(match["major"]), int(match["minor"]), int(match["patch"]), 1, 0)
    return None


def latest_release_tag(tags: list[str] | set[str]) -> str | None:
    versions = [(key, tag) for tag in tags if (key := release_key(tag)) is not None]
    return max(versions)[1] if versions else None


def assess_changes(paths: list[str]) -> tuple[bool, str]:
    if not paths:
        return False, "No changes since the last version tag"
    unexpected = sorted(set(paths) - ALLOW)
    if unexpected:
        return False, "Human review required for paths: " + ", ".join(unexpected)
    if "requirements/container-constraints.txt" not in paths:
        return False, "No qualified runtime dependency changes"
    return True, "Only the reviewed runtime lock changed; full qualification still required"


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def plan(last_tag: str | None = None) -> dict:
    source_sha = git("rev-parse", "HEAD")
    # Fetch all tags in the read-only workflow; only an ancestor release is a valid baseline.
    existing = set(git("tag", "--list", "v*").splitlines())
    reachable = git("tag", "--merged", "HEAD", "--list", "v*").splitlines()
    baseline = latest_release_tag(reachable)
    newest = latest_release_tag(existing)
    if last_tag is not None and last_tag != baseline:
        raise ValueError("Selected baseline is not the latest release reachable from HEAD")

    changed: list[str] = []
    candidate = None
    if baseline is None:
        eligible, reason = False, "No reachable release tag; manual release baseline required"
    elif newest != baseline:
        eligible, reason = False, "Newest release tag is not reachable from HEAD; review tag history"
    elif not VERSION.fullmatch(baseline):
        eligible, reason = False, "Latest release is final; a new RC series requires human review"
    else:
        changed = git("diff", "--name-only", baseline + "^{commit}", "HEAD").splitlines()
        eligible, reason = assess_changes(changed)
        if eligible and not Path("requirements/container-constraints.txt").is_file():
            eligible, reason = False, "No committed container runtime lock"
        if eligible:
            try:
                candidate = next_candidate(baseline, existing)
            except ValueError as error:
                eligible, reason = False, str(error)
    return {"eligible": eligible, "reason": reason, "last_tag": baseline,
            "candidate_tag": candidate, "source_sha": source_sha, "changed_paths": changed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-tag", help="Optional baseline assertion (must be the latest reachable release)")
    args = parser.parse_args()
    result = plan(args.from_tag)
    print(json.dumps(result, indent=2))
    summary = Path(os.environ.get("GITHUB_STEP_SUMMARY", "/dev/null"))
    with summary.open("a", encoding="utf-8") as dest:
        dest.write("### Candidate release eligibility\n\n")
        dest.write(f"- Eligible: {result['eligible']}\n- Reason: {result['reason']}\n")
        dest.write(f"- Baseline: `{result['last_tag']}`\n")
        dest.write(f"- Candidate: `{result['candidate_tag']}`\n")
        dest.write(f"- Source: `{result['source_sha']}`\n")
        dest.write("- No release tag, package or image was published.\n")


if __name__ == "__main__":
    main()
