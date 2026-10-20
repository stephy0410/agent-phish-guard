"""``browse_page``: the agent's browsing tool. It opens local sandbox pages only.

The team rule is that the agent never visits real sites, so this tool has no HTTP client at all.
A URL like ``http://localhost:8000/products/x.html`` is mapped to a file under the sandbox root
(``eval/demo-store`` by default, or ``BROWSE_ROOT``) and read from disk. Anything else (another
host, ``https://``, a path that escapes the root) is refused.

Everything the model sees comes out of the chunker: the page is split into contract ``Chunk``s,
the links on the page become chunks too, the optional ``on_chunks`` hook (where the guardrail
plugs in) can drop or rewrite them, and only the text of the remaining chunks is returned. No
raw HTML or side channel reaches the model, so nothing can skip the guardrail.

The returned text does not label chunks as hidden or metadata. Telling the model "this was
hidden" would already be a defense, and the unguarded agent has to be able to fall for the demo.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup
from strands import tool

from contracts import Chunk
from tools.chunker import chunk_html, chunk_id, normalize_text

BASE_URL = "http://localhost:8000/"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})
PAGE_SUFFIXES = frozenset({".html", ".htm"})
ROOT_ENV_VAR = "BROWSE_ROOT"
DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "eval" / "demo-store"

ChunkHook = Callable[[list[Chunk]], list[Chunk]]
"""Receives the page's chunks and returns the ones the model may see (possibly rewritten)."""


class BrowseError(ValueError):
    """The URL can't be opened: not local, outside the sandbox, or not a page."""


def sandbox_root() -> Path:
    return Path(os.environ.get(ROOT_ENV_VAR) or DEFAULT_ROOT).resolve()


def normalize_url(url: str, base: str = BASE_URL) -> str:
    """Resolve ``url`` against ``base`` and return it as an absolute localhost URL.

    Accepts absolute localhost URLs, bare paths (``products/x.html``, ``/index.html``) and
    ``localhost:8000/...`` without a scheme. Raises ``BrowseError`` for any other host or scheme.
    """
    url = url.strip()
    if not url:
        raise BrowseError("Empty URL.")
    if url.startswith(("localhost", "127.0.0.1")):
        url = "http://" + url
    absolute = urljoin(base, url)
    parts = urlsplit(absolute)
    if parts.scheme != "http" or parts.hostname not in LOCAL_HOSTS:
        raise BrowseError(f"Cannot open {url}: only pages on {BASE_URL} are available.")
    return absolute


def resolve_path(url: str, root: Path | None = None) -> Path:
    """Map a localhost URL to a page file inside the sandbox root."""
    root = (root or sandbox_root()).resolve()
    rel = unquote(urlsplit(url).path).lstrip("/")
    path = (root / rel).resolve()
    if not path.is_relative_to(root):
        raise BrowseError(f"Cannot open {url}: outside the site.")
    if path.is_dir():
        path = path / "index.html"
    if path.suffix.lower() not in PAGE_SUFFIXES:
        raise BrowseError(f"Cannot open {url}: not a web page.")
    if not path.is_file():
        raise BrowseError(f"Page not found: {url}")
    return path


def link_chunks(html: str, page_url: str, start: int) -> list[Chunk]:
    """One chunk per local link, ``'<link text> -> <absolute url>'``, indexed from ``start``.

    Links are page content like any other text, so they go through the guardrail as chunks
    (``source='a[href]'``, ``visibility='metadata'``) instead of reaching the model directly.
    Links to other hosts are dropped: the agent can't open them anyway.
    """
    chunks: list[Chunk] = []
    seen: set[str] = set()
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        try:
            target = normalize_url(str(a["href"]), base=page_url)
        except BrowseError:
            continue
        target = target.split("#")[0]
        label = normalize_text(a.get_text(" "))
        text = f"{label} -> {target}" if label else target
        if target in seen or target == page_url:
            continue
        seen.add(target)
        index = start + len(chunks)
        chunks.append(
            Chunk(
                id=chunk_id(page_url, index),
                url=page_url,
                index=index,
                text=text,
                visibility="metadata",
                source="a[href]",
            )
        )
    return chunks


def fetch_chunks(url: str, root: Path | None = None) -> list[Chunk]:
    """Read a sandbox page and return its text chunks followed by its link chunks."""
    page_url = normalize_url(url)
    html = resolve_path(page_url, root).read_text(encoding="utf-8")
    chunks = chunk_html(html, page_url)
    return chunks + link_chunks(html, page_url, start=len(chunks))


def render(page_url: str, chunks: list[Chunk]) -> str:
    """The text the model reads: the page URL, then one chunk per line."""
    lines = [f"Page: {page_url}", ""]
    lines += [chunk.text for chunk in chunks]
    return "\n".join(lines)


def browse(url: str, on_chunks: ChunkHook | None = None, root: Path | None = None) -> str:
    """Open a sandbox page and return what the model may see. Errors come back as text."""
    try:
        page_url = normalize_url(url)
        chunks = fetch_chunks(page_url, root)
    except BrowseError as exc:
        return str(exc)
    if on_chunks is not None:
        chunks = on_chunks(chunks)
    return render(page_url, chunks)


def make_browse_tool(on_chunks: ChunkHook | None = None, root: Path | None = None):
    """Build the ``browse_page`` tool, optionally with a guardrail hook over each page's chunks."""

    @tool(name="browse_page")
    def browse_page(url: str) -> str:
        """Open a page of the online store and return its text and links.

        Start at http://localhost:8000/ (the store's home page) and follow links from there.

        Args:
            url: Address of the page, for example http://localhost:8000/ or a link from a page.
        """
        return browse(url, on_chunks=on_chunks, root=root)

    return browse_page


browse_page = make_browse_tool()
"""The tool without a guardrail: the agent's "no guard" baseline."""
