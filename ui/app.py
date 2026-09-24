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

st.set_page_config(page_title="Guardrail Lab", page_icon="◆", layout="wide")
S = get_settings()

MAIN_STAGES = ["normalize", "rules", "prompt_guard", "classifier", "policy", "transform", "clarify", "safe_alternative",
               "generate", "validate", "repair", "output_checks"]
LABEL = {"normalize": "Normalise", "rules": "Rules", "prompt_guard": "PromptGuard", "classifier": "Classifier",
         "policy": "Policy", "transform": "Transform", "clarify": "Clarify", "safe_alternative": "Safe alt.",
         "generate": "Generate", "validate": "Validate", "repair": "Repair", "output_checks": "Output checks"}


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

APP_CSS = """
/* Hallmark · genre: modern-minimal · macrostructure: Workbench · theme: Cobalt · enrichment: none
 * tone: technical, instrument-panel · anchor hue: cobalt 256 · tokens: ui/tokens.css */
.stApp { background: var(--color-paper); color: var(--color-ink-2); font-family: var(--font-body); }
.stApp p, .stApp li, .stApp label, .stApp textarea, .stApp input { font-family: var(--font-body); }
.stApp h1, .stApp h2, .stApp h3, .stApp h4 { font-family: var(--font-display); color: var(--color-ink);
  letter-spacing: -0.02em; font-weight: 600; font-style: normal; }
.stApp code, .stApp pre { font-family: var(--font-mono) !important; }
[data-testid="stSidebar"] { background: var(--color-paper-2); border-right: 1px solid var(--color-rule); }
.stButton button { border-radius: var(--radius-control); font-family: var(--font-body); font-weight: 600; }
.stButton button[kind="primary"] { background: var(--color-accent); border-color: var(--color-accent); color: var(--color-accent-ink); }
.stButton button:focus-visible { outline: 2px solid var(--color-focus); outline-offset: 2px; }
.stTextArea textarea { border-radius: var(--radius-control); }

.gl-mast { display: flex; align-items: baseline; gap: var(--space-sm); flex-wrap: wrap; margin-bottom: var(--space-2xs); }
.gl-mast .word { font: 700 2.1rem/1.1 var(--font-display); color: var(--color-ink); letter-spacing: -0.03em; }
.gl-mast .tick { width: 0.7rem; height: 0.7rem; background: var(--color-accent); border-radius: 2px; align-self: center; }
.gl-label { font: 500 0.72rem/1.4 var(--font-mono); letter-spacing: 0.06em; text-transform: uppercase; color: var(--color-muted); }

.gl-stages { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2xs); margin: var(--space-sm) 0 var(--space-md); }
.gl-stage { display: inline-flex; align-items: center; gap: var(--space-2xs); padding: 0.4rem 0.7rem;
  border: 1px solid var(--color-rule-2); border-radius: var(--radius-control); background: var(--color-paper);
  font: 500 0.8rem/1 var(--font-mono); color: var(--color-ink); white-space: nowrap; }
.gl-stage .dot { width: 0.5rem; height: 0.5rem; border-radius: 50%; background: var(--color-skip); }
.gl-stage .ms { color: var(--color-muted); font-weight: 400; }
.gl-stage.pass .dot { background: var(--color-pass); }
.gl-stage.flag { border-color: var(--color-flag); background: var(--color-flag-soft); }
.gl-stage.flag .dot { background: var(--color-flag); }
.gl-stage.block, .gl-stage.fail, .gl-stage.error { border-color: var(--color-block); background: var(--color-block-soft); }
.gl-stage.block .dot, .gl-stage.fail .dot, .gl-stage.error .dot { background: var(--color-block); }
.gl-stage.repaired { border-color: var(--color-repair); background: var(--color-repair-soft); }
.gl-stage.repaired .dot { background: var(--color-repair); }
.gl-stage.skip { border-style: dashed; color: var(--color-muted); background: transparent; }
.gl-sep { color: var(--color-rule-2); font: 400 0.8rem var(--font-mono); }

.gl-kv { display: flex; flex-wrap: wrap; gap: var(--space-md); margin: var(--space-2xs) 0 var(--space-sm); }
.gl-kv .v { margin-top: var(--space-3xs); font: 600 1.05rem/1.2 var(--font-mono); color: var(--color-ink);
  padding: 0.3rem 0.6rem; border: 1px solid var(--color-rule-2); border-radius: var(--radius-control); display: inline-block; }
.gl-kv .v.act-proceed { border-color: var(--color-pass); color: var(--color-pass); }
.gl-kv .v.act-flag { border-color: var(--color-flag); color: var(--color-flag-ink); }
.gl-kv .v.act-stop { background: var(--color-block); border-color: var(--color-block); color: var(--color-accent-ink); }

.gl-checks { border-top: 1px solid var(--color-rule); margin-top: var(--space-2xs); }
.gl-check { display: grid; grid-template-columns: 1.1rem minmax(0, 1fr); gap: var(--space-2xs); padding: 0.55rem 0;
  border-bottom: 1px solid var(--color-rule); font: 500 0.92rem/1.35 var(--font-body); color: var(--color-ink); }
.gl-check .dot { width: 0.55rem; height: 0.55rem; border-radius: 50%; margin-top: 0.35rem; background: var(--color-skip); }
.gl-check.ok .dot { background: var(--color-pass); }
.gl-check.bad .dot { background: var(--color-block); }
.gl-check.fix .dot { background: var(--color-repair); }
.gl-check .detail { display: block; font: 400 0.78rem/1.35 var(--font-mono); color: var(--color-muted); overflow-wrap: anywhere; }

.gl-status { display: flex; align-items: center; gap: var(--space-xs); padding: var(--space-xs) var(--space-sm);
  border: 1px solid var(--color-rule-2); border-left-width: 4px; border-radius: var(--radius-control);
  background: var(--color-paper); margin: var(--space-md) 0 var(--space-xs); }
.gl-status .code { font: 600 0.8rem/1 var(--font-mono); letter-spacing: 0.06em; text-transform: uppercase; }
.gl-status .txt { font: 500 0.95rem/1.3 var(--font-body); color: var(--color-ink); }
.gl-status.ok { border-left-color: var(--color-pass); } .gl-status.ok .code { color: var(--color-pass); }
.gl-status.warn { border-left-color: var(--color-flag); } .gl-status.warn .code { color: var(--color-flag-ink); }
.gl-status.stop { border-left-color: var(--color-block); } .gl-status.stop .code { color: var(--color-block); }
.gl-status.info { border-left-color: var(--color-accent); } .gl-status.info .code { color: var(--color-accent); }

.gl-fault { font: 500 0.82rem/1.4 var(--font-mono); color: var(--color-repair); border: 1px dashed var(--color-repair);
  border-radius: var(--radius-control); padding: 0.45rem 0.7rem; display: inline-block; margin-bottom: var(--space-xs); }
.gl-stats { display: flex; flex-wrap: wrap; gap: var(--space-lg); padding: var(--space-sm) 0; border-top: 1px solid var(--color-rule);
  margin-top: var(--space-sm); }
.gl-stats .n { font: 600 1.35rem/1.1 var(--font-display); color: var(--color-ink); letter-spacing: -0.02em; }
"""


def inject_css() -> None:
    tokens = (ROOT / "ui" / "tokens.css").read_text()
    st.markdown(f"<style>{tokens}\n{APP_CSS}</style>", unsafe_allow_html=True)


# stage status → css class ; final status → (tone, code, text)
STATUS_LINE = {
    "answered": ("ok", "200 · answered", "Answered and validated"),
    "transformed_answered": ("warn", "200 · rewritten", "Answered after a safe rewrite and re-check"),
    "refused_by_model": ("warn", "model refusal", "The model refused on its own"),
    "clarified": ("info", "clarify", "Asked one clarifying question"),
    "redirected": ("info", "redirect", "Out of scope, redirected"),
    "blocked": ("stop", "⛔ blocked", "Blocked by policy"),
    "blocked_output": ("stop", "⛔ withheld", "Output withheld by output checks"),
    "fallback": ("stop", "fallback", "Output could not be repaired"),
    "rejected": ("stop", "rejected", "Rejected at input"),
    "error": ("stop", "error", "Model backend unavailable"),
}
ACTION_TONE = {"ALLOW": "act-proceed", "REDACT_AND_ALLOW": "act-proceed", "TRANSFORM": "act-flag", "CLARIFY": "act-flag",
               "REDIRECT": "act-flag", "BLOCK": "act-stop", "BLOCK_WITH_ALTERNATIVE": "act-stop"}


def chips(trace: dict) -> str:
    by_stage = {s["stage"]: s for s in trace["stages"]}
    order = [s for s in MAIN_STAGES if s in by_stage or s in ("classifier", "generate", "validate", "output_checks")]
    if trace["system"] == "A":
        order = ["normalize", "generate"]
    parts = []
    for name in order:
        s = by_stage.get(name)
        status = s["status"] if s else "skip"
        ms = f" <span class='ms'>{s['latency_ms'] / 1000:.1f}s</span>" if s and s["latency_ms"] >= 100 else ""
        parts.append(f"<span class='gl-stage {status}'><span class='dot'></span>{LABEL[name]}{ms}</span>")
        if name == "transform" and "policy (recheck)" in by_stage:
            rs = by_stage["policy (recheck)"]["status"]
            parts.append(f"<span class='gl-stage {rs}'><span class='dot'></span>Re-check</span>")
    return "<div class='gl-stages'>" + "<span class='gl-sep'>→</span>".join(parts) + "</div>"


def badges(d: dict) -> str:
    cell = "<div><div class='gl-label'>{k}</div><span class='v {cls}'>{v}</span></div>"
    return "<div class='gl-kv'>" + "".join([
        cell.format(k="Category", v=html.escape(d["category"]), cls=""),
        cell.format(k="Decision", v=html.escape(d["action"].replace("_", " ")), cls=ACTION_TONE.get(d["action"], "")),
        cell.format(k="Severity", v=html.escape(d["severity"]), cls=""),
    ]) + "</div>"


def check_line(ok: bool | None, label: str, detail: str = "", repaired: bool = False) -> str:
    cls = "fix" if repaired else ("" if ok is None else ("ok" if ok else "bad"))
    extra = f"<span class='detail'>{html.escape(detail)}</span>" if detail else ""
    return f"<div class='gl-check {cls}'><span class='dot'></span><span>{html.escape(label)}{extra}</span></div>"


def render_trace(trace: dict, compact: bool = False) -> None:
    by_stage = {s["stage"]: s for s in trace["stages"]}
    st.markdown(chips(trace), unsafe_allow_html=True)
    if trace.get("fault_injected"):
        st.markdown(f"<div class='gl-fault'>fault injected for demo · {html.escape(trace['fault_injected'])} · "
                    "applied to the generator's first output</div>", unsafe_allow_html=True)

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("#### Input analysis")
        d, c = trace.get("decision"), trace.get("classification")
        if trace["system"] == "A":
            st.caption("System A has no input guard; the prompt goes straight to the model.")
        elif d:
            rules = by_stage.get("rules", {}).get("details", {})
            inj = by_stage.get("rules", {}).get("scores", {}).get("injection_confidence", 0.0)
            st.markdown(badges(d), unsafe_allow_html=True)
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
                st.markdown(f"**PII redacted before any model call:** `{', '.join(trace['pii_types'])}`")
            if trace.get("transformed_prompt"):
                st.markdown("**Rewritten prompt** (then re-checked):")
                st.code(trace["transformed_prompt"], language=None, wrap_lines=True)
    with right:
        st.markdown("#### Output validation")
        if trace["system"] != "C":
            st.caption(f"System {trace['system']} has no output validation. Flags below are measured, not enforced.")
        v = by_stage.get("validate")
        rep = by_stage.get("repair")
        oc = by_stage.get("output_checks", {}).get("details", {})
        lines = [check_line(trace.get("schema_valid_first"), "Schema on first try",
                            "; ".join(v["details"].get("errors", [])[:2]) if v and v["status"] == "fail" else "")]
        if rep:
            lines.append(check_line(rep["status"] == "repaired", "Repair",
                                    f"{rep['details'].get('method', '')} · {rep['details'].get('attempts', 0)} LLM attempt(s)",
                                    repaired=rep["status"] == "repaired"))
        lines += [
            check_line(trace.get("schema_valid_final"), "Schema final"),
            check_line(None if trace["system"] != "C" and not trace.get("output_pii_found") else not trace.get("output_pii_found"),
                       "PII check", "found and redacted" if trace.get("output_pii_found") and trace["system"] == "C" else ""),
            check_line(not (trace.get("canary_leaked") or trace.get("prompt_overlap")), "Canary / prompt-leak check",
                       "system prompt leaked" if trace.get("canary_leaked") or trace.get("prompt_overlap") else ""),
            check_line(None if not oc else not oc.get("too_long"), "Length policy"),
        ]
        st.markdown("<div class='gl-checks'>" + "".join(lines) + "</div>", unsafe_allow_html=True)

    tone, code, text = STATUS_LINE.get(trace["final_status"], ("info", trace["final_status"], ""))
    st.markdown(f"<div class='gl-status {tone}'><span class='code'>{html.escape(code)}</span>"
                f"<span class='txt'>{html.escape(text)}</span></div>", unsafe_allow_html=True)
    st.markdown("#### Final response")
    with st.container(border=True):
        st.markdown(trace["final_text"] or "_(empty)_")

    stat = "<div><div class='gl-label'>{k}</div><div class='n'>{v}</div></div>"
    st.markdown("<div class='gl-stats'>" + "".join([
        stat.format(k="Total latency", v=f"{trace['total_latency_ms'] / 1000:.1f} s"),
        stat.format(k="LLM calls", v=trace["total_llm_calls"]),
        stat.format(k="Tokens in / out", v=f"{trace['tokens_in']} / {trace['tokens_out']}"),
        stat.format(k="Est. API cost", v=f"${trace['est_cost_usd']:.4f}"),
    ]) + "</div>", unsafe_allow_html=True)
    if not compact:
        timing = pd.DataFrame([{"stage": s["stage"], "seconds": s["latency_ms"] / 1000} for s in trace["stages"]])
        st.bar_chart(timing, x="stage", y="seconds", horizontal=True, height=40 + 26 * len(timing), sort=False, color="#0076ed")
        with st.expander("Raw model output"):
            st.code(trace.get("raw_output") or "(no generation)", language="json")
        with st.expander("Full trace JSON"):
            st.json(trace)


# ---------------------------------------------------------------------------------------------- layout

inject_css()
st.markdown("<div class='gl-mast'><span class='tick'></span><span class='word'>Guardrail Lab</span></div>"
            "<div class='gl-label'>detect → classify → transform / block → generate → validate → repair · "
            "StudyBuddy, a CS study assistant</div>", unsafe_allow_html=True)

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
