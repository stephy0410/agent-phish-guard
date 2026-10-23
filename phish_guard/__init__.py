"""Agent Phish Guard: Strands Decider 2B guardrails against prompt injection in web pages.

Two guardrails for a Strands agent that reads web pages and can act for its user:

- ``ChunkScanner`` checks every chunk of fetched text with Decider before the model sees it, and
  quarantines the chunks that carry instructions aimed at an AI.
- ``ToolCallGuard`` is a Strands ``InterventionHandler``: before a sensitive tool runs, it checks
  the call against what the user actually asked for, then proceeds, asks the user or denies.

Both ask a local Decider server (``DeciderClient``). How to use them: phish_guard/README.md.
"""

from phish_guard.contracts import Action, Chunk, Decision, LabeledChunk
from phish_guard.decider import DeciderClient, DeciderUnavailable
from phish_guard.deobfuscate import deobfuscate
from phish_guard.questions import CHUNK_QUESTIONS, TOOL_CALL_QUESTIONS
from phish_guard.scanner import QUARANTINE_NOTICE, ChunkScanner, ScanPolicy
from phish_guard.tool_guard import RecipientRule, ToolCallGuard, ToolCallPolicy

__all__ = [
    "CHUNK_QUESTIONS",
    "QUARANTINE_NOTICE",
    "TOOL_CALL_QUESTIONS",
    "Action",
    "Chunk",
    "ChunkScanner",
    "DeciderClient",
    "DeciderUnavailable",
    "Decision",
    "LabeledChunk",
    "RecipientRule",
    "ScanPolicy",
    "ToolCallGuard",
    "ToolCallPolicy",
    "deobfuscate",
]
