from typing import Optional

from tools.context import get_llm_client
from tools.registry import tool


@tool(
    param_descriptions={
        "prompt": "User-role text for the sub-LLM.",
        "system": "Optional system-role instruction for the sub-LLM.",
        "model": "Optional model override; default uses the run's default model.",
    },
)
def call_llm(prompt: str, system: Optional[str] = None, model: Optional[str] = None) -> str:
    """Make a one-shot sub-LLM call and return the assistant's text.

    Use this for self-critique, summarization, planning, or any subtask where
    you want a fresh LLM completion that does NOT have access to your tools.
    The call counts against the run's overall LLM budget.
    """
    client = get_llm_client()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    try:
        resp = client.complete(messages=messages, model=model)
    except Exception as exc:  # BudgetExceeded should propagate
        from stem.llm_client import BudgetExceeded
        if isinstance(exc, BudgetExceeded):
            raise
        return f"ERROR: {type(exc).__name__}: {exc}"
    try:
        return resp["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return "ERROR: malformed LLM response"
