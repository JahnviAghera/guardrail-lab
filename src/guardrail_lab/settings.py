"""Runtime configuration, loaded from environment variables and `.env`."""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    llm_backend: Literal["ollama", "openai"] = "ollama"
    llm_base_url: str = "http://localhost:11434"
    llm_api_key: str = "ollama"
    generator_model: str = "qwen3:4b"
    classifier_model: str = "qwen3:4b"
    judge_model: str = ""

    enable_prompt_guard: bool = False
    prompt_guard_model: str = "meta-llama/Llama-Prompt-Guard-2-22M"

    db_path: str = "data/guardrail.db"
    policy_path: str = "config/policy.yaml"
    prices_path: str = "config/prices.yaml"

    max_repair_attempts: int = 2
    llm_timeout_s: float = 180.0
    max_input_chars: int = 8000
    max_output_tokens: int = 900
    temperature: float = 0.0
    seed: int = 42
    api_url: str = "http://localhost:8000"

    def path(self, value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else ROOT / p


@lru_cache
def get_settings() -> Settings:
    return Settings()
