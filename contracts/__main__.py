"""Regenerate contracts/schemas/*.schema.json from the models:

uv run python -m contracts
"""

from __future__ import annotations

import json
from pathlib import Path

from contracts.models import CONTRACT_VERSION, SCHEMAS

SCHEMA_DIR = Path(__file__).parent / "schemas"


def render(name: str) -> str:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"agent-phish-guard/contracts/{CONTRACT_VERSION}/{name}.schema.json",
        **SCHEMAS[name].model_json_schema(),
    }
    return json.dumps(schema, indent=2) + "\n"


def main() -> None:
    SCHEMA_DIR.mkdir(exist_ok=True)
    for name in SCHEMAS:
        (SCHEMA_DIR / f"{name}.schema.json").write_text(render(name))
        print(f"wrote contracts/schemas/{name}.schema.json")


if __name__ == "__main__":
    main()
