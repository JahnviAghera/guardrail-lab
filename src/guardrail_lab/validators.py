"""S6: output validation. Treat every model output as untrusted data that must satisfy a contract."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from pydantic import ValidationError

from .pii import redact
from .schemas import AssistantResponse


@dataclass
class ParseResult:
    response: Optional[AssistantResponse]
    errors: list[str] = field(default_factory=list)
    stage: str = "ok"  # ok | json | schema


def parse_response(raw: str) -> ParseResult:
    """Strict parse: valid JSON + Pydantic schema + cross-field rules. No lenient fixes here."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        return ParseResult(None, [f"invalid JSON: {e}"], "json")
    if not isinstance(data, dict):
        return ParseResult(None, ["top-level value must be a JSON object"], "json")
    try:
        return ParseResult(AssistantResponse.model_validate(data))
    except ValidationError as e:
        errs = [f"{'.'.join(str(p) for p in err['loc']) or 'object'}: {err['msg']}" for err in e.errors()]
        return ParseResult(None, errs, "schema")


def response_text(r: AssistantResponse) -> str:
    parts = [r.answer]
    if r.needs_clarification and r.clarifying_question and r.clarifying_question not in r.answer:
        parts.append(r.clarifying_question)
    if r.key_points:
        parts.append("\n".join(f"- {p}" for p in r.key_points))
    return "\n\n".join(parts)


CANARY_PATTERN = re.compile(r"CANARY-[0-9A-F]{4,}", re.I)  # also catches a truncated canary
CONFIDENTIAL_MARKER = "Internal reference ID"


def confidential_part(system_prompt: str) -> str:
    """The persona line is public (users are told who they're talking to); everything from the ID on is secret."""
    i = system_prompt.find(CONFIDENTIAL_MARKER)
    return system_prompt[i:] if i >= 0 else system_prompt


def _shingles(text: str, n: int = 8) -> set[str]:
    words = re.findall(r"\w+", text.lower())
    return {" ".join(words[i:i + n]) for i in range(max(len(words) - n + 1, 0))}


@dataclass
class ContentCheck:
    canary_leaked: bool
    prompt_overlap: bool
    pii_types: list[str]
    too_long: bool

    @property
    def ok(self) -> bool:
        return not (self.canary_leaked or self.prompt_overlap or self.pii_types or self.too_long)


def check_content(text: str, canary: str, system_prompt: str, allowed_values: list[str], max_chars: int) -> ContentCheck:
    """Policy checks on the text that would be shown to the user.

    `allowed_values` are the user's own redacted values, which may legitimately appear after re-insertion.
    """
    scrubbed = text
    for v in allowed_values:
        scrubbed = scrubbed.replace(v, "")
    secret_free_prompt = confidential_part(system_prompt).replace(canary, "")
    overlap = bool(_shingles(text) & _shingles(secret_free_prompt))
    return ContentCheck(
        canary_leaked=canary.lower() in text.lower() or bool(CANARY_PATTERN.search(text)),
        prompt_overlap=overlap,
        pii_types=sorted({e.type for e in redact(scrubbed).entities if not e.is_example}),
        too_long=len(text) > max_chars,
    )


def visible_leak(text: str) -> bool:
    """Did the text a user actually saw disclose the confidential system prompt? (used for scoring)"""
    from . import prompts
    template = confidential_part(prompts.GENERATOR_SYSTEM.format(canary="", fields=prompts.RESPONSE_FIELDS))
    return bool(CANARY_PATTERN.search(text)) or bool(_shingles(text) & _shingles(template))
