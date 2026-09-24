"""The guardrail pipeline: systems A (baseline), B (input guard) and C (full pipeline).

    S0 normalise → S1 rules → S2 Prompt Guard → S3 LLM classifier → S4 policy
      → [transform + re-check | clarify | redirect | block (+ safe alternative)]
      → S5 generate → S6 validate → S7 repair → S8 output checks → final
"""
from __future__ import annotations

import json
import logging
import secrets
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

import yaml

from . import prompts
from .faults import inject
from .llm_client import LLMClient, LLMError, LLMResult, make_client
from .normalize import NormalizedText, normalize
from .pii import Redaction, redact, reinsert
from .policy import Policy, decide
from .prompt_guard import PromptGuard
from .repair import local_repair
from .rules import scan
from .schemas import (AlternativeResult, AssistantResponse, Classification, ClarifyResult, Decision,
                      PipelineTrace, RewriteResult, RunOptions, StageResult)
from .settings import Settings, get_settings
from .validators import check_content, parse_response, response_text

log = logging.getLogger(__name__)
PROCEED = {"ALLOW", "REDACT_AND_ALLOW"}
ACTION_STATUS = {"ALLOW": "pass", "REDACT_AND_ALLOW": "pass", "TRANSFORM": "flag", "CLARIFY": "flag", "REDIRECT": "flag"}


class Pipeline:
    def __init__(self, settings: Optional[Settings] = None, llm: Optional[LLMClient] = None,
                 policy: Optional[Policy] = None, store=None, prompt_guard: Optional[PromptGuard] = None):
        self.s = settings or get_settings()
        self.llm = llm or make_client(self.s)
        self.policy = policy or Policy.load(self.s.path(self.s.policy_path))
        self.store = store
        self.prompt_guard = prompt_guard or PromptGuard(self.s.prompt_guard_model, self.s.enable_prompt_guard)
        prices_file = self.s.path(self.s.prices_path)
        self.prices = yaml.safe_load(prices_file.read_text()) if prices_file.exists() else {"models": {}}

    def run(self, prompt: str, system: str = "C", options: Optional[RunOptions] = None,
            eval_run_id: Optional[str] = None) -> PipelineTrace:
        trace = _Run(self, prompt, system, options or RunOptions()).execute()
        if self.store is not None:
            self.store.save_trace(trace, prompt=prompt, eval_run_id=eval_run_id)
        return trace

    def price(self, model: str) -> tuple[float, float]:
        models = self.prices.get("models", {})
        p = models.get(model, models.get("default", {}))
        return float(p.get("input_per_mtok", 0) or 0), float(p.get("output_per_mtok", 0) or 0)


class _Run:
    """State for one request. Every stage appends a StageResult to the trace."""

    def __init__(self, p: Pipeline, prompt: str, system: str, opts: RunOptions):
        self.p, self.prompt, self.system, self.opts = p, prompt, system, opts
        self.canary = "CANARY-" + secrets.token_hex(4).upper()
        self.system_prompt = prompts.GENERATOR_SYSTEM.format(canary=self.canary, fields=prompts.RESPONSE_FIELDS)
        self.sent: list[str] = []
        self.cost = 0.0
        self.redaction: Redaction = Redaction(text=prompt)
        self.trace = PipelineTrace(
            request_id=uuid.uuid4().hex, created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            system=system, options=opts, input_redacted="")

    # ---- helpers -------------------------------------------------------------------------------

    @contextmanager
    def stage(self, name: str):
        st = StageResult(stage=name, status="pass")
        t0 = time.perf_counter()
        try:
            yield st
        except LLMError as e:
            st.status = "error"
            st.details["error"] = str(e)
            raise
        finally:
            st.latency_ms = round((time.perf_counter() - t0) * 1000, 2)
            self.trace.stages.append(st)

    def call(self, st: StageResult, messages: list[dict], model: str, schema: Optional[dict] = None,
             max_tokens: Optional[int] = None) -> LLMResult:
        self.sent.extend(m["content"] for m in messages)
        r = self.p.llm.chat(messages, model, schema=schema, max_tokens=max_tokens or self.p.s.max_output_tokens)
        st.llm_calls += 1
        st.tokens_in += r.tokens_in
        st.tokens_out += r.tokens_out
        pin, pout = self.p.price(model)
        self.cost += (r.tokens_in * pin + r.tokens_out * pout) / 1e6
        return r

    def finish(self, response: Optional[AssistantResponse], text: str, status: str) -> None:
        self.trace.final_response = response
        self.trace.final_text = text
        self.trace.final_status = status

    def template(self, key: str, status: str, reason: str) -> None:
        text = self.p.policy.template(key)
        self.finish(AssistantResponse(answer=text, refused=True, refusal_reason=reason, confidence="high"), text, status)

    # ---- main flow ---------------------------------------------------------------------------------

    def execute(self) -> PipelineTrace:
        t0 = time.perf_counter()
        try:
            with self.stage("normalize") as st:
                norm = normalize(self.prompt)
                self.redaction = redact(norm.text)
                self.trace.input_redacted = self.redaction.text
                self.trace.pii_types = sorted({e.type for e in self.redaction.entities if not e.is_example})
                st.details = {"flags": norm.flags, "decoded_segments": norm.decoded_segments, "chars": len(self.prompt)}
                if len(self.prompt) > self.p.s.max_input_chars:
                    st.status = "block"
                    self.template("BLOCK", "rejected", f"Input longer than {self.p.s.max_input_chars} characters.")
                    return self.trace

            if self.system == "A":
                self.answer(self.prompt, status="answered")
            else:
                decision = self.input_guard(norm, self.redaction)
                self.trace.decision = decision
                self.route(decision, self.redaction.text)
        except LLMError as e:
            log.warning("LLM failure: %s", e)
            self.finish(None, "The language model backend is unavailable right now. Please try again.", "error")
        finally:
            tr = self.trace
            tr.total_latency_ms = round((time.perf_counter() - t0) * 1000, 2)
            tr.total_llm_calls = sum(s.llm_calls for s in tr.stages)
            tr.tokens_in = sum(s.tokens_in for s in tr.stages)
            tr.tokens_out = sum(s.tokens_out for s in tr.stages)
            tr.est_cost_usd = round(self.cost, 8)
            tr.pii_sent_to_llm = any(v in t for v in self.redaction.originals for t in self.sent)
        return self.trace

    # ---- input side -----------------------------------------------------------------------------------

    def input_guard(self, norm: NormalizedText, red: Redaction, allow_transform: bool = True, suffix: str = "") -> Decision:
        rules = None
        with self.stage("rules" + suffix) as st:
            if self.opts.use_rules:
                rules = scan(norm)
                flagged = rules.injection_hits or rules.disallowed_hits or rules.real_pii
                st.status = "flag" if flagged else "pass"
                st.scores = {"injection_confidence": rules.injection_confidence}
                st.details = {"injection_hits": rules.injection_hits, "disallowed_hits": rules.disallowed_hits,
                              "pii": [e.type for e in rules.real_pii], "has_task_verb": rules.has_task_verb}
            else:
                st.status = "skip"

        pg_score = None
        with self.stage("prompt_guard" + suffix) as st:
            if self.p.prompt_guard.available:
                views = [norm.text, *norm.decoded_segments]
                pg_score = max(self.p.prompt_guard.score(v) for v in views)
                st.scores = {"p_malicious": round(pg_score, 4)}
                st.status = "flag" if pg_score >= self.p.policy.threshold("prompt_guard_malicious") else "pass"
            else:
                st.status = "skip"
                st.details = {"reason": self.p.prompt_guard.error}

        cls = None
        with self.stage("classifier" + suffix) as st:
            decisive = rules is not None and (
                bool(rules.disallowed_hits)
                or rules.injection_confidence >= self.p.policy.threshold("cascade_skip_llm_if_rules_conf"))
            if not self.opts.use_llm_classifier:
                st.status = "skip"
                st.details = {"reason": "disabled"}
            elif self.opts.cascade and decisive:
                st.status = "skip"
                st.details = {"reason": "cascade: rules were decisive"}
            else:
                cls = self.classify(st, red.text, norm.decoded_segments)
                if cls is None:
                    # Fail closed: an unavailable classifier must not silently mean SAFE.
                    st.status = "error"
                    cls = Classification(category="AMBIGUOUS", confidence=0.0, reason="classifier output unusable")
                else:
                    st.status = "pass" if cls.category == "SAFE" else "flag"
                    st.scores = {"confidence": cls.confidence}
                    st.details = cls.model_dump()

        with self.stage("policy" + suffix) as st:
            d = decide(self.p.policy, rules, cls, pg_score, allow_transform=allow_transform)
            st.status = ACTION_STATUS.get(d.action, "block")
            st.details = d.model_dump()
        if not suffix:
            self.trace.classification = cls
        return d

    def classify(self, st: StageResult, text: str, decoded: list[str]) -> Optional[Classification]:
        extra = "".join(f"\n<DECODED_FROM_BASE64>\n{d}\n</DECODED_FROM_BASE64>" for d in decoded)
        messages = [{"role": "system", "content": prompts.CLASSIFIER_SYSTEM},
                    {"role": "user", "content": prompts.CLASSIFIER_USER.format(text=text, decoded=extra)}]
        for _ in range(2):
            r = self.call(st, messages, self.p.s.classifier_model, schema=Classification.model_json_schema(), max_tokens=250)
            cls = _lenient(r.content, Classification, clamp=("confidence", 0.0, 1.0))
            if cls is not None:
                return cls
            st.details["retry"] = r.content[:300]
        return None

    def route(self, d: Decision, text: str) -> None:
        if d.action in PROCEED:
            self.answer(text, status="answered")
        elif d.action == "TRANSFORM":
            self.transform(d, text)
        elif d.action == "CLARIFY":
            self.clarify(text)
        elif d.action == "REDIRECT":
            self.template("REDIRECT", "redirected", d.reason)
        elif d.action == "BLOCK_WITH_ALTERNATIVE":
            self.alternative(d, text)
        else:
            self.template("BLOCK_DISALLOWED" if d.category == "DISALLOWED" else "BLOCK", "blocked", d.reason)

    def transform(self, d: Decision, text: str) -> None:
        with self.stage("transform") as st:
            instruction = prompts.REWRITE_INSTRUCTIONS.get(d.category, prompts.REWRITE_INSTRUCTIONS["UNSAFE"])
            messages = [{"role": "system", "content": prompts.REWRITE_SYSTEM.format(instruction=instruction)},
                        {"role": "user", "content": text}]
            r = self.call(st, messages, self.p.s.classifier_model, schema=RewriteResult.model_json_schema(), max_tokens=700)
            rw = _lenient(r.content, RewriteResult)
            if rw is None:
                st.status = "fail"
                self.template("BLOCK", "blocked", "Could not safely rewrite the request.")
                return
            st.status = "flag"
            st.details = {"rewritten_prompt": rw.rewritten_prompt, "from_category": d.category}
        self.trace.transformed_prompt = rw.rewritten_prompt

        # A rewrite must pass the guard again, with no second transform allowed: rewriting can't launder a request.
        norm2 = normalize(rw.rewritten_prompt)
        red2 = redact(norm2.text)
        d2 = self.input_guard(norm2, red2, allow_transform=False, suffix=" (recheck)")
        if d2.action in PROCEED:
            self.answer(red2.text, status="transformed_answered")
        else:
            self.template("BLOCK", "blocked", f"Rewritten request still failed the policy: {d2.category}.")

    def clarify(self, text: str) -> None:
        with self.stage("clarify") as st:
            messages = [{"role": "system", "content": prompts.CLARIFY_SYSTEM}, {"role": "user", "content": text}]
            r = self.call(st, messages, self.p.s.generator_model, schema=ClarifyResult.model_json_schema(), max_tokens=120)
            res = _lenient(r.content, ClarifyResult)
            q = res.clarifying_question if res else self.p.policy.template("CLARIFY_DEFAULT")
            st.status = "pass" if res else "fail"
        resp = AssistantResponse(answer=q, needs_clarification=True, clarifying_question=q, confidence="low")
        self.finish(*self.output_checks(resp, q), "clarified")

    def alternative(self, d: Decision, text: str) -> None:
        with self.stage("safe_alternative") as st:
            messages = [{"role": "system", "content": prompts.ALTERNATIVE_SYSTEM.format(reason=d.reason)},
                        {"role": "user", "content": f"Declined request (do not fulfil): {text}"}]
            r = self.call(st, messages, self.p.s.generator_model, schema=AlternativeResult.model_json_schema(), max_tokens=250)
            res = _lenient(r.content, AlternativeResult)
            msg = res.message if res else self.p.policy.template("BLOCK")
            st.status = "pass" if res else "fail"
        resp = AssistantResponse(answer=msg, refused=True, refusal_reason=d.reason, confidence="high")
        self.finish(*self.output_checks(resp, msg), "blocked")

    # ---- generation and output side ------------------------------------------------------------------

    def answer(self, text: str, status: str) -> None:
        messages = [{"role": "system", "content": self.system_prompt}, {"role": "user", "content": text}]
        schema = AssistantResponse.model_json_schema() if self.opts.constrained else None
        with self.stage("generate") as st:
            raw = self.call(st, messages, self.p.s.generator_model, schema=schema).content
            if self.opts.fault_injection:
                raw = inject(raw, self.opts.fault_injection, self.canary)
                self.trace.fault_injected = self.opts.fault_injection
                st.details["fault_injected"] = self.opts.fault_injection
            st.details["constrained"] = self.opts.constrained
        self.trace.raw_output = raw

        first = parse_response(raw)
        resp = first.response
        self.trace.schema_valid_first = resp is not None

        if self.system != "C":
            # Unguarded: whatever came back goes to the user. Leak flags are recorded for measurement only.
            self.trace.schema_valid_final = resp is not None
            self.trace.canary_leaked = self.canary.lower() in raw.lower()
            shown = response_text(resp) if resp else raw
            cc = check_content(shown, self.canary, self.system_prompt, [], 10**9)
            self.trace.prompt_overlap = cc.prompt_overlap
            self.trace.output_pii_found = bool(cc.pii_types)
            self.finish(resp, shown, _status_from(resp, status))
            return

        with self.stage("validate") as st:
            st.status = "pass" if resp else "fail"
            st.details = {"errors": first.errors, "error_type": first.stage}
        if resp is None and self.opts.repair:
            resp = self.repair(raw, first.errors, messages, schema)
        self.trace.schema_valid_final = resp is not None
        if resp is None:
            self.template("FALLBACK", "fallback", "Output failed validation and could not be repaired.")
            return

        resp, text_out = self.output_checks(resp, response_text(resp))
        if self.trace.canary_leaked or self.trace.prompt_overlap:
            self.finish(resp, text_out, "blocked_output")
            return
        if self.opts.reinsert_pii:
            text_out = reinsert(text_out, self.redaction.mapping)
        self.finish(resp, text_out, _status_from(resp, status))

    def repair(self, raw: str, errors: list[str], messages: list[dict], schema: Optional[dict]) -> Optional[AssistantResponse]:
        with self.stage("repair") as st:
            fixed = local_repair(raw)
            if fixed:
                pr = parse_response(fixed)
                if pr.response:
                    st.status = "repaired"
                    st.details = {"method": "local", "attempts": 0}
                    self.trace.repaired = True
                    return pr.response
                errors = pr.errors
            current = fixed or raw
            for attempt in range(1, self.p.s.max_repair_attempts + 1):
                self.trace.repair_attempts = attempt
                fix_msgs = messages + [
                    {"role": "assistant", "content": current},
                    {"role": "user", "content": prompts.REPAIR_SYSTEM.format(
                        errors="\n".join(f"- {e}" for e in errors), fields=prompts.RESPONSE_FIELDS)}]
                out = self.call(st, fix_msgs, self.p.s.generator_model, schema=schema).content
                pr = parse_response(out)
                if pr.response is None and (lf := local_repair(out)):
                    pr = parse_response(lf)
                if pr.response:
                    st.status = "repaired"
                    st.details = {"method": "llm", "attempts": attempt, "initial_errors": errors}
                    self.trace.repaired = True
                    return pr.response
                errors, current = pr.errors, out
            st.status = "fail"
            st.details = {"method": "llm", "attempts": self.p.s.max_repair_attempts, "last_errors": errors}
            return None

    def output_checks(self, resp: AssistantResponse, text: str) -> tuple[AssistantResponse, str]:
        """Content policy on model-written text (system C only). Returns possibly replaced response/text."""
        if self.system != "C":
            return resp, text
        all_text = " ".join(str(v) for v in resp.model_dump().values() if isinstance(v, str)) + " " + text
        out_policy = self.p.policy.output
        with self.stage("output_checks") as st:
            cc = check_content(all_text, self.canary, self.system_prompt, [], 10**9)
            cc.too_long = len(text) > out_policy["max_answer_chars"]
            st.details = {"canary_leaked": cc.canary_leaked, "prompt_overlap": cc.prompt_overlap,
                          "pii_types": cc.pii_types, "too_long": cc.too_long}
            self.trace.canary_leaked |= cc.canary_leaked
            self.trace.prompt_overlap |= cc.prompt_overlap
            self.trace.output_pii_found |= bool(cc.pii_types)
            if cc.canary_leaked or cc.prompt_overlap:
                st.status = "block"
                msg = self.p.policy.template("OUTPUT_BLOCKED")
                return AssistantResponse(answer=msg, refused=True, refusal_reason="Output leaked internal configuration."), msg
            if cc.pii_types:
                st.status = "flag"
                resp = AssistantResponse.model_validate(_redact_fields(resp.model_dump()))
                text = redact(text).text
            if cc.too_long:
                st.status = "flag"
                text = text[: out_policy["max_answer_chars"]] + " …"
        return resp, text


def _status_from(resp: Optional[AssistantResponse], default: str) -> str:
    if resp is None:
        return default
    if resp.refused:
        return "refused_by_model"
    if resp.needs_clarification:
        return "clarified"
    return default


def _redact_fields(data):
    if isinstance(data, str):
        return redact(data).text
    if isinstance(data, list):
        return [_redact_fields(v) for v in data]
    if isinstance(data, dict):
        return {k: _redact_fields(v) for k, v in data.items()}
    return data


def _lenient(raw: str, model, clamp: Optional[tuple[str, float, float]] = None):
    """Parse helper outputs (classifier, rewrite...) leniently: they're internal, not user-facing contracts."""
    for candidate in (raw, local_repair(raw)):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
            if clamp and isinstance(data, dict) and isinstance(data.get(clamp[0]), (int, float)):
                data[clamp[0]] = min(max(float(data[clamp[0]]), clamp[1]), clamp[2])
            return model.model_validate(data)
        except Exception:
            continue
    return None
