"""S4: the policy engine. `decide` is a pure function of detector signals and the YAML policy."""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from .schemas import Classification, Decision, RuleSignals

BENIGN_SIDE = {"SAFE", "AMBIGUOUS", "OFF_TOPIC"}


@dataclass(frozen=True)
class Policy:
    raw: dict

    @classmethod
    def load(cls, path: Path) -> "Policy":
        return cls(yaml.safe_load(Path(path).read_text()))

    @property
    def precedence(self) -> list[str]:
        return self.raw["precedence"]

    def threshold(self, name: str) -> float:
        return float(self.raw["thresholds"][name])

    def category(self, name: str) -> dict:
        return self.raw["categories"][name]

    def template(self, name: str) -> str:
        return self.raw["templates"][name]

    @property
    def output(self) -> dict:
        return self.raw["output_policy"]


def decide(
    policy: Policy,
    rules: Optional[RuleSignals],
    classification: Optional[Classification],
    prompt_guard_score: Optional[float] = None,
    allow_transform: bool = True,
) -> Decision:
    # category -> list of detectors that raised it
    raised: dict[str, list[str]] = {}

    def add(cat: str, source: str) -> None:
        raised.setdefault(cat, []).append(source)

    if rules is not None:
        if rules.disallowed_hits:
            add("DISALLOWED", "rules")
        if rules.injection_hits:
            add("PROMPT_INJECTION", "rules")
        if rules.real_pii:
            add("PII", "rules")

    if prompt_guard_score is not None and prompt_guard_score >= policy.threshold("prompt_guard_malicious"):
        add("PROMPT_INJECTION", "prompt_guard")

    if classification is not None:
        cat = classification.category
        if cat in BENIGN_SIDE and cat != "AMBIGUOUS" and classification.confidence < policy.threshold("llm_classifier_min_conf"):
            add("AMBIGUOUS", "classifier_low_confidence")
        else:
            # Risky labels are kept even at low confidence: fail towards safety.
            add(cat, "classifier")

    order = policy.precedence
    ranked = sorted(raised, key=order.index)
    primary = ranked[0] if ranked else "SAFE"
    cfg = policy.category(primary)
    action = cfg["action"]

    # Transform instead of block when the policy allows it and a signal says there's a legitimate need.
    transform_if = cfg.get("transform_if")
    if allow_transform and transform_if:
        if transform_if == "dual_use" and classification is not None and classification.dual_use:
            action = "TRANSFORM"
        elif transform_if == "has_legitimate_task":
            legit = classification.has_legitimate_task if classification is not None else bool(rules and rules.has_task_verb)
            if legit:
                action = "TRANSFORM"

    reason = cfg["reason"]
    if classification is not None and classification.category == primary and classification.reason:
        reason = f"{reason} Classifier: {classification.reason}"
    if primary == "PROMPT_INJECTION" and rules is not None and rules.injection_hits:
        reason = f"{reason} Matched: {', '.join(rules.injection_hits)}."
    if primary == "DISALLOWED" and rules is not None and rules.disallowed_hits:
        reason = f"{reason} Matched: {', '.join(rules.disallowed_hits)}."

    return Decision(
        category=primary,
        action=action,
        severity=cfg["severity"],
        reason=reason,
        secondary=ranked[1:],
        sources=raised.get(primary, []),
    )
