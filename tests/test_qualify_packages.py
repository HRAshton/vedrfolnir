"""Requalification must verify existing artifact checksums before installation."""
import io
import tarfile
import zipfile

import pytest

from scripts import qualify_packages
from vedrfolnir import __version__


@pytest.fixture
def distributions(tmp_path, monkeypatch):
    metadata = (f"Name: vedrfolnir\nVersion: {__version__}\n"
                "Requires-Python: <3.16,>=3.15\n").encode()
    wheel = tmp_path / f"vedrfolnir-{__version__}-py3-none-any.whl"
    sdist = tmp_path / f"vedrfolnir-{__version__}.tar.gz"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"vedrfolnir-{__version__}.dist-info/METADATA", metadata)
        archive.writestr("vedrfolnir/__init__.py", "")
        archive.writestr("vedrfolnir/static/dashboard.html", "dashboard")
    with tarfile.open(sdist, "w:gz") as archive:
        for name, content in (("PKG-INFO", metadata), ("pyproject.toml", b""),
                              ("src/vedrfolnir/static/dashboard.html", b"dashboard")):
            member = tarfile.TarInfo(f"vedrfolnir-{__version__}/{name}")
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    monkeypatch.setattr(qualify_packages.subprocess, "run", lambda *args, **kwargs: None)
    return tmp_path, wheel, sdist


def test_no_build_can_requalify_its_own_output(distributions):
    dist, wheel, sdist = distributions
    originals = {p: p.read_bytes() for p in (wheel, sdist)}
    qualify_packages.qualify(dist, build=False)
    manifest = (dist / "SHA256SUMS").read_bytes()
    qualify_packages.qualify(dist, build=False)
    assert (dist / "SHA256SUMS").read_bytes() == manifest
    assert all(p.read_bytes() == content for p, content in originals.items())


@pytest.mark.parametrize("damage", ["hash", "missing", "duplicate", "extra", "artifact"])
def test_no_build_rejects_stale_or_malformed_manifest(distributions, monkeypatch, damage):
    dist, wheel, _ = distributions
    qualify_packages.qualify(dist, build=False)
    manifest = dist / "SHA256SUMS"
    original = manifest.read_text()
    if damage == "artifact":
        with zipfile.ZipFile(wheel, "a") as archive:
            archive.writestr("vedrfolnir/new.txt", "changed artifact")
    else:
        lines = original.splitlines(keepends=True)
        damaged = {"hash": "0" * 64 + lines[0][64:] + lines[1],
                   "missing": lines[0], "duplicate": original + lines[0],
                   "extra": original + "0" * 64 + "  unrelated.whl\n"}[damage]
        manifest.write_text(damaged)
    before = manifest.read_bytes()
    def unexpected_install(*args, **kwargs):
        pytest.fail("Invalid checksums must fail before Twine or installation")
    monkeypatch.setattr(qualify_packages.subprocess, "run", unexpected_install)
    with pytest.raises(AssertionError, match="SHA256SUMS does not match"):
        qualify_packages.qualify(dist, build=False)
    assert manifest.read_bytes() == before


def test_no_build_still_rejects_unrelated_files(distributions):
    dist, _, _ = distributions
    qualify_packages.qualify(dist, build=False)
    (dist / "unrelated.txt").write_text("unexpected")
    with pytest.raises(AssertionError, match="Unexpected distribution files"):
        qualify_packages.qualify(dist, build=False)


def test_build_still_requires_empty_directory(distributions):
    dist, _, _ = distributions
    qualify_packages.qualify(dist, build=False)
    with pytest.raises(AssertionError, match="Use an empty distribution directory"):
        qualify_packages.qualify(dist, build=True)
