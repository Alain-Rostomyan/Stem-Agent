"""Thin wrapper around OpenAI Chat Completions.

Logs every call (prompt, response, tokens, estimated cost) to a JSONL file and
enforces a per-run call budget. Designed to be the single LLM seam used by both
the stem and the specialized agents, so budget accounting and traces stay in
one place.

Tests can inject `fake_completion` (a callable that takes the kwargs we'd send
to OpenAI and returns a dict-shaped completion) to avoid hitting the network.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


class BudgetExceeded(RuntimeError):
    """Raised when a run exhausts its LLM-call budget."""


# Approximate USD per 1K tokens. Cost tracking is best-effort; if a model is
# missing from this table we log the call with cost=None rather than crash.
# Verify current numbers against https://openai.com/api/pricing/ before any
# spend-sensitive run — OpenAI updates these.
_PRICING: dict[str, dict[str, float]] = {
    "gpt-5.1":             {"in": 0.00125, "out": 0.01},
    "gpt-5.1-mini":        {"in": 0.00025, "out": 0.002},
    "gpt-5.1-nano":        {"in": 0.00005, "out": 0.0004},
    "gpt-5":               {"in": 0.00125, "out": 0.01},
    "gpt-5-mini":          {"in": 0.00025, "out": 0.002},
    "gpt-5-nano":          {"in": 0.00005, "out": 0.0004},
    "gpt-4o":              {"in": 0.0025,  "out": 0.01},
    "gpt-4o-mini":         {"in": 0.00015, "out": 0.0006},
    "gpt-4.1":             {"in": 0.002,   "out": 0.008},
    "gpt-4.1-mini":        {"in": 0.0004,  "out": 0.0016},
    "gpt-4-turbo":         {"in": 0.01,    "out": 0.03},
}


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> Optional[float]:
    p = _PRICING.get(model)
    if p is None:
        # Try matching a prefix (e.g. "gpt-4o-2024-08-06" -> "gpt-4o").
        for key, val in _PRICING.items():
            if model.startswith(key):
                p = val
                break
    if p is None:
        return None
    return (prompt_tokens / 1000.0) * p["in"] + (completion_tokens / 1000.0) * p["out"]


@dataclass
class CallRecord:
    call_id: str
    model: str
    messages: list[dict[str, Any]]
    tools: Optional[list[dict[str, Any]]]
    response: dict[str, Any]
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: Optional[float]
    latency_s: float
    ts: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "model": self.model,
            "messages": self.messages,
            "tools": self.tools,
            "response": self.response,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "latency_s": self.latency_s,
            "ts": self.ts,
        }


class LLMClient:
    """OpenAI-backed chat client with logging and a hard call budget.

    Parameters
    ----------
    max_calls:
        Hard limit on number of completions per run. Raises BudgetExceeded once
        exceeded. Set to None for unlimited (do not do this in real runs).
    log_path:
        JSONL file that gets one record appended per call. None disables logging.
    default_model:
        Model used when the caller does not pass `model=...`.
    api_key:
        OpenAI key. Defaults to env OPENAI_API_KEY. Not required if
        `fake_completion` is supplied.
    fake_completion:
        Test seam. If set, we call it instead of OpenAI. It receives the
        keyword args we would've sent and must return an OpenAI-shaped completion
        dict (or any object with `.model_dump()`).
    """

    def __init__(
        self,
        *,
        max_calls: Optional[int] = 100,
        log_path: Optional[str | Path] = None,
        default_model: str = "gpt-4o-mini",
        api_key: Optional[str] = None,
        fake_completion: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.max_calls = max_calls
        self.default_model = default_model
        self.log_path = Path(log_path) if log_path else None
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)

        self.fake_completion = fake_completion
        self._client = None
        if fake_completion is None:
            # Lazy import so test code can run without the SDK installed.
            from openai import OpenAI  # type: ignore

            self._client = OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))

        self.calls_made = 0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_cost_usd = 0.0
        self.records: list[CallRecord] = []

    def _check_budget(self) -> None:
        if self.max_calls is not None and self.calls_made >= self.max_calls:
            raise BudgetExceeded(
                f"LLM call budget exhausted (max_calls={self.max_calls})"
            )

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[str | dict[str, Any]] = None,
        temperature: float = 0.0,
        response_format: Optional[dict[str, Any]] = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Run one chat completion and return the response as a plain dict."""
        self._check_budget()
        model = model or self.default_model

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
        if response_format is not None:
            kwargs["response_format"] = response_format
        kwargs.update(extra)

        t0 = time.time()
        if self.fake_completion is not None:
            raw = self.fake_completion(**kwargs)
        else:
            raw = self._client.chat.completions.create(**kwargs)  # type: ignore[union-attr]
        latency = time.time() - t0

        # Normalize to dict whether the SDK returned a pydantic model or a dict.
        if hasattr(raw, "model_dump"):
            response = raw.model_dump()
        else:
            response = dict(raw)

        usage = response.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        total_tokens = int(usage.get("total_tokens", prompt_tokens + completion_tokens) or 0)
        cost = _estimate_cost(model, prompt_tokens, completion_tokens)

        record = CallRecord(
            call_id=str(uuid.uuid4()),
            model=model,
            messages=messages,
            tools=tools,
            response=response,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
            latency_s=latency,
            ts=time.time(),
        )

        self.calls_made += 1
        self.total_prompt_tokens += prompt_tokens
        self.total_completion_tokens += completion_tokens
        if cost is not None:
            self.total_cost_usd += cost
        self.records.append(record)

        if self.log_path is not None:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict(), default=str) + "\n")

        return response

    def stats(self) -> dict[str, Any]:
        return {
            "calls_made": self.calls_made,
            "max_calls": self.max_calls,
            "prompt_tokens": self.total_prompt_tokens,
            "completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_prompt_tokens + self.total_completion_tokens,
            "cost_usd": round(self.total_cost_usd, 6),
        }
