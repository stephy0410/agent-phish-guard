"""HTML chunker: turns a web page into contract Chunks, keeping hidden and metadata text.

Spike for the Agent Phish Guard hackathon. It parses static HTML with BeautifulSoup (no
rendering), walks the tree in document order and emits one Chunk per block of text, tagged with
its visibility:

- ``visible``: text a person sees on the rendered page.
- ``hidden``: body text that is not shown (display:none, visibility:hidden, opacity:0,
  font-size:0, zero size or max size with overflow hidden, off-screen, clipped away with
  ``clip`` or ``clip-path: inset(50%)``, ``transform: scale(0)``, the ``hidden`` attribute, a
  transparent text color, or text colored like, or almost like, its background).
  ``aria-hidden`` does not count: that text is on screen, only screen readers skip it.
- ``metadata``: never body text (comments, alt/title/aria-label/aria-description/placeholder
  attributes, <title>, <meta>, JSON-LD).

Known limits (static parsing):
- CSS is read from inline ``style`` attributes and from simple rules in ``<style>`` blocks
  (selectors ``tag``, ``.class``, ``#id``). External stylesheets, combinators, media queries
  and JavaScript changes are ignored. Rendering with Playwright would close that gap.
- Background color is tracked only through inline/simple CSS; images and gradients are ignored.
- Text that CSS generates (``::before`` / ``::after`` with ``content``) is not in the HTML text,
  so it is not extracted.
- Inline elements are joined without a space unless one is in the HTML, except before a
  capitalized word ("Onyx" + "Lowest price", "SALE" + "Now", "$19.90" + "Sale"), where CSS
  usually spaces them on screen. This is for readability only: the scanner and the model read
  the same chunk text, so spacing can't make them disagree.

Text normalization is deliberately minimal: only regular whitespace is collapsed. Zero-width
characters and non-ASCII look-alikes are kept, because they are attack signals (taxonomy
classes E and F) that Decider has to see.

Usage:
    python -m tools.chunker tools/samples/store_attack.html --url https://shop.example/leggings
"""

from __future__ import annotations

import argparse
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from bs4.element import Doctype, ProcessingInstruction

from contracts import Chunk

MAX_CHARS = 500
"""Chunks longer than this are split at sentence boundaries."""

# Elements whose text forms its own chunk. Text inside inline tags (a, span, b...) joins the
# nearest block ancestor's chunk.
BLOCK_TAGS = frozenset(
    [
        "address",
        "article",
        "aside",
        "blockquote",
        "body",
        "button",
        "caption",
        "dd",
        "details",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "label",
        "legend",
        "li",
        "main",
        "nav",
        "ol",
        "option",
        "p",
        "pre",
        "section",
        "summary",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    ]
)
SKIP_TAGS = frozenset({"style", "link", "iframe", "svg", "canvas", "object", "embed"})
ALWAYS_HIDDEN_TAGS = frozenset({"noscript", "template"})
METADATA_ATTRS = ("alt", "title", "aria-label", "aria-description", "placeholder")
TECHNICAL_META = frozenset(
    {"viewport", "theme-color", "color-scheme", "robots", "referrer", "format-detection"}
)
"""<meta> names that hold browser settings, not text, so they are not chunked."""

# Only regular whitespace (plus NBSP). Not \s: we must not touch zero-width characters.
_WHITESPACE = re.compile(r"[ \t\n\r\f\v ]+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_CSS_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_SIMPLE_SELECTOR = re.compile(r"^(?:[a-zA-Z][\w-]*|\.[\w-]+|#[\w-]+)$")
_LENGTH = re.compile(r"^(-?\d+(?:\.\d+)?)(px|em|rem|%|pt|vw|vh)?$")

_NAMED_COLORS = {
    "white": "#ffffff",
    "black": "#000000",
    "transparent": None,
}
DEFAULT_BACKGROUND = "#ffffff"
DEFAULT_COLOR = "#000000"


# --------------------------------------------------------------------------- text helpers


def normalize_text(text: str) -> str:
    """Collapse regular whitespace to single spaces and trim. Keeps zero-width characters."""
    return _WHITESPACE.sub(" ", text).strip(" ")


def split_long(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    """Split text longer than ``max_chars`` at sentence ends, then at spaces if needed."""
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(text):
        while len(sentence) > max_chars:  # one huge sentence: hard split at a space
            cut = sentence.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            if current:
                parts.append(current)
                current = ""
            parts.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) > max_chars:
            parts.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        parts.append(current)
    return [p for p in parts if p]


def chunk_id(url: str, index: int) -> str:
    """Stable id from the contract: first 8 hex of sha1(url), then the index."""
    return f"{hashlib.sha1(url.encode('utf-8')).hexdigest()[:8]}:{index}"


# --------------------------------------------------------------------------- CSS helpers


def parse_declarations(css: str) -> dict[str, str]:
    """``"display: none; color:#FFF"`` -> ``{"display": "none", "color": "#fff"}``."""
    decls: dict[str, str] = {}
    for part in css.split(";"):
        if ":" not in part:
            continue
        prop, _, value = part.partition(":")
        value = value.replace("!important", "").strip().lower()
        if prop.strip() and value:
            decls[prop.strip().lower()] = value
    return decls


def parse_stylesheets(soup: BeautifulSoup) -> dict[str, dict[str, str]]:
    """Read simple rules (``tag``, ``.class``, ``#id``) from every <style> block."""
    rules: dict[str, dict[str, str]] = {}
    for style in soup.find_all("style"):
        css = re.sub(r"/\*.*?\*/", "", style.get_text(), flags=re.DOTALL)
        for selectors, body in _CSS_RULE.findall(css):
            decls = parse_declarations(body)
            for selector in selectors.split(","):
                selector = selector.strip()
                if _SIMPLE_SELECTOR.match(selector):
                    rules.setdefault(selector.lower(), {}).update(decls)
    return rules


def normalize_color(value: str | None) -> str | None:
    """Return ``#rrggbb`` for hex, rgb() and a few names; None if unknown or transparent."""
    if not value:
        return None
    value = value.strip().lower()
    if value in _NAMED_COLORS:
        return _NAMED_COLORS[value]
    if re.fullmatch(r"#[0-9a-f]{3}", value):
        return "#" + "".join(c * 2 for c in value[1:])
    if re.fullmatch(r"#[0-9a-f]{6}", value):
        return value
    m = re.fullmatch(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)", value)
    if m:
        if m.group(4) is not None and float(m.group(4)) == 0:
            return None
        return "#" + "".join(f"{min(int(g), 255):02x}" for g in m.groups()[:3])
    return None


def is_transparent(value: str | None) -> bool:
    """True for ``transparent``, ``rgba(..., 0)``, ``hsla(..., 0)``, ``#rrggbb00`` and ``#rgb0``.

    ``normalize_color`` returns None for these, which would make the element inherit its
    parent's color, so transparent text has to be checked on its own.
    """
    if not value:
        return False
    value = value.strip().lower()
    if value == "transparent":
        return True
    if re.fullmatch(r"#[0-9a-f]{3}0|#[0-9a-f]{6}00", value):
        return True
    m = re.fullmatch(r"(?:rgb|hsl)a?\((.*)\)", value)
    if not m:
        return False
    # Alpha is the 4th comma-separated value, or what follows "/" in the space syntax.
    inner = m.group(1)
    parts = inner.split("/") if "/" in inner else inner.split(",")
    if len(parts) not in (2, 4):
        return False
    alpha = parts[-1].strip()
    try:
        number = float(alpha[:-1]) / 100 if alpha.endswith("%") else float(alpha)
    except ValueError:
        return False
    return number <= 0.01


def background_color(decls: dict[str, str]) -> str | None:
    if "background-color" in decls:
        return normalize_color(decls["background-color"])
    for token in decls.get("background", "").split():
        color = normalize_color(token)
        if color:
            return color
    return None


def _length(value: str | None) -> float | None:
    if value is None:
        return None
    m = _LENGTH.match(value.strip())
    return float(m.group(1)) if m else None


def hides_element(decls: dict[str, str], tag: Tag) -> bool:
    """True if these styles or attributes keep the element from being seen."""
    # aria-hidden is deliberately not here: it hides text from screen readers, not from eyes.
    if tag.has_attr("hidden") or is_transparent(decls.get("color")):
        return True
    if decls.get("display") == "none" or decls.get("visibility") in ("hidden", "collapse"):
        return True
    opacity = _length(decls.get("opacity"))
    if opacity is not None and opacity <= 0.01:
        return True
    if _length(decls.get("font-size")) == 0:
        return True
    zero_box = _length(decls.get("width")) == 0 or _length(decls.get("height")) == 0
    if zero_box and decls.get("overflow") in ("hidden", "clip"):
        return True
    if decls.get("position") in ("absolute", "fixed"):
        for side in ("left", "top", "right"):
            offset = _length(decls.get(side))
            if offset is not None and offset <= -1000:
                return True
    indent = _length(decls.get("text-indent"))
    if indent is not None and indent <= -1000:
        return True
    zero_max = _length(decls.get("max-width")) == 0 or _length(decls.get("max-height")) == 0
    if zero_max and decls.get("overflow") in ("hidden", "clip"):
        return True
    if _clipped_away(decls.get("clip-path", "")) or _scaled_away(decls.get("transform", "")):
        return True
    clip = decls.get("clip", "")
    return clip.startswith(("rect(0", "rect(1px, 1px, 1px"))


_INSET = re.compile(r"inset\(([^)]*)\)")
_SCALE = re.compile(r"\bscale(x|y|3d)?\(([^)]*)\)")
MIN_SCALE = 0.1
"""A scale factor at or below this shrinks text out of sight."""


def _clipped_away(clip_path: str) -> bool:
    """True when ``clip-path`` leaves nothing visible.

    ``circle(0)`` / ``ellipse(0)``, or an ``inset()`` whose top + bottom or left + right
    percentages add up to 100% or more (``inset(50%)``, ``inset(100%)``). ``inset(50% 0 0 0)``
    still shows the bottom half, so it doesn't count. Insets in px can't be judged without the
    element's size and are left alone.
    """
    value = clip_path.strip()
    if re.match(r"(circle|ellipse)\(\s*0(\.0+)?(px|%)?\s*[)a-z]", value):
        return True
    match = _INSET.search(value)
    if not match:
        return False
    parts = match.group(1).split(" round ")[0].split()
    if not 1 <= len(parts) <= 4:
        return False
    top, right, bottom, left = {
        1: lambda p: (p[0], p[0], p[0], p[0]),
        2: lambda p: (p[0], p[1], p[0], p[1]),
        3: lambda p: (p[0], p[1], p[2], p[1]),
        4: lambda p: (p[0], p[1], p[2], p[3]),
    }[len(parts)](parts)

    def pct(v: str) -> float | None:
        if v.endswith("%"):
            try:
                return float(v[:-1])
            except ValueError:
                return None
        return 0.0 if re.fullmatch(r"0(\.0+)?(px)?", v) else None

    for a, b in ((top, bottom), (left, right)):
        pa, pb = pct(a), pct(b)
        if pa is not None and pb is not None and pa + pb >= 100:
            return True
    return False


def _scaled_away(transform: str) -> bool:
    """True when a ``scale``/``scaleX``/``scaleY``/``scale3d`` factor is at or below 0.1."""
    for match in _SCALE.finditer(transform):
        kind, args = match.groups()
        factors = [a.strip() for a in args.split(",") if a.strip()]
        if kind == "3d":
            factors = factors[:2]  # x and y; z doesn't hide flat text
        for factor in factors:
            try:
                value = float(factor[:-1]) / 100 if factor.endswith("%") else float(factor)
            except ValueError:
                continue
            if abs(value) <= MIN_SCALE:
                return True
    return False


def _luminance(hex_color: str) -> float:
    """WCAG relative luminance of ``#rrggbb``."""

    def channel(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


NEAR_INVISIBLE_CONTRAST = 1.1
"""Below this WCAG contrast ratio text is effectively invisible (#fefefe on white is 1.01;
the faintest readable grey on white, #eeeeee, is 1.16)."""


def _same_looking(color: str, background: str) -> bool:
    """True when text and background are the same color, or too close to tell apart."""
    if color == background:
        return True
    lighter, darker = sorted((_luminance(color), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05) < NEAR_INVISIBLE_CONTRAST


# --------------------------------------------------------------------------- the walk


@dataclass
class _Piece:
    text: str
    visibility: str
    source: str | None
    group: object  # pieces with the same group that arrive back to back are merged


@dataclass
class _Context:
    hidden_by: Tag | None = None
    block: Tag | None = None
    color: str = DEFAULT_COLOR
    background: str = DEFAULT_BACKGROUND


@dataclass
class _Walker:
    rules: dict[str, dict[str, str]]
    pieces: list[_Piece] = field(default_factory=list)

    def styles_for(self, tag: Tag) -> dict[str, str]:
        decls: dict[str, str] = {}
        decls.update(self.rules.get(tag.name, {}))
        for cls in tag.get("class", []) or []:
            decls.update(self.rules.get(f".{cls.lower()}", {}))
        if tag.get("id"):
            decls.update(self.rules.get(f"#{str(tag['id']).lower()}", {}))
        decls.update(parse_declarations(str(tag.get("style", ""))))
        return decls

    def emit_metadata(self, text: str | None, source: str) -> None:
        if text and normalize_text(text):
            # Each metadata item is its own chunk: a unique group object never merges.
            self.pieces.append(_Piece(text, "metadata", source, object()))

    def walk(self, node: Tag, ctx: _Context) -> None:
        for child in node.children:
            if isinstance(child, Comment):
                self.emit_metadata(str(child), "comment")
            elif isinstance(child, (Doctype, ProcessingInstruction)):
                continue
            elif isinstance(child, NavigableString):
                self.visit_text(str(child), ctx)
            elif isinstance(child, Tag):
                self.visit_tag(child, ctx)

    def visit_text(self, text: str, ctx: _Context) -> None:
        if ctx.hidden_by is not None:
            self.pieces.append(_Piece(text, "hidden", ctx.hidden_by.name, ("h", id(ctx.hidden_by))))
        elif ctx.block is not None:
            self.pieces.append(_Piece(text, "visible", ctx.block.name, ("v", id(ctx.block))))
        # Text outside <body> and outside a known element (rare) is ignored.

    def visit_tag(self, tag: Tag, ctx: _Context) -> None:
        name = tag.name
        if name in SKIP_TAGS:
            return
        if name == "title":
            self.emit_metadata(tag.get_text(), "title")
            return
        if name == "meta":
            attr = next((a for a in ("name", "property", "http-equiv") if tag.get(a)), None)
            key = tag.get(attr) if attr else None
            if key and tag.get("content") and str(key).lower() not in TECHNICAL_META:
                self.emit_metadata(str(tag["content"]), f"meta[{attr}={key}]")
            return
        if name == "script":
            if str(tag.get("type", "")).lower() == "application/ld+json":
                self.emit_metadata(tag.get_text(), "script[type=application/ld+json]")
            return

        for attr in METADATA_ATTRS:
            if tag.get(attr):
                self.emit_metadata(str(tag[attr]), f"{name}[{attr}]")

        decls = self.styles_for(tag)
        child = _Context(
            hidden_by=ctx.hidden_by,
            block=tag if name in BLOCK_TAGS else ctx.block,
            color=normalize_color(decls.get("color")) or ctx.color,
            background=background_color(decls) or ctx.background,
        )
        if child.hidden_by is None and (
            name in ALWAYS_HIDDEN_TAGS
            or hides_element(decls, tag)
            or ("color" in decls and _same_looking(child.color, child.background))
        ):
            child.hidden_by = tag
        self.walk(tag, child)


# --------------------------------------------------------------------------- public API


def _joiner(left: str, right: str) -> str:
    """A space between two inline elements when the second starts a capitalized word.

    "Onyx" + "Lowest price", "SALE" + "Now" and "$19.90" + "Sale" read as separate words on
    screen. A word split by tags stays whole: "I" + "g" + "nore", "ignor" + "E". A readability
    choice only; the scanner and the model see the same text either way.
    """
    if left[-1:].isalnum() and right[:1].isupper() and right[1:2].islower():
        return " "
    return ""


def chunk_html(html: str, url: str, max_chars: int = MAX_CHARS) -> list[Chunk]:
    """Split a page into contract Chunks in document order."""
    soup = BeautifulSoup(html, "html.parser")
    walker = _Walker(rules=parse_stylesheets(soup))
    walker.walk(soup, _Context())

    # Merge consecutive pieces of the same block, then normalize and split.
    merged: list[_Piece] = []
    for piece in walker.pieces:
        if merged and merged[-1].group == piece.group:
            merged[-1].text += _joiner(merged[-1].text, piece.text) + piece.text
        else:
            merged.append(_Piece(piece.text, piece.visibility, piece.source, piece.group))

    chunks: list[Chunk] = []
    for piece in merged:
        text = normalize_text(piece.text)
        if not text:
            continue
        for part in split_long(text, max_chars):
            chunks.append(
                Chunk(
                    id=chunk_id(url, len(chunks)),
                    url=url,
                    index=len(chunks),
                    text=part,
                    visibility=piece.visibility,
                    source=piece.source,
                )
            )
    return chunks


def main() -> None:
    parser = argparse.ArgumentParser(description="Chunk an HTML file and print the result.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--url", default=None, help="URL to record (default: file:// path)")
    parser.add_argument("--max-chars", type=int, default=MAX_CHARS)
    args = parser.parse_args()

    url = args.url or args.path.resolve().as_uri()
    for c in chunk_html(args.path.read_text(encoding="utf-8"), url, args.max_chars):
        text = c.text if len(c.text) <= 90 else c.text[:87] + "..."
        print(f"{c.id:<12} {c.visibility:<9} {c.source or '-':<34} {text!r}")


if __name__ == "__main__":
    main()
