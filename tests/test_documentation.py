"""Keep documentation concise, navigable and synchronized with entry points."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PAGES = [
    ROOT / "README.md",
    ROOT / "simulator" / "README.md",
    *(ROOT / "docs").glob("*.md"),
]

LINK = re.compile(r"\]\(([^)\n]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)


def slug(value):
    return re.sub(r"\s+", "-", re.sub(
        r"[^\w\s-]", "", value.replace("`", "").replace("*", "")
        .replace("_", "").lower()).strip())


def test_local_markdown_links_and_heading_targets_exist():
    for page in PAGES:
        content = page.read_text(encoding="utf-8")
        for match in LINK.finditer(content):
            uri = match.group(1).split(' "', 1)[0]
            if uri.startswith(("http:", "https:", "mailto:", "#", "data:")):
                continue
            target_path, _, fragment = uri.partition("#")
            target = (page.parent / target_path).resolve()
            assert target.is_file(), f"{page.relative_to(ROOT)}: {uri} is missing"
            if fragment and target.suffix.lower() == ".md":
                headings = {
                    slug(m.group(1))
                    for m in HEADING.finditer(target.read_text(encoding="utf-8"))
                }
                assert fragment in headings, (
                    f"{page.relative_to(ROOT)}: missing heading {uri}"
                )


def test_readme_is_a_short_entry_point_not_duplicate_manual():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert len(readme) < 5000
    assert all(f"(docs/{p}.md)" in readme for p in (
        "quickstart", "contracts", "architecture", "observations",
        "validation", "operations",
    ))


def test_pool_refresh_override_is_documented_and_forwarded():
    config = (ROOT / "src/vedrfolnir/config.py").read_text(encoding="utf-8")
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    for source in (config, compose, env):
        assert "ETA_POOL_CAPACITY_REFRESH_SECONDS" in source
