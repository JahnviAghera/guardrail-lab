"""Quick terminal runner: python -m guardrail_lab.cli "your prompt" --system C [--fault malformed_json]"""
import argparse
import json

from .pipeline import Pipeline
from .schemas import RunOptions
from .settings import get_settings
from .store import Store

ICON = {"pass": "✓", "flag": "!", "block": "✗", "skip": "–", "error": "E", "repaired": "↻", "fail": "✗"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt")
    ap.add_argument("--system", choices=["A", "B", "C"], default="C")
    ap.add_argument("--fault", choices=["malformed_json", "schema_violation", "pii_leak", "canary_leak"])
    ap.add_argument("--unconstrained", action="store_true")
    ap.add_argument("--json", action="store_true", help="print the full trace as JSON")
    args = ap.parse_args()

    s = get_settings()
    p = Pipeline(s, store=Store(s.path(s.db_path)))
    t = p.run(args.prompt, args.system, RunOptions(fault_injection=args.fault, constrained=not args.unconstrained))
    if args.json:
        print(json.dumps(t.model_dump(mode="json"), indent=2))
        return
    print(f"system {t.system}  request {t.request_id}")
    for st in t.stages:
        extra = ", ".join(f"{k}={v}" for k, v in st.scores.items())
        print(f"  {ICON.get(st.status, '?')} {st.stage:<24} {st.status:<9} {st.latency_ms:>8.0f} ms  {extra}")
    if t.decision:
        print(f"decision: {t.decision.category} → {t.decision.action}  ({t.decision.reason})")
    if t.transformed_prompt:
        print(f"rewritten: {t.transformed_prompt}")
    print(f"status: {t.final_status}  schema valid first/final: {t.schema_valid_first}/{t.schema_valid_final}"
          f"  repaired: {t.repaired}  calls: {t.total_llm_calls}  total: {t.total_latency_ms:.0f} ms")
    print("\n" + t.final_text)


if __name__ == "__main__":
    main()
