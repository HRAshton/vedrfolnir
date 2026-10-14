"""Fail closed unless tag, approved source, repository rules and environment agree."""
import argparse
import json
import os
import re
import subprocess
from urllib.request import Request, urlopen

REPOSITORY = "HRAshton/vedrfolnir"
TAG = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(rc[1-9][0-9]*)?$")


def validate_tag(tag, version):
    if not TAG.fullmatch(tag) or tag[1:] != version:
        raise ValueError("Tag must be vX.Y.Z or vX.Y.ZrcN and equal the package version")


def api(path):
    request = Request("https://api.github.com/repos/" + REPOSITORY + path,
                      headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"],
                               "Accept": "application/vnd.github+json"})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def validate_protections(branch_rules, tag_rulesets, environment):
    required = {"pull_request", "required_status_checks", "deletion", "non_fast_forward"}
    if not required <= {rule["type"] for rule in branch_rules}:
        raise ValueError("Active master PR/check/deletion/force-push rules are required")
    checks = {check["context"] for rule in branch_rules
              if rule["type"] == "required_status_checks"
              for check in rule.get("parameters", {}).get("required_status_checks", [])}
    if not {"Python and package validation", "Shell and workflow validation",
            "Dependency review", "Python vulnerability audit", "Container qualification"} <= checks:
        raise ValueError("The established validation checks must be required on master")
    immutable = False
    for ruleset in tag_rulesets:
        conditions = ruleset.get("conditions", {}).get("ref_name", {})
        applies = (ruleset.get("target") == "tag" and ruleset.get("enforcement") == "active"
                   and "refs/tags/v*" in conditions.get("include", [])
                   and not conditions.get("exclude")
                   and {"update", "deletion"} <= {rule["type"] for rule in ruleset.get("rules", [])})
        if not applies:
            continue
        # GitHub redacts bypass_actors without write access to the ruleset.
        # Absence (or null) does not prove that bypasses are disabled.
        if "bypass_actors" not in ruleset or not isinstance(ruleset["bypass_actors"], list):
            raise ValueError("Cannot verify tag ruleset bypass_actors; GitHub may redact it "
                             "without ruleset write access. Refusing release")
        if ruleset["bypass_actors"] == []:
            immutable = True
    if not immutable:
        raise ValueError("Active immutable v* tag rules without bypasses are required")
    if not any(rule.get("type") == "required_reviewers" and rule.get("reviewers")
               for rule in environment.get("protection_rules", [])):
        raise ValueError("The release environment must have required reviewers")


def resolve_event_commit(ref):
    if not re.fullmatch(r"[0-9a-f]{40}", ref):
        raise ValueError("Expected a full event SHA")
    # Annotated tag events can identify the tag object rather than its commit.
    return subprocess.check_output(["git", "rev-parse", f"{ref}^{{commit}}"], text=True).strip()


def main():
    from vedrfolnir import __version__

    if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
        raise ValueError("Only the canonical repository can release")
    ref = os.environ.get("GITHUB_REF", "")
    if not ref.startswith("refs/tags/"):
        raise ValueError("Releases require a tag event")
    tag = ref.removeprefix("refs/tags/")
    validate_tag(tag, __version__)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if head != resolve_event_commit(os.environ["GITHUB_SHA"]):
        raise ValueError("Checkout does not match the event SHA")
    subprocess.run(["git", "merge-base", "--is-ancestor", head, "origin/master"], check=True)
    tag_sha = subprocess.check_output(["git", "rev-parse", f"{tag}^{{commit}}"], text=True).strip()
    if tag_sha != head:
        raise ValueError("Release tag moved")
    listing = api("/rulesets?per_page=100")
    if len(listing) >= 100:
        raise ValueError("Ruleset listing requires pagination; review configuration")
    rulesets = [api(f"/rulesets/{rule['id']}") for rule in listing if rule.get("target") == "tag"]
    validate_protections(api("/rules/branches/master"), rulesets, api("/environments/release"))
    print(json.dumps({"tag": tag, "version": __version__, "source_sha": head}))
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"version={__version__}\ntag={tag}\nsha={head}\n")


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    main()
