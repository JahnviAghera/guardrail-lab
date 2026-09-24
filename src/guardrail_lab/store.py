"""SQLite persistence. Everything stored is PII-redacted; the raw prompt is kept only as a SHA-256 hash."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from .pii import redact, redact_obj
from .schemas import PipelineTrace

SCHEMA = """
CREATE TABLE IF NOT EXISTS eval_runs (
  run_id TEXT PRIMARY KEY, started_at TEXT, finished_at TEXT, system TEXT, label TEXT,
  config_json TEXT, dataset_path TEXT, dataset_sha256 TEXT, split TEXT
);
CREATE TABLE IF NOT EXISTS requests (
  request_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, system TEXT NOT NULL,
  prompt_sha256 TEXT NOT NULL, prompt_redacted TEXT NOT NULL,
  category TEXT, decision TEXT, final_status TEXT,
  total_latency_ms REAL, total_llm_calls INTEGER, tokens_in INTEGER, tokens_out INTEGER, est_cost_usd REAL,
  repaired INTEGER DEFAULT 0, fault_injected TEXT,
  eval_run_id TEXT REFERENCES eval_runs(run_id),
  trace_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stage_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL REFERENCES requests(request_id),
  seq INTEGER NOT NULL, stage TEXT NOT NULL, status TEXT NOT NULL, latency_ms REAL,
  llm_calls INTEGER, tokens_in INTEGER, tokens_out INTEGER, scores_json TEXT, details_json TEXT
);
CREATE TABLE IF NOT EXISTS eval_results (
  run_id TEXT REFERENCES eval_runs(run_id), item_id TEXT, request_id TEXT REFERENCES requests(request_id),
  gold_category TEXT, pred_category TEXT, gold_action TEXT, pred_action TEXT, final_status TEXT,
  subtype TEXT, benign_but_scary INTEGER,
  complied INTEGER, harmful_outcome INTEGER, over_refusal INTEGER, injection_success INTEGER,
  pii_sent_to_llm INTEGER, schema_valid_first INTEGER, schema_valid_final INTEGER, repaired INTEGER,
  caught_by TEXT, judge_json TEXT, latency_ms REAL, llm_calls INTEGER, tokens_in INTEGER, tokens_out INTEGER,
  PRIMARY KEY (run_id, item_id)
);
CREATE INDEX IF NOT EXISTS idx_stage_req ON stage_events(request_id);
CREATE INDEX IF NOT EXISTS idx_req_run ON requests(eval_run_id);
"""


class Store:
    def __init__(self, path: Path | str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)

    def execute(self, sql: str, params=()) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.fetchall()

    def save_trace(self, trace: PipelineTrace, prompt: Optional[str] = None, eval_run_id: Optional[str] = None) -> None:
        safe = redact_obj(trace.model_dump(mode="json"))
        digest = hashlib.sha256((prompt or trace.input_redacted).encode()).hexdigest()
        d = trace.decision
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (trace.request_id, trace.created_at, trace.system, digest, redact(trace.input_redacted).text,
                 d.category if d else None, d.action if d else None, trace.final_status,
                 trace.total_latency_ms, trace.total_llm_calls, trace.tokens_in, trace.tokens_out, trace.est_cost_usd,
                 int(trace.repaired), trace.fault_injected, eval_run_id, json.dumps(safe)))
            self._conn.executemany(
                "INSERT INTO stage_events (request_id, seq, stage, status, latency_ms, llm_calls, tokens_in, tokens_out,"
                " scores_json, details_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(trace.request_id, i, s["stage"], s["status"], s["latency_ms"], s["llm_calls"], s["tokens_in"],
                  s["tokens_out"], json.dumps(s["scores"]), json.dumps(s["details"]))
                 for i, s in enumerate(safe["stages"])])
            self._conn.commit()

    def get_trace(self, request_id: str) -> Optional[dict]:
        rows = self.execute("SELECT trace_json FROM requests WHERE request_id = ?", (request_id,))
        return json.loads(rows[0]["trace_json"]) if rows else None

    def list_requests(self, limit: int = 50, category: Optional[str] = None, include_eval: bool = False) -> list[dict]:
        sql = ("SELECT request_id, created_at, system, prompt_redacted, category, decision, final_status,"
               " total_latency_ms, total_llm_calls, repaired, fault_injected FROM requests WHERE 1=1")
        params: list = []
        if category:
            sql += " AND category = ?"
            params.append(category)
        if not include_eval:
            sql += " AND eval_run_id IS NULL"
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.execute(sql, params)]

    def stats(self) -> dict:
        rows = self.execute("SELECT system, category, decision, final_status, total_latency_ms FROM requests WHERE eval_run_id IS NULL")
        lat = sorted(r["total_latency_ms"] for r in rows if r["total_latency_ms"] is not None)
        pct = lambda q: lat[min(int(q * len(lat)), len(lat) - 1)] if lat else None
        count = lambda key: {k: sum(1 for r in rows if r[key] == k) for k in {r[key] for r in rows}}
        return {"requests": len(rows), "by_category": count("category"), "by_decision": count("decision"),
                "by_status": count("final_status"), "latency_ms_p50": pct(0.5), "latency_ms_p95": pct(0.95)}
