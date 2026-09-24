"""Deliberate, labelled fault injection so the validator/repair path can be demonstrated on demand.

Faults corrupt the generator's *first* raw output only. They are never used when measuring natural failure rates.
"""
import json


def inject(raw: str, kind: str, canary: str) -> str:
    if kind == "malformed_json":
        cut = raw[: max(int(len(raw) * 0.6), 20)]
        return "Sure! Here is the JSON you asked for:\n```json\n" + cut
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError
    except ValueError:
        data = {"answer": raw}
    if kind == "schema_violation":
        data.pop("answer", None)
        data["needs_clarification"] = True
        data["clarifying_question"] = None
        data["task_type"] = "essay"
    elif kind == "pii_leak":
        data["answer"] = str(data.get("answer", "")) + " For help, contact jane.doe@university.edu or +1 415 555 0132."
    elif kind == "canary_leak":
        data["answer"] = str(data.get("answer", "")) + f" (Internal reference ID: {canary})"
    return json.dumps(data)
