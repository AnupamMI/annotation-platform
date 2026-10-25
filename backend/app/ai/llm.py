"""
One interface for every language model the platform can use.

LLM_PROVIDER = "anthropic" | "openai" | "ollama" | "none" (default)
  anthropic: Claude Messages API (https://api.anthropic.com/v1/messages)
  openai:    any OpenAI-compatible chat completions endpoint (LLM_BASE_URL to override)
  ollama:    a local model served by Ollama (default http://localhost:11434)
  none:      no model; Ask answers built-in questions and agents use rule-based planning

The model never touches the database directly: it only proposes SQL text or JSON,
which the caller validates and runs with read-only rights.
"""

from __future__ import annotations

import json
import re

import httpx

from app.config import get_settings


class LLMUnavailable(RuntimeError):
    pass


def configured() -> bool:
    s = get_settings()
    return s.llm_provider in ("anthropic", "openai", "ollama") and bool(s.llm_model) and (s.llm_provider == "ollama" or bool(s.llm_api_key))


def describe() -> dict:
    s = get_settings()
    return {"provider": s.llm_provider if configured() else "none", "model": s.llm_model if configured() else None}


async def complete(system: str, user: str, max_tokens: int = 1024) -> str:
    """Single-turn completion. Raises LLMUnavailable with a readable message on failure."""
    s = get_settings()
    if not configured():
        raise LLMUnavailable("No language model is configured.")
    try:
        async with httpx.AsyncClient(timeout=s.llm_timeout_s) as client:
            if s.llm_provider == "anthropic":
                r = await client.post(
                    (s.llm_base_url or "https://api.anthropic.com") + "/v1/messages",
                    headers={"x-api-key": s.llm_api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={"model": s.llm_model, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": user}]},
                )
                r.raise_for_status()
                return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
            if s.llm_provider == "openai":
                r = await client.post(
                    (s.llm_base_url or "https://api.openai.com/v1") + "/chat/completions",
                    headers={"Authorization": f"Bearer {s.llm_api_key}"},
                    json={"model": s.llm_model, "max_tokens": max_tokens,
                          "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
                )
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"] or ""
            r = await client.post(
                (s.llm_base_url or "http://localhost:11434") + "/api/chat",
                json={"model": s.llm_model, "stream": False, "options": {"num_predict": max_tokens},
                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
            )
            r.raise_for_status()
            return r.json()["message"]["content"]
    except httpx.HTTPStatusError as exc:
        raise LLMUnavailable(f"The language model returned an error ({exc.response.status_code}).") from exc
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise LLMUnavailable("The language model could not be reached.") from exc


def extract_json(text: str) -> dict:
    """Models sometimes wrap JSON in prose or code fences; take the first object."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in the model's reply")
    return json.loads(m.group(0))
