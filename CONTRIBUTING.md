# Contributing

Agent Phish Guard is built by Arxevia. Contributions are welcome: open an issue first for anything larger than a small fix.

## Workflow

- One branch and one pull request per change. Nobody pushes to `main` directly: a ruleset requires a pull request, one approving review and the `test` check, and blocks force-pushes and deletion.
- CI (`.github/workflows/ci.yml`) runs on every pull request:
  - **test:** `ruff check`, `ruff format --check` and `pytest`. It's the required check. **test (Python 3.11)** runs `pytest` on the oldest Python the package supports.
  - **package:** builds the wheel, checks it holds only `phish_guard`, installs it in a clean environment and runs both guardrails from it.
  - **terraform:** `terraform fmt` and `validate` for every stack.
- Approvals are dismissed when new commits are pushed, so the reviewer always approves what gets merged.

The team tracks its tasks in Notion. A PR description can carry a `Notion task: <link>` line (the PR template has it). The `Notion task sync` workflow then keeps that task's status current:
- **Opened:** the task moves to In progress.
- **Merged:** the task moves to Done once every done criterion on its page is checked. Otherwise the PR gets a comment listing what's unchecked.

## Before you push

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest                                   # no AWS calls, no Decider needed
RUN_DECIDER_TESTS=1 uv run pytest -m decider    # with the Decider server running
```

## Ground rules

- **Never commit credentials.** `.env`, Terraform state, `backend.hcl` and `*.tfvars` are git-ignored. The project never uses long-lived access keys: sign in with `aws login`, and on EC2 the instance role provides credentials.
- **Only pages we built.** The agent never visits real sites that host attacks. Attack pages and the demo store use the fictional Lumora brand, with addresses on `.example` domains and no real logos.
- **Fake data only.** The user profile is fictional, and the card number is the public test number `4242 4242 4242 4242`.
- **Invisible characters as escapes.** In Python and in generated JSON (`ensure_ascii=True`), write zero-width, tag and look-alike characters as `\u` escapes, so they stay visible in diffs. Only the attack fixtures in `eval/pages/` carry them literally, because they are the attack.
- **Numbers come from files.** Every number in the docs links to the script or result file it comes from. If you change a threshold or a question, re-run the evaluation and update the docs.
