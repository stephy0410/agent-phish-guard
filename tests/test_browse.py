import json
import socket
from pathlib import Path

import pytest

from tools.browse import (
    BASE_URL,
    ROOT_ENV_VAR,
    BrowseError,
    browse,
    browse_page,
    fetch_chunks,
    make_browse_tool,
    normalize_url,
)
from tools.chunker import normalize_text

REPO = Path(__file__).resolve().parents[1]
PAGES = REPO / "eval" / "pages"
MANIFEST = json.loads((PAGES / "manifest.json").read_text(encoding="utf-8"))

# Where each taxonomy class lands (docs/contract.md, "Typical Chunk.visibility"), as built in
# Hannah's attack pages.
EXPECTED_VISIBILITY = {
    "css_hidden": "hidden",
    "html_comment": "metadata",
    "alt_aria": "metadata",
    "meta_tag": "metadata",
    "zero_width": "visible",
    "homoglyph": "visible",
    "ugc_injection": "visible",
    "fake_system": "visible",
    "fragmentation": "visible",
    "encoded": "visible",
}


@pytest.fixture(autouse=True)
def _pages_root(monkeypatch):
    monkeypatch.setenv(ROOT_ENV_VAR, str(PAGES))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """browse_page reads files; any socket connection fails the test."""

    def refuse(*args, **kwargs):
        raise AssertionError("browse_page tried to use the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def parts(page: dict) -> list[str]:
    text = page["page_text"]
    return [normalize_text(t) for t in (text if isinstance(text, list) else [text])]


# --------------------------------------------------------------------------- Hannah's pages


def test_manifest_has_ten_pages_with_known_techniques():
    assert len(MANIFEST["pages"]) == 10
    assert {p["technique"] for p in MANIFEST["pages"]} <= set(EXPECTED_VISIBILITY)


@pytest.mark.parametrize("page", MANIFEST["pages"], ids=lambda p: p["technique"])
def test_attack_text_is_extracted_with_the_expected_visibility(page):
    chunks = fetch_chunks(page["file"])
    found = []
    for part in parts(page):
        matches = [c for c in chunks if part in c.text]
        assert matches, f"{page['file']}: attack text not in any chunk: {part[:60]!r}"
        found.append(matches[0])
    assert all(c.visibility == EXPECTED_VISIBILITY[page["technique"]] for c in found)
    if len(found) > 1:  # fragmentation: one chunk per fragment, in page order
        assert len({c.id for c in found}) == len(found)
        assert [c.index for c in found] == sorted(c.index for c in found)


@pytest.mark.parametrize("page", MANIFEST["pages"], ids=lambda p: p["technique"])
def test_unguarded_model_sees_the_attack(page):
    """Without a guardrail the attack reaches the model: the demo's 'agent falls' half."""
    text = browse_page(page["file"])
    assert all(part in text for part in parts(page))


# --------------------------------------------------------------------------- contract


def test_chunks_follow_the_contract():
    chunks = fetch_chunks(f"{BASE_URL}index.html")
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert len({c.id for c in chunks}) == len(chunks)
    assert all(c.url == f"{BASE_URL}index.html" for c in chunks)


def test_links_become_chunks():
    links = [c for c in fetch_chunks(BASE_URL) if c.source == "a[href]"]
    targets = {c.text.rsplit(" -> ", 1)[-1] for c in links}
    assert f"{BASE_URL}products/flex-leggings-midnight.html" in targets
    assert all(c.visibility == "metadata" for c in links)


def test_relative_links_resolve_against_the_page(tmp_path, monkeypatch):
    (tmp_path / "products").mkdir()
    (tmp_path / "products" / "a.html").write_text(
        '<p>A</p><a href="../index.html">Home</a><a href="https://shop.example/x">Out</a>'
    )
    monkeypatch.setenv(ROOT_ENV_VAR, str(tmp_path))
    links = [c.text for c in fetch_chunks("products/a.html") if c.source == "a[href]"]
    assert links == [f"Home -> {BASE_URL}index.html"]  # off-site links are dropped


# --------------------------------------------------------------------------- sandbox


@pytest.mark.parametrize(
    "url",
    [
        "https://shop.example/leggings",
        "http://evil.example/products/x.html",
        "https://localhost:8000/",  # only plain http on localhost
        "ftp://localhost/index.html",
        "file:///etc/passwd",
    ],
)
def test_only_localhost_is_allowed(url):
    with pytest.raises(BrowseError):
        normalize_url(url)
    assert browse(url).startswith("Cannot open")


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://localhost:8000/../../pyproject.toml", "outside the site"),
        ("http://localhost:8000/%2e%2e/%2e%2e/pyproject.toml", "outside the site"),
        ("/styles.css", "not a web page"),
        ("/missing.html", "Page not found"),
        ("", "Empty URL"),
        ("http://localhost:8000/x%00.html", "invalid path"),  # null byte
        ("http://localhost:8000/index%00.html", "invalid path"),
        ("http://localhost:abc/index.html", "invalid port"),
    ],
)
def test_bad_paths_are_refused(url, message):
    assert message in browse(url)


def test_directory_opens_its_index():
    assert browse("localhost:8000/") == browse(f"{BASE_URL}index.html").replace(
        f"Page: {BASE_URL}index.html", f"Page: {BASE_URL}"
    )


# --------------------------------------------------------------------------- guardrail hook


def test_model_only_sees_what_the_hook_returns():
    page = next(p for p in MANIFEST["pages"] if p["technique"] == "css_hidden")
    attack = parts(page)[0]

    def guard(chunks):
        return [
            c.model_copy(update={"text": "[removed]"}) if c.visibility == "hidden" else c
            for c in chunks
        ]

    text = make_browse_tool(on_chunks=guard)(page["file"])
    assert attack not in text
    assert "[removed]" in text


def test_output_is_only_chunk_text():
    seen = []

    def spy(chunks):
        seen.extend(chunks)
        return chunks

    text = browse(MANIFEST["pages"][0]["file"], on_chunks=spy)
    header, blank, *body = text.split("\n")
    assert header.startswith("Page: ") and blank == ""
    assert body == [c.text for c in seen]


def test_output_does_not_label_hidden_text():
    """Labels like 'hidden' would be a defense inside the unguarded agent."""
    text = browse_page(MANIFEST["pages"][0]["file"]).lower()
    assert "hidden" not in text.replace("hidden waistband", "")
    assert "metadata" not in text


def test_tool_description_is_neutral():
    description = browse_page.tool_spec["description"].lower()
    for word in ("only", "never", "untrusted", "ignore", "careful", "instructions"):
        assert word not in description


def test_userinfo_is_dropped():
    url = "http://evil.example@localhost:8000/index.html"
    assert normalize_url(url) == f"{BASE_URL}index.html"
    text = browse(url)
    assert text.startswith(f"Page: {BASE_URL}index.html")
    assert "evil.example" not in text
    assert all("@" not in c.url for c in fetch_chunks(url))


def test_hook_errors_propagate_so_nothing_unchecked_is_shown():
    """Fail closed: if the guardrail breaks, the tool call fails instead of showing raw text."""

    class DeciderDown(RuntimeError):
        pass

    def broken_guard(chunks):
        raise DeciderDown("Decider unreachable")

    with pytest.raises(DeciderDown):
        browse(MANIFEST["pages"][0]["file"], on_chunks=broken_guard)
