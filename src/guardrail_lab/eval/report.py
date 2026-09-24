"""Build a markdown report + figures for one eval batch.

    python -m guardrail_lab.eval.report                 # latest batch
    python -m guardrail_lab.eval.report --batch main-20260924-120000
    python -m guardrail_lab.eval.report --batch main-... --batch unconstrained-...   # combine batches
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import yaml

from ..settings import ROOT, get_settings
from ..store import Store
from . import metrics as M

STAGE_ORDER = ["normalize", "rules", "prompt_guard", "classifier", "policy", "transform", "clarify", "safe_alternative",
               "generate", "validate", "repair", "output_checks"]


def table(df, index=False) -> str:
    try:
        return df.to_markdown(index=index)
    except ImportError:  # tabulate not installed
        return "```\n" + df.to_string(index=index) + "\n```"


def plot_confusion(cm: np.ndarray, labels: list[str], title: str, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(6.4, 5.4))
    ax.imshow(cm, cmap="Blues")
    ax.set_xticks(range(len(labels)), labels, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(len(labels)), labels, fontsize=8)
    ax.set_xlabel("predicted")
    ax.set_ylabel("gold")
    for i in range(len(labels)):
        for j in range(len(labels)):
            if cm[i, j]:
                ax.text(j, i, cm[i, j], ha="center", va="center", color="white" if cm[i, j] > cm.max() / 2 else "black")
    ax.set_title(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_latency(lat, run_labels: dict[str, str], path: Path) -> None:
    lat = lat.copy()
    lat["stage"] = lat["stage"].str.replace(" (recheck)", "", regex=False)
    n_req = lat.groupby("run_id")["stage"].apply(lambda s: (s == "normalize").sum())
    per = lat.groupby(["run_id", "stage"])["latency_ms"].sum().unstack(fill_value=0).div(n_req, axis=0) / 1000
    stages = [s for s in STAGE_ORDER if s in per.columns]
    fig, ax = plt.subplots(figsize=(7, 3.2))
    left = np.zeros(len(per))
    for s in stages:
        if per[s].sum() == 0:
            continue
        ax.barh([run_labels[r] for r in per.index], per[s], left=left, label=s)
        left += per[s].to_numpy()
    ax.set_xlabel("mean seconds per request")
    ax.legend(fontsize=7, ncol=3, loc="lower right")
    ax.set_title("Where the time goes (mean per request, by stage)", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_frontier(df, run_labels: dict[str, str], path: Path) -> None:
    fig, ax = plt.subplots(figsize=(5, 4))
    for run_id, g in df.groupby("run_id", sort=False):
        legit = g[g["gold_action"].isin(["ALLOW", "REDACT_AND_ALLOW"])]
        x = legit["over_refusal"].dropna().mean()
        y = (1 - g["harmful_outcome"].dropna()).mean()
        ax.scatter(x, y, s=60)
        ax.annotate(run_labels[run_id], (x, y), textcoords="offset points", xytext=(6, 4), fontsize=8)
    ax.set_xlabel("over-refusal on legitimate requests  (lower is better)")
    ax.set_ylabel("harm recall  (higher is better)")
    ax.set_xlim(-0.05, 1.05)
    ax.set_ylim(-0.05, 1.05)
    ax.grid(alpha=0.3)
    ax.set_title("Safety vs helpfulness trade-off", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", action="append", help="batch label(s); default = latest")
    args = ap.parse_args()

    s = get_settings()
    store = Store(s.path(s.db_path))
    batches = args.batch or [store.execute("SELECT label FROM eval_runs ORDER BY started_at DESC LIMIT 1")[0]["label"]]
    q = ",".join("?" * len(batches))
    runs = store.execute(f"SELECT run_id, system, label, config_json, split FROM eval_runs WHERE label IN ({q}) ORDER BY started_at", batches)
    run_ids = [r["run_id"] for r in runs]
    run_labels = {r["run_id"]: f"{r['system']} ({r['label'].rsplit('-', 2)[0]})" for r in runs}

    df = M.load_results(store, run_ids)
    if df.empty:
        raise SystemExit("no results for that batch")
    prices_file = s.path(s.prices_path)
    prices = yaml.safe_load(prices_file.read_text()) if prices_file.exists() else {}

    out = ROOT / "results" / "_".join(batches)
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "results.csv", index=False)

    lines = [f"# Guardrail Lab evaluation: {', '.join(batches)}", "",
             f"Split: `{runs[0]['split']}` · items per run: {df.groupby('run_id').size().iloc[0]} · "
             "rates are shown as mean [95% bootstrap CI] (n = applicable items)", "",
             "## Table 1: System comparison", "", table(M.system_summary(df, prices)), ""]

    by_system = {r["system"]: r["run_id"] for r in runs}
    if "A" in by_system and "C" in by_system:
        lines += ["**McNemar (paired, same items)**", ""]
        for col in ("harmful_outcome", "over_refusal"):
            for other in ("B", "C"):
                if other in by_system:
                    res = M.mcnemar(df, by_system["A"], by_system[other], col)
                    lines.append(f"- `{col}` A vs {other}: {res}")
        lines.append("")

    lines += ["## Table 2: Input classifier (per guarded run)", ""]
    for r in runs:
        g = df[df["run_id"] == r["run_id"]]
        if g["pred_category"].notna().any():
            rep, cm, labels = M.classifier_report(g)
            img = f"confusion_{r['run_id']}.png"
            plot_confusion(cm, labels, f"Confusion matrix: {run_labels[r['run_id']]}", out / img)
            lines += [f"### {run_labels[r['run_id']]}", "", table(rep, index=True), "",
                      f"Action accuracy: {M.action_accuracy(g)}", "", f"![confusion]({img})", ""]

    lines += ["## Table 3: Structured output and repair", "", table(M.repair_table(df)), "",
              "## Table 4: Stage attribution (which layer stopped non-SAFE items)", ""]
    for r in runs:
        g = df[df["run_id"] == r["run_id"]]
        lines += [f"### {run_labels[r['run_id']]}", "", table(M.stage_attribution(g), index=True), ""]

    lat = M.load_stage_latency(store, run_ids)
    if not lat.empty:
        plot_latency(lat, run_labels, out / "latency.png")
        plot_frontier(df, run_labels, out / "frontier.png")
        lines += ["## Figures", "", "![latency](latency.png)", "", "![frontier](frontier.png)", ""]

    failures = df[(df["harmful_outcome"] == 1) | (df["over_refusal"] == 1)]
    lines += ["## Failure cases (harmful outcome or over-refusal)", "",
              table(failures[["system", "item_id", "gold_category", "pred_category", "final_status", "caught_by"]]), ""]

    (out / "report.md").write_text("\n".join(lines))
    print(f"wrote {out / 'report.md'}")


if __name__ == "__main__":
    main()
