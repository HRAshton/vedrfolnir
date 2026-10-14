"""Check untrusted PR titles and commit subjects without shell interpolation."""
import json
import os
from pathlib import Path
import re
import subprocess

SUBJECT = re.compile(
    r"^(?:build|chore|ci|docs|feat|fix|perf|refactor|release|revert|security|style|test)"
    r"(?:\([^()\s]+\))?!?: \S[^\r\n]*$"
)
SHA = re.compile(r"^[0-9a-f]{40}$")


def valid_subject(subject):
    return SUBJECT.fullmatch(subject) is not None


def revision_range(event_name, event):
    if event_name == "pull_request":
        pr = event["pull_request"]
        before, after = pr["base"]["sha"], pr["head"]["sha"]
    elif event_name == "push":
        before, after = event["before"], event["after"]
    else:
        raise ValueError(f"Unsupported event: {event_name}")
    if not SHA.fullmatch(before) or not SHA.fullmatch(after) or after == "0" * 40:
        raise ValueError("Expected full commit SHAs")
    return after if before == "0" * 40 else f"{before}..{after}"


def check(event_name, event):
    errors = []
    if event_name == "pull_request" and not valid_subject(event["pull_request"]["title"]):
        errors.append("PR title must be a Conventional Commit subject")
    revisions = revision_range(event_name, event)
    # GitHub-generated merge subjects are allowed; squash subjects must be conventional.
    result = subprocess.run(
        ["git", "log", "--no-merges", "--format=%s", revisions],
        check=True, capture_output=True, text=True,
    )
    for subject in result.stdout.splitlines():
        if not valid_subject(subject):
            errors.append(f"Invalid commit subject: {subject}")
    return errors


def main():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    errors = check(os.environ["GITHUB_EVENT_NAME"], event)
    if errors:
        raise SystemExit("\n".join(errors))
    print("PR title / commit subject policy passed")


if __name__ == "__main__":
    main()
