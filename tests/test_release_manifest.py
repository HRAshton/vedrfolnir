"""Consumer verification binds all publication surfaces to one approved identity."""
from copy import deepcopy
import pytest
from scripts.rehearse_release import validate_manifest


def test_manifest_rejects_cross_release_or_unsafe_identity():
    version = "0.3.0rc1"
    image = "ghcr.io/hrashton/vedrfolnir@sha256:" + "a" * 64
    sha = "b" * 40
    manifest = {"version": version, "image": image, "source_sha": sha,
                "artifacts": {"package.whl": {"sha256": "c" * 64, "size": 10}}}
    validate_manifest(manifest, version, image, sha)
    for key, wrong in [("version", "0.3.0"), ("image", image + "a"), ("source_sha", "c" * 40)]:
        corrupted = deepcopy(manifest)
        corrupted[key] = wrong
        with pytest.raises(ValueError):
            validate_manifest(corrupted, version, image, sha)
    manifest["artifacts"]["../escape.whl"] = manifest["artifacts"].pop("package.whl")
    with pytest.raises(ValueError):
        validate_manifest(manifest, version, image, sha)


@pytest.mark.parametrize("bad_name", ["../outside.whl", "/tmp/escape.whl", r"..\\escape.whl",
                                      "subdir/file.whl", ".", "..", "", "x\n.whl"])
def test_manifest_blocks_untrusted_paths(bad_name):
    manifest = {"version": "0.3.0rc1", "image": "ghcr.io/hrashton/vedrfolnir@sha256:" + "a" * 64,
                "source_sha": "b" * 40,
                "artifacts": {bad_name: {"sha256": "c" * 64, "size": 12}}}
    with pytest.raises(ValueError, match="Unsafe release asset"):
        validate_manifest(manifest, "0.3.0rc1", manifest["image"], "b" * 40)


@pytest.mark.parametrize("metadata", [
    {"sha256": "c" * 64}, {"sha256": "c" * 64, "size": -1},
    {"sha256": "c" * 64, "size": True}, {"sha256": "x", "size": 2},
    {"sha256": "c" * 64, "size": "2"}, None,
])
def test_manifest_rejects_invalid_asset_metadata(metadata):
    manifest = {"version": "0.3.0rc1", "image": "ghcr.io/hrashton/vedrfolnir@sha256:" + "a" * 64,
                "source_sha": "b" * 40, "artifacts": {"package.whl": metadata}}
    with pytest.raises(ValueError, match="Invalid release asset metadata"):
        validate_manifest(manifest, "0.3.0rc1", manifest["image"], "b" * 40)


def test_rehearsal_requires_ghcr_authentication_before_verification():
    from pathlib import Path

    workflow = (Path(__file__).resolve().parents[1] /
                ".github/workflows/release.yml").read_text(encoding="utf-8")
    consumer = workflow.split("  consumer-rehearsal:\n", 1)[1]
    login = consumer.index("uses: docker/login-action@")
    script = consumer.index("python -m scripts.rehearse_release")
    assert login < script
    assert "registry: ghcr.io" in consumer[login:script]
    assert "username: ${{ github.actor }}" in consumer[login:script]
    assert "password: ${{ github.token }}" in consumer[login:script]
    assert "packages: read" in consumer
    assert "packages: write" not in consumer


def test_script_never_constructs_paths_from_untrusted_manifest_names():
    from pathlib import Path

    script = (Path(__file__).resolve().parents[1] /
              "scripts/rehearse_release.py").read_text(encoding="utf-8")
    assert "asset = root / name" not in script
    assert "downloaded = {asset.name: asset for asset in root.iterdir()" in script
    assert 'downloaded.keys() != manifest["artifacts"].keys()' in script
