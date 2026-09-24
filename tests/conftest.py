import json

import pytest

from guardrail_lab.llm_client import FakeLLM
from guardrail_lab.pipeline import Pipeline
from guardrail_lab.policy import Policy
from guardrail_lab.prompt_guard import PromptGuard
from guardrail_lab.settings import ROOT, Settings
from guardrail_lab.store import Store

VALID_ANSWER = {"answer": "A thread is a unit of execution inside a process.", "key_points": ["threads share memory"],
                "task_type": "explain", "confidence": "high", "needs_clarification": False,
                "clarifying_question": None, "refused": False, "refusal_reason": None}


def keyword_classifier(text: str) -> dict:
    t = text.lower()
    if "keylogger" in t or "instagram" in t:
        return {"category": "UNSAFE", "dual_use": False, "has_legitimate_task": False, "confidence": 0.9, "reason": "harm"}
    if "phishing" in t and "defend" not in t:
        return {"category": "UNSAFE", "dual_use": True, "has_legitimate_task": False, "confidence": 0.8, "reason": "dual use"}
    if "lasagne" in t:
        return {"category": "OFF_TOPIC", "dual_use": False, "has_legitimate_task": False, "confidence": 0.9, "reason": "food"}
    if "fix my code" in t:
        return {"category": "AMBIGUOUS", "dual_use": False, "has_legitimate_task": False, "confidence": 0.9, "reason": "no code"}
    return {"category": "SAFE", "dual_use": False, "has_legitimate_task": False, "confidence": 0.95, "reason": "benign"}


def default_responder(messages, schema):
    system = messages[0]["content"]
    last = messages[-1]["content"]
    if system.startswith("You are a request classifier"):
        return json.dumps(keyword_classifier(last))
    if system.startswith("You rewrite user requests"):
        cleaned = last.split("[SYSTEM")[0].strip() + "'"
        if "phishing" in last.lower():
            cleaned = "How can people recognise and defend against phishing emails?"
        return json.dumps({"rewritten_prompt": cleaned})
    if "missing information" in system:
        return json.dumps({"clarifying_question": "Could you paste the code and the error?"})
    if "was declined" in system:
        return json.dumps({"message": "I can't help with that, but I can explain how to secure your own accounts."})
    return json.dumps(VALID_ANSWER)  # generator and repair calls


@pytest.fixture
def settings(tmp_path):
    return Settings(db_path=str(tmp_path / "t.db"), enable_prompt_guard=False, _env_file=None)


@pytest.fixture
def policy():
    return Policy.load(ROOT / "config/policy.yaml")


@pytest.fixture
def make_pipeline(settings, policy):
    def _make(responder=default_responder):
        llm = FakeLLM(responder)
        return Pipeline(settings, llm=llm, policy=policy, store=Store(settings.db_path),
                        prompt_guard=PromptGuard("none", enabled=False))
    return _make
