"""Specialized-agent runner.

Takes an `AgentConfig`, an `LLMClient`, and a task, and runs the agent until it
either returns a final answer or hits `max_steps`. Supports two architectures:

- `single_loop`: the standard "model decides; tools run; loop" pattern.
- `planner_executor`: one extra LLM call to produce a plan, which is then
  prepended to the executor's system prompt before running a single loop.

Both architectures share the same low-level `_run_single_loop` so they share
the same tool-execution / logging path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.config import AgentConfig, load_config
from stem.llm_client import LLMClient
from tools import context as tool_context
from tools import registry


@dataclass
class StepRecord:
    step: int
    assistant_message: dict[str, Any]
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AgentResult:
    final_answer: Optional[str]
    steps: list[StepRecord]
    messages: list[dict[str, Any]]
    stopped_reason: str  # "answer", "max_steps", "budget", "error"
    error: Optional[str] = None
    plan: Optional[str] = None


def _ensure_starters_loaded() -> None:
    if not registry.all_tools():
        registry.load_starter_tools()


def _build_messages(
    config: AgentConfig, task: str, plan: Optional[str] = None
) -> list[dict[str, Any]]:
    system = config.system_prompt
    if plan:
        system = f"{system}\n\n[Plan from planner]\n{plan}"
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    # Few-shot examples are stored as {"task": "...", "answer": "..."} pairs.
    for ex in config.few_shot_examples or []:
        if "task" in ex:
            messages.append({"role": "user", "content": ex["task"]})
        if "answer" in ex:
            messages.append({"role": "assistant", "content": ex["answer"]})
    messages.append({"role": "user", "content": task})
    return messages


def _execute_tool_call(call: dict[str, Any]) -> str:
    fn = call.get("function") or {}
    name = fn.get("name", "")
    raw_args = fn.get("arguments", "{}") or "{}"
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except json.JSONDecodeError as e:
        return f"ERROR: tool args were not valid JSON: {e}"
    try:
        spec = registry.get(name)
    except KeyError:
        return f"ERROR: unknown tool: {name}"
    try:
        result = spec.func(**args)
    except TypeError as e:
        return f"ERROR: bad arguments to {name}: {e}"
    except Exception as e:  # noqa: BLE001
        # call_llm intentionally re-raises BudgetExceeded; let that propagate.
        from stem.llm_client import BudgetExceeded
        if isinstance(e, BudgetExceeded):
            raise
        return f"ERROR: {type(e).__name__}: {e}"
    if not isinstance(result, str):
        # Be defensive — model expects a string in the tool message.
        try:
            result = json.dumps(result, default=str)
        except Exception:  # noqa: BLE001
            result = str(result)
    return result


def _run_single_loop(
    *,
    config: AgentConfig,
    llm: LLMClient,
    task: str,
    max_steps: int,
    plan: Optional[str] = None,
    model: Optional[str] = None,
) -> AgentResult:
    tool_context.set_llm_client(llm)
    messages = _build_messages(config, task, plan=plan)
    tools_spec = registry.openai_tool_specs(config.enabled_tools) if config.enabled_tools else None

    steps: list[StepRecord] = []
    final_answer: Optional[str] = None
    stopped_reason = "max_steps"
    error: Optional[str] = None

    for step_idx in range(max_steps):
        try:
            response = llm.complete(
                messages=messages,
                tools=tools_spec,
                model=model,
            )
        except Exception as e:  # noqa: BLE001
            from stem.llm_client import BudgetExceeded
            if isinstance(e, BudgetExceeded):
                stopped_reason = "budget"
            else:
                stopped_reason = "error"
                error = f"{type(e).__name__}: {e}"
            break

        msg = response["choices"][0]["message"]
        # Convert to a plain dict and keep it in messages so the next turn has context.
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": msg.get("content"),
        }
        tool_calls = msg.get("tool_calls") or []
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)

        record = StepRecord(step=step_idx, assistant_message=assistant_msg, tool_calls=list(tool_calls))
        steps.append(record)

        if not tool_calls:
            final_answer = msg.get("content") or ""
            stopped_reason = "answer"
            break

        # Execute each tool call and append a tool message.
        try:
            for call in tool_calls:
                result = _execute_tool_call(call)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": result,
                })
        except Exception as e:  # noqa: BLE001
            from stem.llm_client import BudgetExceeded
            if isinstance(e, BudgetExceeded):
                stopped_reason = "budget"
            else:
                stopped_reason = "error"
                error = f"{type(e).__name__}: {e}"
            break
    else:
        stopped_reason = "max_steps"

    return AgentResult(
        final_answer=final_answer,
        steps=steps,
        messages=messages,
        stopped_reason=stopped_reason,
        error=error,
        plan=plan,
    )


def _run_planner_executor(
    *,
    config: AgentConfig,
    llm: LLMClient,
    task: str,
    max_steps: int,
    model: Optional[str] = None,
) -> AgentResult:
    planner_messages = [
        {
            "role": "system",
            "content": (
                "You are a planning module. Given a task and a list of available tools, "
                "produce a concise step-by-step plan (3-7 numbered steps) for an executor "
                "to follow. Do NOT call tools yourself. Return only the plan as plain text."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Task:\n{task}\n\n"
                f"Available tools: {', '.join(config.enabled_tools) or '(none)'}\n\n"
                "Write the plan."
            ),
        },
    ]
    try:
        plan_resp = llm.complete(messages=planner_messages, model=model)
    except Exception as e:  # noqa: BLE001
        from stem.llm_client import BudgetExceeded
        if isinstance(e, BudgetExceeded):
            return AgentResult(
                final_answer=None, steps=[], messages=planner_messages,
                stopped_reason="budget", error=None,
            )
        return AgentResult(
            final_answer=None, steps=[], messages=planner_messages,
            stopped_reason="error", error=f"{type(e).__name__}: {e}",
        )
    plan = plan_resp["choices"][0]["message"].get("content") or ""
    return _run_single_loop(
        config=config, llm=llm, task=task,
        max_steps=max_steps, plan=plan, model=model,
    )


def run_agent(
    config: AgentConfig,
    llm: LLMClient,
    task: str,
    *,
    max_steps: int = 20,
    model: Optional[str] = None,
) -> AgentResult:
    """Instantiate the specialized agent from `config` and run it on `task`."""
    config.validate()
    _ensure_starters_loaded()
    if config.custom_tools:
        registry.load_custom_tools(config.custom_tools)

    if config.architecture == "planner_executor":
        return _run_planner_executor(
            config=config, llm=llm, task=task, max_steps=max_steps, model=model,
        )
    return _run_single_loop(
        config=config, llm=llm, task=task, max_steps=max_steps, model=model,
    )


def load_agent(path: str, llm: LLMClient):
    """Convenience: load a config and return a closure that runs it on a task."""
    cfg = load_config(path)

    def _run(task: str, *, max_steps: int = 20, model: Optional[str] = None) -> AgentResult:
        return run_agent(cfg, llm, task, max_steps=max_steps, model=model)

    return _run
