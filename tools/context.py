"""Per-run tool context.

Some tools (notably `call_llm`) need access to the live `LLMClient` so that
sub-LLM calls share the run's budget and trace log. We don't expose this as a
tool argument — it would only confuse the model — so the runner sets it via
`set_llm_client()` before each tool call and tools read it via `get_llm_client()`.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Optional


_llm_client: ContextVar[Optional[Any]] = ContextVar("stem_llm_client", default=None)


def set_llm_client(client: Any) -> None:
    _llm_client.set(client)


def get_llm_client() -> Any:
    client = _llm_client.get()
    if client is None:
        raise RuntimeError(
            "no active LLMClient in tool context — was set_llm_client called?"
        )
    return client
