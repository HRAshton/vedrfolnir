"""Preview publication must never move the edge tag before attestation."""
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/weekly-maintenance.yml"


def test_preview_promotion_requires_signed_candidate():
    source = WORKFLOW.read_text(encoding="utf-8")
    publish = source.split("\n  publish:\n", 1)[1].split("\n  attest:\n", 1)[0]
    attest = source.split("\n  attest:\n", 1)[1].split("\n  promote:\n", 1)[0]
    promote = source.split("\n  promote:\n", 1)[1]

    assert "edge-candidate-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT" in publish
    assert "docker push \"$image\"" in publish
    assert "vedrfolnir:edge" not in publish.split("run: |", 1)[1].replace("edge-candidate", "")
    assert "needs: publish" in attest
    assert "push-to-registry: true" in attest
    assert "needs: [publish, attest]" in promote
    assert "docker push ghcr.io/hrashton/vedrfolnir:edge" in promote
    assert "pushed\" == \"$DIGEST" in promote
    assert "v0." not in promote and ":latest" not in promote
    assert "actions/checkout@" not in promote


def test_change_gate_skips_publication_but_preserves_weekly_scanning():
    source = WORKFLOW.read_text(encoding="utf-8")
    changes = source.split("\n  changes:\n", 1)[1].split("\n  qualify:\n", 1)[0]
    qualify = source.split("\n  qualify:\n", 1)[1].split("\n  scan-existing:\n", 1)[0]
    scan = source.split("\n  scan-existing:\n", 1)[1].split("\n  publish:\n", 1)[0]
    assert "python3 scripts/maintenance_changes.py" in changes
    assert "packages: read" in changes and "packages: write" not in changes
    assert "needs: changes" in qualify
    assert "needs.changes.outputs.changed == 'true'" in qualify
    assert "org.vedrfolnir.image-input-sha256" in qualify
    assert "needs.changes.outputs.changed == 'false'" in scan
    assert "anchore/scan-action@" in scan
    assert "check_container_audit.py" in scan
    assert "inputs.force" in changes
