"""S1: cheap, deterministic rule signals. Rules may flag; they never declare a request SAFE."""
import re

from .normalize import NormalizedText
from .pii import redact
from .schemas import RuleSignals

I = re.I
# name -> (pattern, confidence)
INJECTION_PATTERNS: dict[str, tuple[re.Pattern, float]] = {
    "ignore_instructions": (re.compile(
        r"\b(ignore|disregard|forget|override|bypass)\s+(all\s+|any\s+)?(of\s+)?(the\s+|your\s+|my\s+|these\s+)?"
        r"(previous\s+|prior\s+|above\s+|earlier\s+|preceding\s+|system\s+|original\s+|safety\s+)?"
        r"(instructions?|rules|prompts?|directions|guidelines|restrictions|filters)\b", I), 0.95),
    "ignore_above": (re.compile(r"\b(ignore|disregard)\s+(the|everything)\s+(above|before)\b", I), 0.9),
    "reveal_hidden_prompt": (re.compile(
        r"\b(reveal|print|show|output|repeat|display|tell|give|leak|dump)\b[\w\s]{0,20}?"
        r"\b(system|hidden|initial|secret|original|internal|confidential|developer)\s+(prompt|instructions?|message|rules)\b", I), 0.95),
    "reveal_your_prompt": (re.compile(
        r"\b(reveal|print|show|output|repeat|display|tell me|give me)\s+(me\s+)?your\s+(prompt|instructions|rules)\b", I), 0.9),
    "persona_unrestricted": (re.compile(
        r"\b(you are now|from now on,? you are|pretend (to be|you('re| are))|act as|roleplay as)\b.{0,80}"
        r"\b(no|without|unrestricted|unfiltered|jailbroken|uncensored|devmode|developer mode)\b", I), 0.9),
    "jailbreak_terms": (re.compile(r"\b(developer mode|dev ?mode|do anything now|jailbreak(ed)?)\b", I), 0.85),
    "fake_system_tag": (re.compile(r"\[\s*(system|admin|developer)\s*[:\]]|<\s*/?\s*system\s*>|\bsystem override\b", I), 0.9),
    "classifier_attack": (re.compile(
        r"\b(note to (the )?(classifier|moderator|filter|guardrail)|classif(y|ied) (this|it) as safe|this (request|prompt) is safe)\b", I), 0.95),
    "safety_disabled": (re.compile(r"\b(safety|content)\s+(filters?|guardrails?|restrictions?)\s+(are\s+|is\s+|have been\s+)?(disabled|off|removed|turned off)\b", I), 0.9),
    "reply_only_with": (re.compile(r"\breply\s+(only\s+)?with\s+(only\s+)?the\s+word\b", I), 0.8),
    "multilingual_ignore": (re.compile(
        r"\bignora\s+(las|todas las)\s+instrucciones\b|\bignorez\s+(les|toutes les)\s+instructions\b|"
        r"\bignoriere\s+(alle\s+)?(vorherigen\s+)?anweisungen\b|忽略.{0,6}指令", I), 0.9),
}

DISALLOWED_PATTERNS: dict[str, re.Pattern] = {
    "chemical_weapon": re.compile(r"\b(nerve agent|sarin|tabun|vx gas|mustard gas|chemical weapon)\b", I),
    "biological_weapon": re.compile(r"\b(ricin|bioweapon|biological weapon|weaponi[sz]e\w* (a )?(virus|pathogen|anthrax))\b", I),
    "nuclear": re.compile(r"\b(weapons?[- ]grade (uranium|plutonium)|enrich\w* uranium|dirty bomb)\b", I),
    "explosives": re.compile(r"\b(build\w*|make|making|assemble)\s+(a\s+)?(pipe\s+)?(bomb|explosive device|ied)\b", I),
    "untraceable_weapon": re.compile(r"\b(untraceable (gun|firearm)|ghost gun|remove (the )?serial numbers?)\b", I),
    "mass_violence": re.compile(r"\b(maximum|mass) casualt", I),
    "minors_sexual": re.compile(r"\b(sexual|explicit|nude)\b.{0,40}\b(minor|child|\d{1,2}[- ]year[- ]old)\b|\bgroom\w*\s+(a\s+)?child", I),
}

TASK_VERB = re.compile(
    r"^\s*(please\s+)?(summari[sz]e|translate|explain|proofread|rewrite|paraphrase|review|analy[sz]e|fix|debug|correct)\b", I)


def scan(norm: NormalizedText) -> RuleSignals:
    views = norm.variants()
    hits, conf = [], 0.0
    for name, (pattern, weight) in INJECTION_PATTERNS.items():
        if any(pattern.search(v) for v in views):
            hits.append(name)
            conf = max(conf, weight)
    if norm.decoded_segments and hits:
        hits.append("hidden_in_base64")
    disallowed = [name for name, p in DISALLOWED_PATTERNS.items() if any(p.search(v) for v in views)]
    return RuleSignals(
        pii=redact(norm.text).entities,
        injection_hits=hits,
        injection_confidence=conf,
        disallowed_hits=disallowed,
        has_task_verb=bool(TASK_VERB.search(norm.text)) and len(norm.text) > 60,
    )
