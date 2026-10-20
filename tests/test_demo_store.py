"""The demo-store manifest must match the pages it describes."""

import json
from pathlib import Path

STORE = Path(__file__).resolve().parents[1] / "eval" / "demo-store"
MANIFEST = json.loads((STORE / "manifest.json").read_text(encoding="utf-8"))


def test_every_hidden_instruction_appears_on_its_page():
    for item in MANIFEST["hidden_instructions"]:
        page = (STORE / item["on_page"]).read_text(encoding="utf-8")
        assert item["page_text"] in page, item["on_page"]


def test_clean_products_have_no_injected_text():
    injected = [i["injected_text"] for i in MANIFEST["hidden_instructions"]]
    for rel in MANIFEST["clean_products"]:
        page = (STORE / rel).read_text(encoding="utf-8")
        for text in injected:
            assert text not in page, rel
