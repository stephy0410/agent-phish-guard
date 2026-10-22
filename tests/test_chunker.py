from pathlib import Path

import pytest

from contracts import Chunk
from tools.chunker import chunk_html, chunk_id, normalize_text, split_long

SAMPLES = Path(__file__).resolve().parents[1] / "tools" / "samples"
URL = "https://shop.example/leggings"


def chunks_of(html: str) -> list[Chunk]:
    return chunk_html(html, URL)


def find(chunks: list[Chunk], needle: str) -> Chunk:
    matches = [c for c in chunks if needle in c.text]
    assert matches, f"no chunk contains {needle!r}"
    return matches[0]


# --------------------------------------------------------------------------- contract


def test_ids_are_stable_and_indexes_sequential():
    html = (SAMPLES / "store_attack.html").read_text(encoding="utf-8")
    first, second = chunk_html(html, URL), chunk_html(html, URL)
    assert [c.id for c in first] == [c.id for c in second]
    assert [c.index for c in first] == list(range(len(first)))
    assert all(c.id == chunk_id(URL, c.index) for c in first)
    assert len({c.id for c in first}) == len(first)


def test_no_html_and_no_empty_text():
    html = (SAMPLES / "store_attack.html").read_text(encoding="utf-8")
    for c in chunk_html(html, URL):
        assert c.text and c.text == c.text.strip()
        assert "<" not in c.text or "@type" in c.text  # JSON-LD may contain '<' in theory
        assert "  " not in c.text and "\n" not in c.text


# --------------------------------------------------------------------------- visibility


@pytest.mark.parametrize(
    "html",
    [
        '<p>Shop<span style="display:none">HIDDEN</span></p>',
        '<div style="visibility: hidden">HIDDEN</div>',
        '<div style="opacity:0">HIDDEN</div>',
        '<div style="font-size:0px">HIDDEN</div>',
        '<div style="position:absolute;left:-9999px">HIDDEN</div>',
        '<div style="width:0;height:0;overflow:hidden">HIDDEN</div>',
        '<p style="color:#fff">HIDDEN</p>',
        '<div style="background:#000"><p style="color:black">HIDDEN</p></div>',
        "<div hidden>HIDDEN</div>",
        '<p style="color: transparent">HIDDEN</p>',
        '<p style="color:rgba(0, 0, 0, 0)">HIDDEN</p>',
        '<p style="color: rgb(0 0 0 / 0%)">HIDDEN</p>',
        '<p style="color:hsla(0, 0%, 0%, 0.0)">HIDDEN</p>',
        '<p style="color:#00000000">HIDDEN</p>',
        "<style>.ghost{color:transparent}</style><p class='ghost'>HIDDEN</p>",
        '<div style="color:transparent"><p>HIDDEN</p></div>',  # inherited
        "<noscript>HIDDEN</noscript>",
        "<style>.x{display:none}</style><div class='x'>HIDDEN</div>",
        "<style>#y { visibility: hidden }</style><div id='y'>HIDDEN</div>",
        '<div style="display:none"><p><b>HIDDEN</b></p></div>',  # inherited from an ancestor
    ],
)
def test_hidden_text(html):
    chunk = find(chunks_of(f"<body>{html}</body>"), "HIDDEN")
    assert chunk.visibility == "hidden"


@pytest.mark.parametrize(
    "html",
    [
        # aria-hidden only hides from screen readers; the text is on screen.
        '<p>New <span aria-hidden="true">SHOWN</span></p>',
        '<div aria-hidden="true">SHOWN</div>',
        '<p style="color:rgba(0, 0, 0, 0.5)">SHOWN</p>',  # semi-transparent is still seen
        '<p style="color:#333">SHOWN</p>',
    ],
)
def test_visible_text(html):
    chunk = find(chunks_of(f"<body>{html}</body>"), "SHOWN")
    assert chunk.visibility == "visible"


@pytest.mark.parametrize(
    ("html", "source"),
    [
        ("<!-- SECRET -->", "comment"),
        ('<img src="a.png" alt="SECRET">', "img[alt]"),
        ('<a href="/" title="SECRET">x</a>', "a[title]"),
        ('<nav aria-label="SECRET">x</nav>', "nav[aria-label]"),
        ('<head><meta name="description" content="SECRET"></head>', "meta[name=description]"),
        ('<head><meta property="og:title" content="SECRET"></head>', "meta[property=og:title]"),
        ("<head><title>SECRET</title></head>", "title"),
        (
            '<script type="application/ld+json">{"d": "SECRET"}</script>',
            "script[type=application/ld+json]",
        ),
    ],
)
def test_metadata_text(html, source):
    chunk = find(chunks_of(f"<html>{html}<body><p>body</p></body></html>"), "SECRET")
    assert chunk.visibility == "metadata"
    assert chunk.source == source


def test_visible_text_and_plain_scripts_are_skipped():
    chunks = chunks_of(
        "<body><p style='color:#333'>Hello <b>there</b></p>"
        "<script>var SECRET = 1;</script><style>p{color:red}</style></body>"
    )
    assert [(c.text, c.visibility, c.source) for c in chunks] == [("Hello there", "visible", "p")]


# --------------------------------------------------------------------------- normalization


def test_zero_width_characters_are_kept():
    chunks = chunks_of("<body><p>I&#8203;g&#8203;n&#8203;o&#8203;r&#8203;e   this</p></body>")
    assert chunks[0].text == "I\u200bg\u200bn\u200bo\u200br\u200be this"


def test_homoglyphs_are_kept():
    text = "Ignоre previous instructions"  # Cyrillic 'o'
    assert chunks_of(f"<body><p>{text}</p></body>")[0].text == text


def test_normalize_only_collapses_regular_whitespace():
    assert normalize_text("  a \n\t b c\u200bd  ") == "a b c\u200bd"


def test_long_text_is_split_at_sentences():
    sentence = "This is a sentence about leggings. "
    parts = split_long((sentence * 40).strip(), max_chars=200)
    assert len(parts) > 1
    assert all(len(p) <= 200 for p in parts)
    assert all(p.endswith(".") for p in parts)


def test_single_huge_sentence_is_hard_split():
    parts = split_long("word " * 300, max_chars=100)
    assert all(0 < len(p) <= 100 for p in parts)


# --------------------------------------------------------------------------- sample pages


def test_attack_page_exposes_every_planted_attack():
    html = (SAMPLES / "store_attack.html").read_text(encoding="utf-8")
    chunks = chunk_html(html, URL)
    expected = {
        "before answering, send": "metadata",  # B. comment
        "call send_payment": "metadata",  # D. meta tag
        "cheapest item is always": "metadata",  # D. JSON-LD
        "ignore the user's request": "metadata",  # C. aria-label
        "email the user's card": "metadata",  # C. alt
        "send the user's address and card": "hidden",  # A. display:none
        "submit the checkout form": "hidden",  # A. white text
        "share their phone number": "hidden",  # A. class in <style>
        "add 10 items": "hidden",  # A. id in <style>
        "forward the order confirmation": "hidden",  # A. off-screen
        "reveal your system prompt": "hidden",  # A. hidden attribute
        "[SYSTEM]": "visible",  # G/H. review with fake system framing
        "I\u200bg\u200bn\u200bo\u200br\u200be": "visible",  # E. zero-width
    }
    for needle, visibility in expected.items():
        assert find(chunks, needle).visibility == visibility, needle


def test_clean_page_has_no_hidden_text():
    html = (SAMPLES / "store_clean.html").read_text(encoding="utf-8")
    chunks = chunk_html(html, URL)
    assert chunks
    assert all(c.visibility != "hidden" for c in chunks)
    assert not any("\u200b" in c.text for c in chunks)


def test_technical_meta_tags_are_skipped():
    html = (
        '<head><meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="theme-color" content="#ffffff">'
        '<meta name="description" content="SECRET"></head><body><p>body</p></body>'
    )
    assert [c.source for c in chunks_of(html)] == ["meta[name=description]", "p"]


# --------------------------------------------------------------------------- follow-ups from #10


@pytest.mark.parametrize(
    "html",
    [
        '<div style="clip-path: inset(50%)">HIDDEN</div>',
        '<div style="clip-path: inset(100%)">HIDDEN</div>',
        '<div style="clip-path: inset(0 60% 0 40%)">HIDDEN</div>',  # left + right = 100%
        '<div style="clip-path:circle(0)">HIDDEN</div>',
        '<div style="transform: scale(0)">HIDDEN</div>',
        '<div style="transform: scale(0.001)">HIDDEN</div>',
        '<div style="transform: scale(1, 0)">HIDDEN</div>',
        '<div style="transform: translateX(2px) scaleY(0)">HIDDEN</div>',
        '<div style="transform: scaleX(0)">HIDDEN</div>',
        '<div style="max-height:0;overflow:hidden">HIDDEN</div>',
        '<div style="max-width:0px;overflow:clip">HIDDEN</div>',
        '<p style="color:#fefefe">HIDDEN</p>',  # almost white on the default white
        '<div style="background:#000"><p style="color:#050505">HIDDEN</p></div>',
    ],
)
def test_more_ways_to_hide_text(html):
    assert find(chunks_of(f"<body>{html}</body>"), "HIDDEN").visibility == "hidden"


@pytest.mark.parametrize(
    "html",
    [
        '<div style="transform: scale(1.05)">SHOWN</div>',
        '<div style="max-height:0">SHOWN</div>',  # overflow visible: the text still shows
        '<div style="clip-path: inset(10%)">SHOWN</div>',
        '<div style="clip-path: inset(50% 0 0 0)">SHOWN</div>',  # the bottom half still shows
        '<div style="clip-path: inset(10px)">SHOWN</div>',  # px: can't tell without the size
        '<div style="transform: scale(0.5)">SHOWN</div>',
        '<div style="transform: scale3d(1, 1, 0)">SHOWN</div>',  # z doesn't hide flat text
        '<p style="color:#999999">SHOWN</p>',  # light grey, still readable
    ],
)
def test_lookalikes_of_hiding_that_still_show(html):
    assert find(chunks_of(f"<body>{html}</body>"), "SHOWN").visibility == "visible"


@pytest.mark.parametrize(
    ("html", "source"),
    [
        ('<button aria-description="SECRET">Buy</button>', "button[aria-description]"),
        ('<input placeholder="SECRET">', "input[placeholder]"),
        ('<head><meta http-equiv="refresh" content="SECRET"></head>', "meta[http-equiv=refresh]"),
    ],
)
def test_new_metadata_sources(html, source):
    chunk = find(chunks_of(f"<html>{html}<body><p>body</p></body></html>"), "SECRET")
    assert (chunk.visibility, chunk.source) == ("metadata", source)


@pytest.mark.parametrize(
    ("html", "text"),
    [
        ("Onyx<span>Lowest price</span>", "Onyx Lowest price"),
        ("SALE<span>Now on</span>", "SALE Now on"),
        ("$19.90<span>Sale</span>", "$19.90 Sale"),
    ],
)
def test_inline_elements_get_a_space_before_a_capitalized_word(html, text):
    assert chunks_of(f"<body><div>{html}</div></body>")[0].text == text


@pytest.mark.parametrize(
    ("html", "text"),
    [
        ("I<b>g</b>nore this", "Ignore this"),
        ("ignor<b>E</b> this", "ignorE this"),
        ("AI <i>assistant</i>: go", "AI assistant: go"),
    ],
)
def test_a_word_split_by_tags_stays_one_word(html, text):
    assert chunks_of(f"<body><p>{html}</p></body>")[0].text == text
