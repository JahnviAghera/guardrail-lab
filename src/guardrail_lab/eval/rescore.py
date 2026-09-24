"""Re-score stored eval traces against the current dataset labels/markers without re-running any model.

    python -m guardrail_lab.eval.rescore --batch main-20260924-230500
"""
import argparse
import json

from ..schemas import PipelineTrace
from ..settings import get_settings
from ..store import Store
from .run import load_dataset, score_item


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", required=True)
    ap.add_argument("--dataset", default="data/eval/prompts.jsonl")
    args = ap.parse_args()
    s = get_settings()
    store = Store(s.path(s.db_path))
    items = {i["id"]: i for i in load_dataset(s.path(args.dataset), "all")}
    rows = store.execute(
        "SELECT r.run_id, r.item_id, q.trace_json FROM eval_results r JOIN requests q USING (request_id)"
        " JOIN eval_runs e USING (run_id) WHERE e.label = ?", (args.batch,))
    for row in rows:
        trace = PipelineTrace.model_validate(json.loads(row["trace_json"]))
        scored = score_item(items[row["item_id"]], trace, judge=None)
        sets = ", ".join(f"{k} = ?" for k in scored if k != "judge_json")
        store.execute(f"UPDATE eval_results SET {sets} WHERE run_id = ? AND item_id = ?",
                      [v for k, v in scored.items() if k != "judge_json"] + [row["run_id"], row["item_id"]])
    print(f"rescored {len(rows)} rows in batch {args.batch}")


if __name__ == "__main__":
    main()
