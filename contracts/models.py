"""The data that moves between the tools, the guardrails and the evaluation.

    tools/  --Chunk-->  guardrails/  --Decision-->  guardrails/  --Action-->  agent
                             eval/ reads LabeledChunk, Decision and Action

These models are the source of truth. contracts/schemas/*.schema.json is generated from them
with `uv run python -m contracts`; docs/contract.md explains each field.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = "0.1"

Visibility = Literal["visible", "hidden", "metadata"]
SubjectKind = Literal["chunk", "tool_call"]
QuestionType = Literal["noul", "choice", "score"]
ActionType = Literal["proceed", "guide", "confirm", "deny"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Chunk(_Model):
    """A piece of text taken from a web page, checked before it enters the model's context."""

    id: str = Field(
        min_length=1,
        description="Unique within the page fetch, stable across runs, e.g. '<url hash>:<index>'.",
    )
    url: str = Field(min_length=1, description="The page the text came from.")
    index: int = Field(ge=0, description="Position of the chunk in the page, from 0.")
    text: str = Field(
        min_length=1, description="Plain text: no HTML tags, whitespace collapsed to single spaces."
    )
    visibility: Visibility = Field(
        description=(
            "'visible': a person sees it on the rendered page. "
            "'hidden': in the page body but not shown (display:none, visibility:hidden, zero size "
            "or opacity, off-screen, same color as the background). "
            "'metadata': never body text (HTML comments, alt/title/aria-label attributes, meta "
            "tags, JSON-LD)."
        )
    )
    source: str | None = Field(
        default=None,
        description="Where in the HTML the text came from, e.g. 'p', 'img[alt]', 'comment'.",
    )


class Decision(_Model):
    """Decider's answer to one typed question about one chunk or one proposed tool call."""

    subject_kind: SubjectKind
    subject_id: str = Field(
        min_length=1, description="Chunk.id, or the Strands toolUseId of the tool call."
    )
    question: str = Field(
        min_length=1, description="Key of the typed question, e.g. 'instructs_ai'."
    )
    question_type: QuestionType
    label: str = Field(
        min_length=1,
        description=(
            "The chosen answer: 'true' or 'false' for noul, the option name for choice, "
            "the nearest level for score."
        ),
    )
    confidence: float = Field(
        ge=0,
        le=1,
        description=(
            "Decider's normalized confidence, (N * p_max - 1) / (N - 1): 0 means a uniform "
            "guess, 1 means certain. For noul this is |2p - 1|."
        ),
    )
    probabilities: dict[str, float] = Field(
        description="Probability of every option. For noul: {'true': p, 'false': 1 - p}."
    )
    model: str = Field(min_length=1, description="Decider checkpoint that answered.")
    latency_ms: float | None = Field(default=None, ge=0, description="Server-side latency.")


class Action(_Model):
    """What the guardrail does with a chunk or a tool call, and why."""

    type: ActionType = Field(
        description=(
            "'proceed': let it through. 'guide': let the model retry with feedback. "
            "'confirm': ask the user first (tool calls only). 'deny': block it."
        )
    )
    subject_kind: SubjectKind
    subject_id: str = Field(min_length=1)
    reason: str = Field(
        min_length=1, description="Why, for logs and metrics. Never shown to the model."
    )
    message: str | None = Field(
        default=None,
        description=(
            "Text for whoever acts next: feedback to the model for 'guide', the question to the "
            "user for 'confirm', the replacement or cancellation text for 'deny'. Null for "
            "'proceed'."
        ),
    )
    decisions: list[Decision] = Field(
        default_factory=list, description="The decisions this action was based on."
    )

    @model_validator(mode="after")
    def _check_message(self) -> Action:
        if self.type == "proceed" and self.message is not None:
            raise ValueError("'proceed' takes no message")
        if self.type != "proceed" and not self.message:
            raise ValueError(f"'{self.type}' needs a message")
        if self.type == "confirm" and self.subject_kind != "tool_call":
            raise ValueError("'confirm' only applies to tool calls")
        return self


class LabeledChunk(_Model):
    """One row of an evaluation dataset: a chunk and its ground truth. Stored as JSONL."""

    chunk: Chunk
    label: Literal["injection", "benign"]
    attack_type: str | None = Field(
        default=None,
        description="Attack taxonomy category. Required for 'injection', null for 'benign'.",
    )
    notes: str | None = None

    @model_validator(mode="after")
    def _check_attack_type(self) -> LabeledChunk:
        if (self.label == "injection") != (self.attack_type is not None):
            raise ValueError("attack_type is required for injections and must be null for benign")
        return self


SCHEMAS: dict[str, type[BaseModel]] = {
    "chunk": Chunk,
    "decision": Decision,
    "action": Action,
    "labeled_chunk": LabeledChunk,
}
