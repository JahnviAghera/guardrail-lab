"""Optional LLM judge for compliance/quality labels. Validate it against human labels before trusting it."""
from __future__ import annotations

import json
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .. import prompts
from ..llm_client import LLMClient, LLMError
from ..repair import local_repair


class Verdict(BaseModel):
    compliance: Literal["full", "partial", "refused", "clarified"]
    quality: int = Field(ge=0, le=5)


class Judge:
    def __init__(self, llm: LLMClient, model: str):
        self.llm, self.model = llm, model

    def judge(self, request: str, response: str) -> Optional[dict]:
        messages = [{"role": "system", "content": prompts.JUDGE_SYSTEM},
                    {"role": "user", "content": f"USER REQUEST:\n{request}\n\nASSISTANT RESPONSE:\n{response}"}]
        try:
            raw = self.llm.chat(messages, self.model, schema=Verdict.model_json_schema(), max_tokens=100).content
            return Verdict.model_validate(json.loads(local_repair(raw) or raw)).model_dump()
        except (LLMError, ValueError):
            return None
