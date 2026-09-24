"""One thin client interface over Ollama (native API) or any OpenAI-compatible API."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

import httpx

from .settings import Settings

THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.S)


@dataclass
class LLMResult:
    content: str
    tokens_in: int
    tokens_out: int
    latency_ms: float
    model: str


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    def chat(self, messages: list[dict], model: str, schema: Optional[dict] = None,
             max_tokens: int = 900) -> LLMResult: ...


class OllamaClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self._http = httpx.Client(base_url=settings.llm_base_url, timeout=settings.llm_timeout_s)
        self._no_think_ok: dict[str, bool] = {}

    def chat(self, messages, model, schema=None, max_tokens=900) -> LLMResult:
        body = {
            "model": model, "messages": messages, "stream": False, "keep_alive": "30m",
            "options": {"temperature": self.s.temperature, "seed": self.s.seed, "num_predict": max_tokens},
        }
        if schema is not None:
            body["format"] = schema
        if self._no_think_ok.get(model, True):
            body["think"] = False  # Qwen3 etc.: skip the reasoning trace for latency and clean JSON
        t0 = time.perf_counter()
        try:
            r = self._http.post("/api/chat", json=body)
            if r.status_code == 400 and "think" in r.text:
                self._no_think_ok[model] = False
                body.pop("think")
                r = self._http.post("/api/chat", json=body)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"Ollama request failed: {e}") from e
        d = r.json()
        return LLMResult(
            content=THINK_BLOCK.sub("", d["message"]["content"]),
            tokens_in=d.get("prompt_eval_count", 0) or 0,
            tokens_out=d.get("eval_count", 0) or 0,
            latency_ms=(time.perf_counter() - t0) * 1000,
            model=model,
        )

    def health(self) -> bool:
        try:
            return self._http.get("/api/version", timeout=3).status_code == 200
        except httpx.HTTPError:
            return False


class OpenAICompatClient:
    def __init__(self, settings: Settings):
        from openai import OpenAI
        self.s = settings
        self._client = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key, timeout=settings.llm_timeout_s)

    def chat(self, messages, model, schema=None, max_tokens=900) -> LLMResult:
        kwargs = {}
        if schema is not None:
            kwargs["response_format"] = {"type": "json_schema", "json_schema": {"name": "output", "schema": schema}}
        t0 = time.perf_counter()
        try:
            resp = self._client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens,
                temperature=self.s.temperature, seed=self.s.seed, **kwargs)
        except Exception as e:  # SDK raises many error types
            raise LLMError(f"API request failed: {e}") from e
        usage = resp.usage
        return LLMResult(
            content=THINK_BLOCK.sub("", resp.choices[0].message.content or ""),
            tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
            tokens_out=getattr(usage, "completion_tokens", 0) or 0,
            latency_ms=(time.perf_counter() - t0) * 1000,
            model=model,
        )

    def health(self) -> bool:
        return True


class FakeLLM:
    """Deterministic stand-in for tests: `responder(messages, schema) -> str`."""

    def __init__(self, responder: Callable[[list[dict], Optional[dict]], str]):
        self.responder = responder
        self.calls: list[list[dict]] = []

    def chat(self, messages, model, schema=None, max_tokens=900) -> LLMResult:
        self.calls.append(messages)
        content = self.responder(messages, schema)
        return LLMResult(content=content, tokens_in=sum(len(m["content"]) for m in messages) // 4,
                         tokens_out=len(content) // 4, latency_ms=1.0, model=model)

    def health(self) -> bool:
        return True


def make_client(settings: Settings) -> LLMClient:
    if settings.llm_backend == "openai":
        return OpenAICompatClient(settings)
    return OllamaClient(settings)
