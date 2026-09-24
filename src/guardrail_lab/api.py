"""FastAPI service. Run: uvicorn guardrail_lab.api:app --reload"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from fastapi import FastAPI, HTTPException

from .pipeline import Pipeline
from .schemas import PipelineTrace, RunRequest
from .settings import get_settings
from .store import Store

app = FastAPI(title="Guardrail Lab", version="0.1.0",
              description="Detect → Classify → Transform/Block → Generate → Validate → Repair")


@lru_cache
def pipeline() -> Pipeline:
    s = get_settings()
    return Pipeline(s, store=Store(s.path(s.db_path)))


@app.post("/v1/run", response_model=PipelineTrace)
def run(req: RunRequest) -> PipelineTrace:
    return pipeline().run(req.prompt, req.system, req.options)


@app.get("/v1/traces/{request_id}")
def get_trace(request_id: str) -> dict:
    trace = pipeline().store.get_trace(request_id)
    if trace is None:
        raise HTTPException(404, "trace not found")
    return trace


@app.get("/v1/traces")
def list_traces(limit: int = 50, category: Optional[str] = None) -> list[dict]:
    return pipeline().store.list_requests(limit=limit, category=category)


@app.get("/v1/policy")
def get_policy() -> dict:
    return pipeline().policy.raw


@app.get("/v1/stats")
def stats() -> dict:
    return pipeline().store.stats()


@app.get("/health")
def health() -> dict:
    p = pipeline()
    return {
        "llm_backend": p.s.llm_backend,
        "llm_reachable": p.llm.health(),
        "generator_model": p.s.generator_model,
        "classifier_model": p.s.classifier_model,
        "prompt_guard": "ready" if p.prompt_guard.available else p.prompt_guard.error,
    }
