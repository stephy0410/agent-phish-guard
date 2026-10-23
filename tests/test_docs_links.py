"""Relative links in the repo's Markdown point at files, and #anchors at headings, that exist."""

import re
import subprocess
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")
FENCE = re.compile(r"^```.*?^```", flags=re.S | re.M)
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*$", flags=re.M)


def markdown_files() -> list[Path]:
    """Tracked and new (not ignored) Markdown files, so a link is checked before it's committed."""
    try:
        listed = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("needs git to list the repo's files")
    return [ROOT / f for f in listed.splitlines() if f.endswith(".md") and (ROOT / f).exists()]


def anchors(path: Path) -> set[str]:
    """The ids GitHub gives the headings: lowercase, punctuation dropped, spaces to hyphens."""
    seen: Counter[str] = Counter()
    ids = set()
    for heading in HEADING.findall(FENCE.sub("", path.read_text(encoding="utf-8"))):
        slug = re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")
        ids.add(f"{slug}-{seen[slug]}" if seen[slug] else slug)
        seen[slug] += 1
    return ids


def test_relative_links_and_anchors_resolve():
    broken = []
    for path in markdown_files():
        for target in LINK.findall(FENCE.sub("", path.read_text(encoding="utf-8"))):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            file, _, anchor = target.partition("#")
            linked = (path.parent / file).resolve() if file else path
            where = f"{path.relative_to(ROOT)} -> {target}"
            if not linked.exists():
                broken.append(f"{where}: no such file")
            elif anchor and linked.suffix == ".md" and anchor not in anchors(linked):
                broken.append(f"{where}: no such heading")
    assert not broken, "\n".join(broken)
