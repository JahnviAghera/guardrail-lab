"""Regex PII / secret detection and reversible redaction.

Order matters: specific, high-precision patterns run first and are replaced by placeholders,
so broader patterns (phone numbers) can't re-match their digits.
"""
import re
from dataclasses import dataclass, field

from .schemas import PIIEntity

EXAMPLE_EMAIL_DOMAINS = re.compile(r"@(example\.(com|org|net)|test\.com|localhost)\b", re.I)


def _luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def _digit_count_between(lo: int, hi: int):
    return lambda m: lo <= sum(c.isdigit() for c in m) <= hi


# (type, pattern, extra check on the matched string)
PATTERNS: list[tuple[str, re.Pattern, object]] = [
    ("API_KEY", re.compile(r"\b(sk-[A-Za-z0-9_\-]{12,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|hf_[A-Za-z0-9]{20,}|xox[bp]-[A-Za-z0-9\-]{10,})"), None),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), None),
    ("CREDIT_CARD", re.compile(r"\b(?:\d[ \-]?){12,18}\d\b"), lambda m: _luhn_ok(re.sub(r"\D", "", m))),
    ("GOV_ID", re.compile(r"\b\d{3}-\d{2}-\d{4}\b|\b\d{4} \d{4} \d{4}\b"), None),  # US SSN, Aadhaar
    ("IP_ADDRESS", re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"), None),
    ("PHONE", re.compile(r"(?<![\w.])\+?\(?\d[\d \-()]{7,}\d\b"), _digit_count_between(10, 15)),
]


@dataclass
class Redaction:
    text: str
    entities: list[PIIEntity] = field(default_factory=list)
    mapping: dict[str, str] = field(default_factory=dict)  # placeholder -> original value

    @property
    def originals(self) -> list[str]:
        return list(self.mapping.values())


def redact(text: str) -> Redaction:
    entities: list[PIIEntity] = []
    mapping: dict[str, str] = {}
    counters: dict[str, int] = {}
    out = text
    for kind, pattern, check in PATTERNS:
        def _sub(m: re.Match) -> str:
            value = m.group(0)
            if check is not None and not check(value):
                return value
            is_example = kind == "EMAIL" and bool(EXAMPLE_EMAIL_DOMAINS.search(value))
            if is_example:
                entities.append(PIIEntity(type=kind, placeholder=value, is_example=True))
                return value
            counters[kind] = counters.get(kind, 0) + 1
            placeholder = f"<{kind}_{counters[kind]}>"
            mapping[placeholder] = value
            entities.append(PIIEntity(type=kind, placeholder=placeholder))
            return placeholder
        out = pattern.sub(_sub, out)
    return Redaction(text=out, entities=entities, mapping=mapping)


def reinsert(text: str, mapping: dict[str, str]) -> str:
    for placeholder, value in mapping.items():
        text = text.replace(placeholder, value)
    return text


def redact_obj(obj, skip_keys=frozenset({"request_id", "created_at"})):
    """Recursively redact every string in a JSON-like object (used before logging)."""
    if isinstance(obj, str):
        return redact(obj).text
    if isinstance(obj, list):
        return [redact_obj(v, skip_keys) for v in obj]
    if isinstance(obj, dict):
        return {k: (v if k in skip_keys else redact_obj(v, skip_keys)) for k, v in obj.items()}
    return obj
