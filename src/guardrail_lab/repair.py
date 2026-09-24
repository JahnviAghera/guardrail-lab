"""S7: repair invalid outputs: cheap local fixes first, then an LLM repair loop with the validator's errors."""
from __future__ import annotations

import re
from typing import Optional

import json_repair

FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def local_repair(raw: str) -> Optional[str]:
    """Deterministic fixes: strip markdown fences and chatter, then repair JSON syntax."""
    text = raw.strip()
    m = FENCE.search(text)
    if m:
        text = m.group(1).strip()
    start = text.find("{")
    if start > 0:
        text = text[start:]
    try:
        fixed = json_repair.repair_json(text, ensure_ascii=False)
    except Exception:
        return None
    return fixed if fixed and fixed not in ('""', "{}", "[]") and fixed != raw else None
