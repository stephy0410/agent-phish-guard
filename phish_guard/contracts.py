"""The data that moves through the guardrails.

    page fetcher --Chunk--> ChunkScanner --Decision, Action--> the agent
    agent's tool call --> ToolCallGuard --Decision, Action--> Strands (proceed, confirm, deny)
    evaluation datasets: LabeledChunk

These models are the source of truth. In the agent-phish-guard repo, contracts/schemas/ holds
their JSON Schemas (``uv run python -m contracts``) and docs/contract.md explains each field.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "CONTRACT_VERSION",
    "SCHEMAS",
    "Action",
    "ActionType",
    "Chunk",
    "Decision",
    "Evasion",
    "ExpectedAction",
    "Intent",
    "LabeledChunk",
    "QuestionType",
    "SubjectKind",
    "Technique",
    "Vector",
    "Visibility",
]

CONTRACT_VERSION = "0.1"

Visibility = Literal["visible", "hidden", "metadata"]
SubjectKind = Literal["chunk", "tool_call"]
QuestionType = Literal["noul", "choice", "score"]
ActionType = Literal["proceed", "guide", "confirm", "deny"]

# Label axes from eval/attack-taxonomy.md.
Vector = Literal["dom", "metadata", "attribute", "ugc"]
Technique = Literal[
    "css_hidden",  # A
    "html_comment",  # B
    "alt_aria",  # C
    "meta_tag",  # D
    "zero_width",  # E
    "homoglyph",  # F
    "ugc_injection",  # G
    "fake_system",  # H
    "fragmentation",  # I
    "encoded",  # J
]
Intent = Literal[
    "data_exfiltration",
    "unauthorized_action",
    "instruction_override",
    "tool_hijack",
    "misdirection",
]
Evasion = Literal["plain", "obfuscated"]
ExpectedAction = Literal["allow", "flag_chunk", "block_tool_call"]


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
    """One row of an evaluation dataset, labeled on the axes of eval/attack-taxonomy.md.

    Stored as JSONL. Benign look-alikes carry the vector, technique and evasion of the attack
    class they imitate, so false positives can be reported per class.
    """

    chunk: Chunk
    is_attack: bool
    vector: Vector = Field(
        description=(
            "Where the text lives in the page source: the body ('dom', including HTML comments), "
            "the <head> ('metadata'), an HTML attribute, or user-generated content. Whether a "
            "person sees it is Chunk.visibility, a separate axis."
        )
    )
    technique: Technique = Field(
        description="Taxonomy class (A to J). Benign rows use the class they imitate."
    )
    intent: Intent | None = Field(
        default=None, description="What the attack wants. Required for attacks, null for benign."
    )
    evasion: Evasion
    expected_action: ExpectedAction = Field(
        description=(
            "Ground truth for the guardrail. 'allow': every Action is 'proceed'. 'flag_chunk': "
            "the chunk's Action is not 'proceed'. 'block_tool_call': the resulting tool call's "
            "Action is not 'proceed'."
        )
    )
    notes: str | None = None

    @model_validator(mode="after")
    def _check_labels(self) -> LabeledChunk:
        if self.is_attack != (self.intent is not None):
            raise ValueError("intent is required for attacks and must be null for benign rows")
        if self.is_attack == (self.expected_action == "allow"):
            raise ValueError("attacks must not expect 'allow', and benign rows must")
        return self


SCHEMAS: dict[str, type[BaseModel]] = {
    "chunk": Chunk,
    "decision": Decision,
    "action": Action,
    "labeled_chunk": LabeledChunk,
}
