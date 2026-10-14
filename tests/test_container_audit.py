"""Fail-closed policy regression cases for image vulnerability qualification."""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path

import pytest

from scripts.check_container_audit import evaluate, validate_policy


@pytest.fixture
def candidate():
    policy = {
        "schema_version": 1,
        "distro": {"name": "debian", "version": "13"},
        "exceptions": [{
            "id": "CVE-2026-5435", "severity": "High",
            "packages": {"libc6": "2.41-12+deb13u4"},
            "reviewed_on": "2026-10-14", "expires_on": "2026-10-28",
            "rationale": "Privileged libc TSIG-printing path not exposed through this unprivileged API.",
            "reference": "https://security-tracker.debian.org/tracker/CVE-2026-5435",
        }],
    }
    audit = {
        "distro": {"name": "debian", "version": "13"},
        "matches": [{
            "vulnerability": {
                "id": "CVE-2026-5435", "severity": "High",
                "fix": {"state": "wont-fix", "versions": []},
            },
            "artifact": {"name": "libc6", "version": "2.41-12+deb13u4"},
        }],
    }
    return audit, policy


def review(audit, policy, when=date(2026, 10, 15)):
    return evaluate(audit, policy, today=when)


def test_exact_unfixed_high_is_accepted(candidate):
    outcome = review(*candidate)
    assert outcome["passed"] and outcome["accepted_count"] == 1
    assert not outcome["blocking"]


@pytest.mark.parametrize("change", ["cve", "package", "version", "critical", "fixable",
                                     "fix_available_without_version", "unknown_fix"])
def test_unreviewed_or_fixable_vulnerability_blocks(candidate, change):
    audit, policy = deepcopy(candidate)
    issue = audit["matches"][0]
    if change == "cve":
        issue["vulnerability"]["id"] = "CVE-2026-5436"
    elif change == "package":
        issue["artifact"]["name"] = "libc-bin"
    elif change == "version":
        issue["artifact"]["version"] = "2.41-12+deb13u5"
    elif change == "critical":
        issue["vulnerability"]["severity"] = "Critical"
    elif change == "fixable":
        issue["vulnerability"]["fix"] = {"state": "fixed", "versions": ["2.41-13"]}
    elif change == "fix_available_without_version":
        issue["vulnerability"]["fix"] = {"state": "fixed", "versions": []}
    else:
        issue["vulnerability"]["fix"] = {"state": "unknown", "versions": []}
    assert review(audit, policy)["blocking_count"] == 1


def test_expiration_blocks(candidate):
    assert review(*candidate, when=date(2026, 10, 29))["blocking_count"] == 1


def test_expiration_boundary_is_inclusive(candidate):
    assert review(*candidate, when=date(2026, 10, 28))["passed"]


def test_changed_distro_fails_closed(candidate):
    audit, policy = candidate
    audit["distro"]["version"] = "14"
    with pytest.raises(ValueError, match="Scanner OS"):
        review(audit, policy)


def test_missing_or_incomplete_scan_fails_closed(candidate):
    audit, policy = candidate
    del audit["matches"]
    with pytest.raises(ValueError, match="Missing Grype"):
        review(audit, policy)


def test_new_high_blocks_existing_accepted_finding(candidate):
    audit, policy = candidate
    another = deepcopy(audit["matches"][0])
    another["vulnerability"]["id"] = "CVE-2026-99999"
    audit["matches"].append(another)
    outcome = review(audit, policy)
    assert not outcome["passed"]
    assert outcome["accepted_count"] == 1 and outcome["blocking_count"] == 1


def test_low_severity_is_not_blocked(candidate):
    audit, policy = candidate
    audit["matches"][0]["vulnerability"]["severity"] = "Low"
    assert review(audit, policy)["passed"]


def test_invalid_policy_expiry_fails_closed(candidate):
    audit, policy = candidate
    policy["exceptions"][0]["expires_on"] = "2026-12-31"
    with pytest.raises(ValueError, match="within 30 days"):
        review(audit, policy)


def test_real_exception_list_is_scoped_and_expiring():
    path = Path(__file__).resolve().parents[1] / "security/container-risk-acceptances.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    indexed = validate_policy(data)
    assert len(data["exceptions"]) == 14
    assert len(indexed) == 55
    assert all(entry["expires_on"] == "2026-10-28" for entry in data["exceptions"])
