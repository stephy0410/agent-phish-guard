import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "notion_sync", Path(__file__).parents[1] / ".github" / "scripts" / "notion_sync.py"
)
notion_sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(notion_sync)

PAGE = "3fd47f6f17c98182921ed7ef0d49a4bf"


@pytest.mark.parametrize(
    "body",
    [
        f"Notion task: https://app.notion.com/p/Stephy-Instalar-Decider-2B-y-probar-la-CLI-{PAGE}",
        f"Notion task: https://app.notion.com/p/Task-{PAGE}?source=copy_link",
        f"## What\n\nnotion task: [Instalar Decider](https://www.notion.so/{PAGE})\n",
        "Notion task: https://www.notion.so/3fd47f6f-17c9-8182-921e-d7ef0d49a4bf",
    ],
)
def test_page_id_found(body):
    assert notion_sync.page_id(body) == PAGE


@pytest.mark.parametrize(
    "body",
    [
        "",
        "Notion task: Stephy: Instalar Decider 2B",
        f"See https://app.notion.com/p/Task-{PAGE} for context",
        "Notion task: <!-- paste the task's link -->",
        f"Notion task: https://github.com/stephy0410/agent-phish-guard/pull/{PAGE}",
    ],
)
def test_page_id_missing(body):
    assert notion_sync.page_id(body) is None


def todo(text, checked):
    return {"type": "to_do", "to_do": {"checked": checked, "rich_text": [{"plain_text": text}]}}


def test_unchecked_lists_only_open_todos():
    blocks = [
        {"type": "heading_3", "heading_3": {}},
        todo("Repo exists", True),
        todo("Teammates have access", False),
    ]
    assert notion_sync.unchecked(blocks) == ["Teammates have access"]


@pytest.mark.parametrize(
    ("action", "status", "expected"),
    [
        ("opened", "Not started", "In progress"),
        ("opened", "Backlog", "In progress"),
        ("edited", "Not started", "In progress"),
        ("opened", "In progress", None),
        ("opened", "Bloqueado", None),
        ("reopened", "Done", None),
    ],
)
def test_open_moves_only_unstarted_tasks(action, status, expected):
    assert notion_sync.decide(action, False, status, None) == (expected, None)


def test_merge_with_everything_checked_marks_done():
    new_status, note = notion_sync.decide("closed", True, "In progress", [])
    assert new_status == "Done"
    assert "Done" in note


def test_merge_with_open_criteria_keeps_status_and_lists_them():
    new_status, note = notion_sync.decide("closed", True, "In progress", ["Teammates have access"])
    assert new_status is None
    assert "- [ ] Teammates have access" in note


def test_closed_without_merge_does_nothing():
    assert notion_sync.decide("closed", False, "In progress", None) == (None, None)
