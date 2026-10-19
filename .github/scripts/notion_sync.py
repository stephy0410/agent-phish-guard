"""Keep a Notion task's status in step with the pull request that implements it.

Runs in GitHub Actions on pull_request events. The PR description links the task on a line like:

    Notion task: https://app.notion.com/p/Task-name-3fd47f6f17c98182921ed7ef0d49a4bf

- PR opened, reopened, marked ready or edited: a task in Backlog or Not started moves to
  In progress. Other statuses (Bloqueado, Done) are left alone.
- PR merged: the task moves to Done when every checkbox on its page is checked, the team's
  definition of done. Otherwise the status stays and the PR gets a comment listing what is left.

Standard library only, so the workflow needs no install step.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from typing import Any

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
STATUS_PROPERTY = "Status"
NOT_STARTED = {"Backlog", "Not started"}
IN_PROGRESS = "In progress"
DONE = "Done"
OPEN_ACTIONS = {"opened", "reopened", "ready_for_review", "edited"}

_URL = re.compile(r"https?://[^\s)\]>]+")
_PAGE_ID = re.compile(
    r"([0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$", re.I
)


def page_id(body: str) -> str | None:
    """The Notion page ID from the PR description's `Notion task:` line, if there is one."""
    for line in body.splitlines():
        if "notion task:" not in line.lower():
            continue
        for url in _URL.findall(line):
            if "notion." not in url:
                continue
            path = url.split("?")[0].split("#")[0]
            match = _PAGE_ID.search(path)
            if match:
                return match.group(1).replace("-", "").lower()
    return None


def unchecked(blocks: list[dict[str, Any]]) -> list[str]:
    """Text of every unchecked to-do block on the page."""
    return [
        "".join(part["plain_text"] for part in block["to_do"]["rich_text"])
        for block in blocks
        if block.get("type") == "to_do" and not block["to_do"]["checked"]
    ]


def decide(
    action: str, merged: bool, status: str, pending: list[str] | None
) -> tuple[str | None, str | None]:
    """The status to set (or None) and the PR comment to post (or None)."""
    if action in OPEN_ACTIONS:
        return (IN_PROGRESS if status in NOT_STARTED else None), None
    if action == "closed" and merged:
        if pending:
            items = "\n".join(f"- [ ] {item}" for item in pending)
            return None, (
                f"Merged, but the Notion task stays **{status}**: "
                f"these done criteria are still unchecked.\n\n{items}"
            )
        if status != DONE:
            return DONE, "Notion task moved to **Done**: every done criterion is checked."
    return None, None


class Notion:
    def __init__(self, token: str) -> None:
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"{NOTION_API}{path}", data=data, headers=self._headers, method=method
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())

    def status(self, page: str) -> str:
        properties = self._call("GET", f"/pages/{page}")["properties"]
        return properties[STATUS_PROPERTY]["status"]["name"]

    def set_status(self, page: str, status: str) -> None:
        body = {"properties": {STATUS_PROPERTY: {"status": {"name": status}}}}
        self._call("PATCH", f"/pages/{page}", body)

    def blocks(self, page: str) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        cursor = None
        while True:
            query = "?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
            result = self._call("GET", f"/blocks/{page}/children{query}")
            blocks.extend(result["results"])
            if not result.get("has_more"):
                return blocks
            cursor = result["next_cursor"]


def comment(repo: str, number: int, text: str) -> None:
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    request = urllib.request.Request(
        f"{api}/repos/{repo}/issues/{number}/comments",
        data=json.dumps({"body": text}).encode(),
        headers={
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
        },
        method="POST",
    )
    urllib.request.urlopen(request, timeout=30).close()


def main() -> int:
    with open(os.environ["GITHUB_EVENT_PATH"]) as f:
        event = json.load(f)
    pr = event["pull_request"]

    page = page_id(pr.get("body") or "")
    if page is None:
        print("No 'Notion task: <link>' line in the PR description; nothing to sync.")
        return 0
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        print("NOTION_TOKEN is not available (for example, a PR from a fork); skipping.")
        return 0

    notion = Notion(token)
    status = notion.status(page)
    merged = bool(pr.get("merged"))
    pending = unchecked(notion.blocks(page)) if event["action"] == "closed" and merged else None
    new_status, note = decide(event["action"], merged, status, pending)

    if new_status:
        notion.set_status(page, new_status)
        print(f"Notion task {page}: {status} -> {new_status}")
    else:
        print(f"Notion task {page}: stays {status}")
    if note:
        comment(event["repository"]["full_name"], pr["number"], note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
