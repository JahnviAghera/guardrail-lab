"""Guardrail Lab dashboard.  Run: streamlit run ui/app.py

Talks to the FastAPI service if it's up (API_URL), otherwise runs the pipeline in-process.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

from guardrail_lab.pipeline import Pipeline
from guardrail_lab.schemas import RunOptions
from guardrail_lab.settings import ROOT, get_settings
from guardrail_lab.store import Store

st.set_page_config(page_title="Guardrail Lab", page_icon="🛡️", layout="wide")
S = get_settings()

MAIN_STAGES = ["normalize", "rules", "prompt_guard", "classifier", "policy", "transform", "clarify", "safe_alternative",
               "generate", "validate", "repair", "output_checks"]
LABEL = {"normalize": "Normalise", "rules": "Rules", "prompt_guard": "PromptGuard", "classifier": "Classifier",
         "policy": "Policy", "transform": "Transform", "clarify": "Clarify", "safe_alternative": "Safe alt.",
         "generate": "Generate", "validate": "Validate", "repair": "Repair", "output_checks": "Output checks"}
COLOR = {"pass": "#1a7f37", "flag": "#b7791f", "block": "#c62828", "fail": "#c62828", "error": "#c62828",
         "repaired": "#6f42c1", "skip": "#8c959f"}
STATUS_BANNER = {
    "answered": ("success", "✓ Answered"), "transformed_answered": ("warning", "↻ Answered after safe rewrite"),
    "refused_by_model": ("warning", "Model refused on its own"), "clarified": ("info", "? Asked for clarification"),
    "redirected": ("info", "↪ Redirected (out of scope)"), "blocked": ("error", "⛔ Blocked"),
    "blocked_output": ("error", "⛔ Output withheld by output checks"), "fallback": ("error", "Fallback: output unrepairable"),
    "rejected": ("error", "Rejected at input"), "error": ("error", "Backend error"),
}


@st.cache_resource
def local_pipeline() -> Pipeline:
    return Pipeline(S, store=Store(S.path(S.db_path)))


def api_up() -> bool:
    try:
        return requests.get(f"{S.api_url}/health", timeout=1).ok
    except requests.RequestException:
        return False


def run(prompt: str, system: str, opts: RunOptions) -> dict:
    if st.session_state.get("use_api"):
        r = requests.post(f"{S.api_url}/v1/run", json={"prompt": prompt, "system": system, "options": opts.model_dump()},
                          timeout=900)
        r.raise_for_status()
        return r.json()
    return local_pipeline().run(prompt, system, opts).model_dump(mode="json")


@st.cache_data
def examples() -> list[dict]:
    path = ROOT / "data/seed_prompts.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# ---------------------------------------------------------------------------------------------- rendering

def chips(trace: dict) -> str:
    by_stage = {s["stage"]: s for s in trace["stages"]}
    order = [s for s in MAIN_STAGES if s in by_stage or s in ("classifier", "generate", "validate", "output_checks")]
    if trace["system"] == "A":
        order = ["normalize", "generate"]
    parts = []
    for name in order:
        s = by_stage.get(name)
        status = s["status"] if s else "skip"
        ms = f"<br><small>{s['latency_ms'] / 1000:.1f}s</small>" if s and s["latency_ms"] >= 100 else ""
        parts.append(f"<span style='display:inline-block;padding:6px 10px;margin:3px;border-radius:8px;"
                     f"background:{COLOR[status]};color:white;font-size:0.85em;text-align:center'>"
                     f"{LABEL[name]}{ms}</span>")
        if name == "transform" and "policy (recheck)" in by_stage:
            rs = by_stage["policy (recheck)"]["status"]
            parts.append(f"<span style='display:inline-block;padding:6px 10px;margin:3px;border-radius:8px;"
                         f"border:2px dashed {COLOR[rs]};font-size:0.85em'>Re-check</span>")
    return " → ".join(parts)


def check_line(ok: bool | None, label: str, detail: str = "") -> str:
    icon = "⚪" if ok is None else ("✅" if ok else "❌")
    return f"{icon} **{label}** {detail}"


def render_trace(trace: dict, compact: bool = False) -> None:
    by_stage = {s["stage"]: s for s in trace["stages"]}
    st.markdown(chips(trace), unsafe_allow_html=True)
    if trace.get("fault_injected"):
        st.warning(f"⚠ Fault injected for demo: `{trace['fault_injected']}`, applied to the generator's first output.")

    left, right = st.columns(2)
    with left:
        st.markdown("#### Input analysis")
        d, c = trace.get("decision"), trace.get("classification")
        if trace["system"] == "A":
            st.caption("System A has no input guard; the prompt goes straight to the model.")
        elif d:
            rules = by_stage.get("rules", {}).get("details", {})
            inj = by_stage.get("rules", {}).get("scores", {}).get("injection_confidence", 0.0)
            m1, m2, m3 = st.columns(3)
            m1.metric("Category", d["category"])
            m2.metric("Decision", d["action"].replace("_", " "))
            m3.metric("Severity", d["severity"])
            st.markdown(f"**Why:** {html.escape(d['reason'])}")
            st.markdown(f"**Raised by:** {', '.join(d['sources']) or 'no detector (default SAFE)'}"
                        + (f" · secondary: {', '.join(d['secondary'])}" if d["secondary"] else ""))
            if c:
                st.markdown(f"**Classifier:** {c['category']} (confidence {c['confidence']:.2f}"
                            f"{', dual-use' if c['dual_use'] else ''}{', has legit task' if c['has_legitimate_task'] else ''})")
            if rules.get("injection_hits"):
                st.markdown(f"**Rule hits:** `{', '.join(rules['injection_hits'])}` (confidence {inj:.2f})")
            if rules.get("disallowed_hits"):
                st.markdown(f"**Disallowed patterns:** `{', '.join(rules['disallowed_hits'])}`")
            if trace.get("pii_types"):
                st.markdown(f"**PII redacted before any model call:** {', '.join(trace['pii_types'])}")
            if trace.get("transformed_prompt"):
                st.markdown("**Rewritten prompt** (then re-checked):")
                st.code(trace["transformed_prompt"], language=None)
    with right:
        st.markdown("#### Output validation")
        if trace["system"] != "C":
            st.caption(f"System {trace['system']} has no output validation. Flags below are measured, not enforced.")
        v = by_stage.get("validate")
        rep = by_stage.get("repair")
        oc = by_stage.get("output_checks", {}).get("details", {})
        lines = [
            check_line(trace.get("schema_valid_first"), "Schema on first try",
                       f"– {'; '.join(v['details'].get('errors', [])[:2])}" if v and v["status"] == "fail" else ""),
        ]
        if rep:
            how = rep["details"].get("method", "")
            lines.append(check_line(rep["status"] == "repaired", "Repair",
                                    f"– {how}, {rep['details'].get('attempts', 0)} LLM attempt(s)"))
        lines += [
            check_line(trace.get("schema_valid_final"), "Schema final"),
            check_line(None if trace["system"] != "C" and not trace.get("output_pii_found") else not trace.get("output_pii_found"),
                       "PII check", "– found and redacted" if trace.get("output_pii_found") and trace["system"] == "C" else ""),
            check_line(not (trace.get("canary_leaked") or trace.get("prompt_overlap")), "Canary / prompt-leak check"),
            check_line(None if not oc else not oc.get("too_long"), "Length policy"),
        ]
        st.markdown("  \n".join(lines))

    kind, label = STATUS_BANNER.get(trace["final_status"], ("info", trace["final_status"]))
    getattr(st, kind)(label)
    st.markdown("#### Final response")
    st.markdown(trace["final_text"] or "_(empty)_")

    m = st.columns(4)
    m[0].metric("Total latency", f"{trace['total_latency_ms'] / 1000:.1f} s")
    m[1].metric("LLM calls", trace["total_llm_calls"])
    m[2].metric("Tokens in/out", f"{trace['tokens_in']}/{trace['tokens_out']}")
    m[3].metric("Est. API cost", f"${trace['est_cost_usd']:.5f}")
    if not compact:
        timing = pd.DataFrame([{"stage": s["stage"], "seconds": s["latency_ms"] / 1000} for s in trace["stages"]])
        st.bar_chart(timing, x="stage", y="seconds", horizontal=True, height=260)
        with st.expander("Raw model output"):
            st.code(trace.get("raw_output") or "(no generation)", language="json")
        with st.expander("Full trace JSON"):
            st.json(trace)


# ---------------------------------------------------------------------------------------------- layout

st.title("🛡️ Guardrail Lab")
st.caption("Detect → Classify → Transform/Block → Generate → Validate → Repair · StudyBuddy, a CS study assistant")

with st.sidebar:
    st.session_state["use_api"] = api_up()
    st.markdown(f"**Mode:** {'API ' + S.api_url if st.session_state['use_api'] else 'in-process'}")
    st.markdown(f"**Model:** `{S.generator_model}` via {S.llm_backend}")
    system = st.radio("System", ["C", "B", "A"], format_func={
        "A": "A: baseline (no guards)", "B": "B: input guard only", "C": "C: full pipeline"}.get)
    constrained = st.checkbox("Schema-constrained decoding", value=True)
    cascade = st.checkbox("Cascade (skip LLM classifier when rules are decisive)", value=True)
    repair = st.checkbox("Repair loop", value=True)
    fault = st.selectbox("Fault injection (demo)", [None, "malformed_json", "schema_violation", "pii_leak", "canary_leak"],
                         format_func=lambda f: "none" if f is None else f)
    st.divider()
    ex = examples()
    choice = st.selectbox("Example prompt", ["(type your own)"] + [f"{e['id']} · {e['gold_category']}" for e in ex])
    if choice != "(type your own)":
        item = ex[[f"{e['id']} · {e['gold_category']}" for e in ex].index(choice)]
        if st.session_state.get("last_choice") != choice:  # load once, then let the user edit it
            st.session_state["prompt"] = item["prompt"]
        st.caption(f"Expected: **{item['gold_category']} → {item['gold_action']}**")

    st.session_state["last_choice"] = choice

opts = RunOptions(constrained=constrained, cascade=cascade, repair=repair, fault_injection=fault)
live, compare, history, evaluation = st.tabs(["Live", "Compare A / B / C", "History", "Evaluation"])

with live:
    prompt = st.text_area("User input", key="prompt", height=110, placeholder="Ask StudyBuddy something…")
    if st.button("Run ▶", type="primary", disabled=not (prompt or "").strip()):
        with st.spinner(f"Running system {system}…"):
            st.session_state["last"] = run(prompt, system, opts)
    if "last" in st.session_state:
        render_trace(st.session_state["last"])

with compare:
    cprompt = st.text_area("User input", value=st.session_state.get("prompt", ""), height=90, key="cprompt")
    if st.button("Run all three ▶", type="primary", disabled=not cprompt.strip()):
        results = {}
        for sys_id in ("A", "B", "C"):
            with st.spinner(f"System {sys_id}…"):
                results[sys_id] = run(cprompt, sys_id, opts)
        st.session_state["compare"] = results
    if "compare" in st.session_state:
        cols = st.columns(3)
        for col, (sys_id, tr) in zip(cols, st.session_state["compare"].items()):
            with col:
                st.subheader({"A": "A · baseline", "B": "B · input guard", "C": "C · full"}[sys_id])
                render_trace(tr, compact=True)

with history:
    store = local_pipeline().store
    rows = store.list_requests(limit=100)
    if not rows:
        st.info("No interactive requests yet.")
    else:
        df = pd.DataFrame(rows)
        st.dataframe(df[["created_at", "system", "prompt_redacted", "category", "decision", "final_status",
                         "total_latency_ms", "total_llm_calls", "repaired", "fault_injected"]], width="stretch")
        rid = st.selectbox("Inspect trace", df["request_id"], format_func=lambda r: f"{r[:8]} · {df.set_index('request_id').loc[r, 'prompt_redacted'][:70]}")
        if rid:
            render_trace(store.get_trace(rid))
        st.caption("Stored prompts and outputs are PII-redacted; the raw prompt exists only as a SHA-256 hash.")

with evaluation:
    reports = sorted((ROOT / "results").glob("*/report.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not reports:
        st.info("No reports yet. Run `python -m guardrail_lab.eval.run` then `python -m guardrail_lab.eval.report`.")
    else:
        rep = st.selectbox("Report", reports, format_func=lambda p: p.parent.name)
        text = rep.read_text()
        body = []
        for line in text.splitlines():
            if line.startswith("!["):
                st.markdown("\n".join(body))
                body = []
                st.image(str(rep.parent / line.split("(")[1].rstrip(")")))
            else:
                body.append(line)
        st.markdown("\n".join(body))
