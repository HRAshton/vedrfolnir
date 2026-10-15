"""Read-only policy for automatic patch/RC publication candidates.

This module never creates a Git tag, changes application versions or publishes.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

VERSION = re.compile(r"^v(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)\.(?P<patch>0|[1-9][0-9]*)rc(?P<rc>[1-9][0-9]*)$")
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


def plan(last_tag: str) -> dict:
    if not VERSION.fullmatch(last_tag):
        raise ValueError("Use a specific existing prerelease tag")
    git("rev-parse", "--verify", last_tag + "^{commit}")
    existing = set(git("tag", "--list", "v*").splitlines())
    next_tag = next_candidate(last_tag, existing)
    changed = git("diff", "--name-only", last_tag + "^{commit}", "HEAD").splitlines()
    eligible, reason = assess_changes(changed)
    if eligible and not Path("requirements/container-constraints.txt").is_file():
        eligible, reason = False, "No committed container runtime lock"
    return {"eligible": eligible, "reason": reason, "last_tag": last_tag,
            "candidate_tag": next_tag if eligible else None, "source_sha": git("rev-parse", "HEAD"),
            "changed_paths": changed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-tag", required=True)
    args = parser.parse_args()
    result = plan(args.from_tag)
    print(json.dumps(result, indent=2))
    summary = Path(__import__("os").environ.get("GITHUB_STEP_SUMMARY", "/dev/null"))
    with summary.open("a", encoding="utf-8") as dest:
        dest.write("### Candidate release eligibility\n\n")
        dest.write(f"- Eligible: {result['eligible']}\n- Reason: {result['reason']}\n")
        dest.write(f"- Source: `{result['source_sha']}`\n")
        dest.write("- No release tag, package or image was published.\n")


if __name__ == "__main__":
    main()
