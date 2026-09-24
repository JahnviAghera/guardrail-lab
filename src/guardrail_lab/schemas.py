"""Data contracts shared by the pipeline, API, UI, store and evaluation."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

Category = Literal["SAFE", "AMBIGUOUS", "OFF_TOPIC", "PII", "UNSAFE", "PROMPT_INJECTION", "DISALLOWED"]
Action = Literal["ALLOW", "TRANSFORM", "REDACT_AND_ALLOW", "CLARIFY", "REDIRECT", "BLOCK", "BLOCK_WITH_ALTERNATIVE"]
SystemId = Literal["A", "B", "C"]
FaultKind = Literal["malformed_json", "schema_violation", "pii_leak", "canary_leak"]
CATEGORIES: tuple[str, ...] = Category.__args__  # type: ignore[attr-defined]


class AssistantResponse(BaseModel):
    """The contract every generated answer must satisfy."""

    answer: str = Field(min_length=1, max_length=6000)
    key_points: list[str] = Field(default_factory=list, max_length=6)
    task_type: Literal["explain", "code", "summarize", "write", "other"] = "other"
    confidence: Literal["low", "medium", "high"] = "medium"
    needs_clarification: bool = False
    clarifying_question: Optional[str] = None
    refused: bool = False
    refusal_reason: Optional[str] = None

    @model_validator(mode="after")
    def consistency(self) -> "AssistantResponse":
        if self.needs_clarification and not self.clarifying_question:
            raise ValueError("clarifying_question is required when needs_clarification is true")
        if self.refused and not self.refusal_reason:
            raise ValueError("refusal_reason is required when refused is true")
        return self


class Classification(BaseModel):
    """Output contract of the LLM-as-classifier stage."""

    category: Category
    dual_use: bool = False
    has_legitimate_task: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(default="", max_length=400)


class RewriteResult(BaseModel):
    rewritten_prompt: str = Field(min_length=1)


class ClarifyResult(BaseModel):
    clarifying_question: str = Field(min_length=1)


class AlternativeResult(BaseModel):
    message: str = Field(min_length=1)


class PIIEntity(BaseModel):
    type: str
    placeholder: str
    is_example: bool = False  # e.g. user@example.com in code samples


class RuleSignals(BaseModel):
    pii: list[PIIEntity] = Field(default_factory=list)
    injection_hits: list[str] = Field(default_factory=list)
    injection_confidence: float = 0.0
    disallowed_hits: list[str] = Field(default_factory=list)
    has_task_verb: bool = False

    @property
    def real_pii(self) -> list[PIIEntity]:
        return [e for e in self.pii if not e.is_example]


class Decision(BaseModel):
    category: Category
    action: Action
    severity: str
    reason: str
    secondary: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)  # detectors that raised the winning category


class RunOptions(BaseModel):
    constrained: bool = True          # JSON-schema constrained decoding for generation
    cascade: bool = True              # skip the LLM classifier when rules are decisive
    use_rules: bool = True
    use_llm_classifier: bool = True
    repair: bool = True
    reinsert_pii: bool = True         # put the user's own redacted values back into the final text (locally only)
    fault_injection: Optional[FaultKind] = None


class StageResult(BaseModel):
    stage: str
    status: Literal["pass", "flag", "block", "skip", "error", "repaired", "fail"]
    latency_ms: float = 0.0
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    scores: dict[str, float] = Field(default_factory=dict)
    details: dict = Field(default_factory=dict)


class PipelineTrace(BaseModel):
    request_id: str
    created_at: str
    system: SystemId
    options: RunOptions
    input_redacted: str
    pii_types: list[str] = Field(default_factory=list)
    classification: Optional[Classification] = None
    decision: Optional[Decision] = None
    transformed_prompt: Optional[str] = None
    stages: list[StageResult] = Field(default_factory=list)
    raw_output: Optional[str] = None
    final_response: Optional[AssistantResponse] = None
    final_text: str = ""
    # answered | transformed_answered | refused_by_model | clarified | redirected | blocked | blocked_output | fallback | rejected | error
    final_status: str = ""
    schema_valid_first: Optional[bool] = None
    schema_valid_final: Optional[bool] = None
    repaired: bool = False
    repair_attempts: int = 0
    canary_leaked: bool = False
    prompt_overlap: bool = False
    output_pii_found: bool = False
    pii_sent_to_llm: bool = False
    fault_injected: Optional[str] = None
    total_latency_ms: float = 0.0
    total_llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    est_cost_usd: float = 0.0


class RunRequest(BaseModel):
    prompt: str = Field(min_length=1)
    system: SystemId = "C"
    options: RunOptions = Field(default_factory=RunOptions)
