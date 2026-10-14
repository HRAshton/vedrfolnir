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
        with pytest.raises(AssertionError):
            validate_manifest(corrupted, version, image, sha)
    manifest["artifacts"]["../escape.whl"] = manifest["artifacts"].pop("package.whl")
    with pytest.raises(AssertionError):
        validate_manifest(manifest, version, image, sha)
