"""S2: optional Llama Prompt Guard 2 injection/jailbreak classifier (lazy-loaded).

Requires `pip install -e '.[promptguard]'`, accepting the model licence on Hugging Face, and HF_TOKEN.
If anything is missing, `available` is False and the pipeline marks the stage as skipped.
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)


class PromptGuard:
    def __init__(self, model_id: str, enabled: bool):
        self.model_id = model_id
        self.enabled = enabled
        self._pipe = None
        self._tried = False
        self.error: Optional[str] = None if enabled else "disabled (ENABLE_PROMPT_GUARD=false)"

    @property
    def available(self) -> bool:
        if self.enabled and self._pipe is None and not self._tried:
            self._tried = True
            self._load()
        return self._pipe is not None

    def _load(self) -> None:
        try:
            from transformers import pipeline  # heavy import, only when enabled
            self._pipe = pipeline("text-classification", model=self.model_id, top_k=None, truncation=True)
            self.error = None
        except Exception as e:  # missing package, gated model, no network...
            self.error = f"unavailable: {e}"
            log.warning("Prompt Guard %s", self.error)

    def score(self, text: str) -> float:
        """Probability that `text` is malicious (injection/jailbreak)."""
        scores = self._pipe(text)
        if scores and isinstance(scores[0], list):  # shape differs across transformers versions
            scores = scores[0]
        # Prompt Guard 2 labels: LABEL_0 = benign, LABEL_1 = malicious
        for s in scores:
            if s["label"] in ("LABEL_1", "MALICIOUS", "malicious"):
                return float(s["score"])
        benign = next((s["score"] for s in scores if s["label"] in ("LABEL_0", "BENIGN", "benign")), 1.0)
        return 1.0 - float(benign)
