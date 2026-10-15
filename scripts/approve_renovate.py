"""Conservative Renovate dev-tool patch policy for trusted bot approval."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import sys
import tomllib
from urllib.request import Request, urlopen

REPO = "HRAshton/vedrfolnir"
APPROVED = {"pytest", "ruff", "build", "twine", "httpx"}
SPEC = re.compile(r"^([a-z][a-z0-9-]*)>=(\\d+(?:\\.\\d+){0,2}),<(\\d+(?:\\.\\d+){0,2})$")


def parse_spec(text: str):
    match = SPEC.fullmatch(text)
    if not match:
        raise ValueError("Unsupported dependency specifier: " + text)
    name, lower, upper = match.groups()
    values = tuple(int(value) for value in lower.split("."))
    return name, values + (0,) * (3 - len(values)), upper


def safe_pyproject_change(before: str, after: str) -> bool:
    old, new = tomllib.loads(before), tomllib.loads(after)
    old_dev = old.get("project", {}).get("optional-dependencies", {}).get("dev")
    new_dev = new.get("project", {}).get("optional-dependencies", {}).get("dev")
    if not isinstance(old_dev, list) or not isinstance(new_dev, list):
        return False
    if len(old_dev) != len(new_dev) or len(old_dev) == 0:
        return False
    old["project"]["optional-dependencies"]["dev"] = []
    new["project"]["optional-dependencies"]["dev"] = []
    if old != new:
        return False
    changes = 0
    for previous, proposed in zip(old_dev, new_dev):
        if previous == proposed:
            continue
        try:
            old_name, old_version, old_cap = parse_spec(previous)
            new_name, new_version, new_cap = parse_spec(proposed)
        except ValueError:
            return False
        if (old_name != new_name or old_name not in APPROVED or old_cap != new_cap
                or old_version[:2] != new_version[:2]
                or new_version[2] <= old_version[2]):
            return False
        changes += 1
    return changes > 0


def api(path: str, *, payload=None):
    token = os.environ["GH_TOKEN"]
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(
        "https://api.github.com/repos/" + REPO + "/" + path,
        data=data,
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"},
        method="POST" if payload is not None else "GET",
    )
    with urlopen(request, timeout=20) as response:
        return json.load(response) if response.status != 204 else None


def get_file(sha: str) -> str:
    response = api("contents/pyproject.toml?ref=" + sha)
    if response.get("encoding") != "base64":
        raise ValueError("Unexpected source encoding")
    return base64.b64decode(response["content"], validate=False).decode("utf-8")


def main(event: dict) -> None:
    run = event["workflow_run"]
    if (os.environ.get("GITHUB_REPOSITORY") != REPO
            or run.get("name") != "CI"
            or run.get("event") != "pull_request"
            or run.get("conclusion") != "success"):
        return
    candidates = run.get("pull_requests") or []
    if len(candidates) != 1:
        return
    number = candidates[0].get("number")
    if not isinstance(number, int):
        return
    pr = api("pulls/" + str(number))
    head = pr["head"]
    if (pr.get("state") != "open"
            or pr.get("user", {}).get("login") != "renovate[bot]"
            or pr.get("base", {}).get("ref") != "master"
            or head.get("repo", {}).get("full_name") != REPO
            or head.get("sha") != run.get("head_sha")):
        return
    files = api("pulls/" + str(number) + "/files?per_page=100")
    if len(files) != 1 or files[0].get("filename") != "pyproject.toml" or files[0].get("status") != "modified":
        return
    if not safe_pyproject_change(get_file(pr["base"]["sha"]), get_file(head["sha"])):
        return
    if api("pulls/" + str(number))["head"]["sha"] != head["sha"]:
        return
    reviews = api("pulls/" + str(number) + "/reviews?per_page=100")
    if any(x.get("user", {}).get("login") == "github-actions[bot]"
           and x.get("state") == "APPROVED"
           and x.get("commit_id") == head["sha"] for x in reviews):
        return
    api("pulls/" + str(number) + "/reviews", payload={"event": "APPROVE"})


if __name__ == "__main__":
    main(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
