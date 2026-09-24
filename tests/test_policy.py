from guardrail_lab.policy import decide
from guardrail_lab.schemas import Classification, PIIEntity, RuleSignals


def cls(cat, conf=0.9, **kw):
    return Classification(category=cat, confidence=conf, reason="r", **kw)


def test_no_signals_is_safe_allow(policy):
    d = decide(policy, RuleSignals(), None)
    assert (d.category, d.action) == ("SAFE", "ALLOW")


def test_precedence_injection_beats_pii(policy):
    rules = RuleSignals(pii=[PIIEntity(type="EMAIL", placeholder="<EMAIL_1>")], injection_hits=["ignore_instructions"],
                        injection_confidence=0.95)
    d = decide(policy, rules, cls("SAFE"))
    assert d.category == "PROMPT_INJECTION"
    assert "PII" in d.secondary and "SAFE" in d.secondary
    assert d.sources == ["rules"]


def test_disallowed_beats_everything(policy):
    d = decide(policy, RuleSignals(disallowed_hits=["explosives"], injection_hits=["x"]), cls("PROMPT_INJECTION"))
    assert (d.category, d.action) == ("DISALLOWED", "BLOCK")


def test_example_emails_are_not_pii(policy):
    rules = RuleSignals(pii=[PIIEntity(type="EMAIL", placeholder="a@example.com", is_example=True)])
    assert decide(policy, rules, cls("SAFE")).category == "SAFE"


def test_real_pii_redact_and_allow(policy):
    rules = RuleSignals(pii=[PIIEntity(type="PHONE", placeholder="<PHONE_1>")])
    assert decide(policy, rules, cls("SAFE")).action == "REDACT_AND_ALLOW"


def test_dual_use_unsafe_transforms(policy):
    assert decide(policy, RuleSignals(), cls("UNSAFE", dual_use=True)).action == "TRANSFORM"
    assert decide(policy, RuleSignals(), cls("UNSAFE")).action == "BLOCK_WITH_ALTERNATIVE"


def test_injection_with_legit_task_transforms_unless_disallowed(policy):
    c = cls("PROMPT_INJECTION", has_legitimate_task=True)
    assert decide(policy, RuleSignals(), c).action == "TRANSFORM"
    assert decide(policy, RuleSignals(), c, allow_transform=False).action == "BLOCK"


def test_cascade_without_classifier_uses_task_verb(policy):
    rules = RuleSignals(injection_hits=["fake_system_tag"], injection_confidence=0.9, has_task_verb=True)
    assert decide(policy, rules, None).action == "TRANSFORM"


def test_low_confidence_benign_becomes_ambiguous(policy):
    d = decide(policy, RuleSignals(), cls("SAFE", conf=0.3))
    assert (d.category, d.action) == ("AMBIGUOUS", "CLARIFY")


def test_low_confidence_risky_label_is_kept(policy):
    assert decide(policy, RuleSignals(), cls("UNSAFE", conf=0.3)).category == "UNSAFE"


def test_prompt_guard_threshold(policy):
    assert decide(policy, RuleSignals(), cls("SAFE"), prompt_guard_score=0.95).category == "PROMPT_INJECTION"
    assert decide(policy, RuleSignals(), cls("SAFE"), prompt_guard_score=0.5).category == "SAFE"


def test_off_topic_redirects(policy):
    assert decide(policy, RuleSignals(), cls("OFF_TOPIC")).action == "REDIRECT"
