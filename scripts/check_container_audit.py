"""Check a complete Grype image report against narrowly reviewed and expiring risks."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
import sys

SEVERITIES = {"negligible": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
NO_FIX = {"wont-fix", "not-fixed"}
CVE = re.compile(r"^CVE-\d{4}-\d{4,}$")


def validate_policy(policy):
    if policy.get("schema_version") != 1:
        raise ValueError("Unsupported risk acceptance schema")
    distro = policy.get("distro")
    if not isinstance(distro, dict) or (distro.get("name"), distro.get("version")) != ("debian", "13"):
        raise ValueError("Accepted risks must be scoped to Debian 13")
    entries = policy.get("exceptions")
    if not isinstance(entries, list):
        raise ValueError("Risk acceptances must be an array")
    indexed = {}
    for entry in entries:
        if not isinstance(entry, dict) or not CVE.fullmatch(str(entry.get("id", ""))):
            raise ValueError("Missing or invalid CVE ID")
        if entry.get("severity") != "High":
            raise ValueError("Exceptions may accept only reviewed High findings")
        rationale = entry.get("rationale")
        if not isinstance(rationale, str) or len(rationale.strip()) < 40:
            raise ValueError("Risk acceptance needs a specific rationale")
        if entry.get("reference") != "https://security-tracker.debian.org/tracker/" + entry["id"]:
            raise ValueError("Risk acceptance needs a Debian advisory link")
        reviewed = date.fromisoformat(entry["reviewed_on"])
        expiry = date.fromisoformat(entry["expires_on"])
        if not reviewed < expiry or (expiry - reviewed).days > 30:
            raise ValueError("Risk acceptance must expire within 30 days")
        packages = entry.get("packages")
        if not isinstance(packages, dict) or not packages:
            raise ValueError("Risk acceptance needs exact package versions")
        for name, version in packages.items():
            if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
                raise ValueError("Invalid scoped package/version")
            key = (entry["id"], name, version)
            if key in indexed:
                raise ValueError("Duplicate risk entry: " + repr(key))
            indexed[key] = entry
    return indexed


def evaluate(audit, policy, today=None):
    if today is None:
        today = datetime.now(timezone.utc).date()
    accepted_keys = validate_policy(policy)
    distro = audit.get("distro")
    expected_distro = policy["distro"]
    # Grype reports Debian point releases such as 13.7; accept only the
    # reviewed major release and numeric point versions, never Debian 14.
    if (not isinstance(distro, dict)
            or distro.get("name") != expected_distro["name"]
            or re.fullmatch(
                rf"{re.escape(expected_distro['version'])}(?:\\.\\d+)*",
                str(distro.get("version")),
            ) is None):
        raise ValueError("Scanner OS does not match the reviewed Debian 13 target")
    matches = audit.get("matches")
    if not isinstance(matches, list):
        raise ValueError("Missing Grype matches; scanner output is not complete")
    accepted, blocking = [], []
    for match in matches:
        if not isinstance(match, dict):
            raise ValueError("Invalid Grype vulnerability match")
        vuln, artifact = match.get("vulnerability"), match.get("artifact")
        if not isinstance(vuln, dict) or not isinstance(artifact, dict):
            raise ValueError("Incomplete Grype vulnerability/artifact fields")
        severity = vuln.get("severity", "").lower()
        if severity not in SEVERITIES:
            raise ValueError("Unknown scanner severity: " + repr(severity))
        if SEVERITIES[severity] < SEVERITIES["high"]:
            continue
        issue = {
            "id": vuln.get("id"), "severity": vuln.get("severity"),
            "package": artifact.get("name"), "version": artifact.get("version"),
            "fix": vuln.get("fix"),
        }
        entry = accepted_keys.get((issue["id"], issue["package"], issue["version"]))
        fix = vuln.get("fix")
        no_fix = (isinstance(fix, dict)
                  and fix.get("state") in NO_FIX
                  and isinstance(fix.get("versions"), list)
                  and not fix["versions"])
        if (severity == "high" and entry and no_fix
                and today <= date.fromisoformat(entry["expires_on"])):
            accepted.append({**issue, "expires_on": entry["expires_on"],
                             "rationale": entry["rationale"]})
        else:
            blocking.append(issue)
    return {
        "checked_on": today.isoformat(), "distro": distro,
        "accepted": accepted, "blocking": blocking,
        "accepted_count": len(accepted), "blocking_count": len(blocking),
        "passed": not blocking,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--decision", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = json.loads(args.audit.read_text(encoding="utf-8"))
        policy = json.loads(args.policy.read_text(encoding="utf-8"))
        decision = evaluate(report, policy)
    except (OSError, ValueError, TypeError, KeyError) as error:
        decision = {"passed": False, "error": str(error)}
    args.decision.write_text(json.dumps(decision, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in decision.items()
                      if k not in {"accepted", "blocking"}}, indent=2))
    for finding in decision.get("blocking", []):
        print("BLOCKED " + json.dumps(finding), file=sys.stderr)
    if not decision["passed"]:
        raise SystemExit("Container vulnerability risk gate failed")


if __name__ == "__main__":
    main()
