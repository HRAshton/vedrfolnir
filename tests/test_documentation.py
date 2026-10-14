"""Validate navigability, supported setup and security guidance, not editorial size."""
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGES = [
    ROOT / "README.md",
    ROOT / "simulator" / "README.md",
    *(ROOT / "docs").rglob("*.md"),
    *(ROOT / "examples").rglob("README.md"),
]

LINK = re.compile(r"\]\(([^)\n]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)


def prose(text):
    """Ignore fenced examples when finding headings and documentation links."""
    lines = []
    fence = None
    for line in text.splitlines():
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence is None:
            lines.append(line)
    return "\n".join(lines)


def heading_ids(text):
    counts = {}
    anchors = set()
    for match in HEADING.finditer(prose(text)):
        value = match.group(1).strip().rstrip("#").strip()
        value = value.replace("`", "").replace("*", "").lower()
        value = re.sub(r"[^\w\s-]", "", value)
        slug = re.sub(r"\s", "-", value)
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        anchors.add(f"{slug}-{count}" if count else slug)
    return anchors


def local_link_errors(page):
    errors = []
    for match in LINK.finditer(prose(page.read_text(encoding="utf-8"))):
        uri = match.group(1).split(' "', 1)[0].strip().strip("<>")
        parsed = urlsplit(uri)
        if parsed.scheme or parsed.netloc:
            continue
        target = (page.parent / unquote(parsed.path)).resolve() if parsed.path else page
        if not target.is_file():
            errors.append(f"{page}: {uri} is missing")
        elif parsed.fragment and target.suffix.lower() == ".md":
            if unquote(parsed.fragment) not in heading_ids(target.read_text(encoding="utf-8")):
                errors.append(f"{page}: missing heading {uri}")
    return errors


def test_local_markdown_links_and_heading_targets_exist():
    errors = [error for page in PAGES for error in local_link_errors(page)]
    assert not errors, "\n".join(errors)


def test_readme_documents_supported_setup_and_warnings():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "(docs/README.md)" in readme
    assert "(docs/quickstart.md)" in readme
    assert "(docs/operations.md)" in readme
    assert "docker compose up -d --build" in readme
    assert "python -m pip install ." in readme
    assert "vedrfolnir doctor" in readme and "vedrfolnir serve" in readme
    assert "CPython 3.15" in readme and "Linux/amd64" in readme
    assert "unauthenticated" in readme and "127.0.0.1" in readme
    assert "authenticating TLS" in readme and "one worker per database" in readme
    assert "production ETA accuracy" in readme and "not proven" in readme


@pytest.mark.parametrize("link", ["missing.md", "guide.md#missing", "#missing"])
def test_link_checker_rejects_missing_files_and_fragments(tmp_path, link):
    (tmp_path / "guide.md").write_text("# Existing\n", encoding="utf-8")
    page = tmp_path / "README.md"
    page.write_text(f"# Existing\n[broken]({link})\n", encoding="utf-8")
    assert local_link_errors(page)


def test_link_checker_accepts_local_encoded_and_duplicate_fragments(tmp_path):
    page = tmp_path / "README.md"
    page.write_text(
        "# Repeat\n# Repeat\n[local](#repeat-1)\n[encoded](guide%20name.md#source_mode)\n"
        "```text\n# Not a heading\n[example](missing.md)\n```\n",
        encoding="utf-8",
    )
    (tmp_path / "guide name.md").write_text("# `SOURCE_MODE`\n", encoding="utf-8")
    assert not local_link_errors(page)
    assert "not-a-heading" not in heading_ids(page.read_text())


def test_pool_refresh_override_is_documented_and_forwarded():
    config = (ROOT / "src/vedrfolnir/config.py").read_text(encoding="utf-8")
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    for source in (config, compose, env):
        assert "ETA_POOL_CAPACITY_REFRESH_SECONDS" in source
