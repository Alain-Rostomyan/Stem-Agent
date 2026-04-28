"""Generic baseline agent — the 'before' in the before/after comparison.

Same starter tools as a specialized agent could use, generic prompt, single
loop. We rely on this being identical across QA and Research domains so the
only thing that varies between baseline and specialized is the stem's output.
"""

from __future__ import annotations

from typing import Optional

from agent.config import AgentConfig
from agent.runner import AgentResult, run_agent
from stem.llm_client import LLMClient
from tools import registry


GENERIC_PROMPT = (
    "You are a helpful AI agent. You will be given a task. Use the tools you have "
    "to investigate, take action, and produce a final answer. Be concise. When you "
    "are confident the task is complete, reply with the final answer and stop calling tools."
)


def baseline_config() -> AgentConfig:
    """A generic config: all 7 starter tools, generic prompt, single loop."""
    if not registry.all_tools():
        registry.load_starter_tools()
    starter_names = sorted(registry.all_tools().keys())
    return AgentConfig(
        system_prompt=GENERIC_PROMPT,
        enabled_tools=starter_names,
        custom_tools=[],
        few_shot_examples=[],
        architecture="single_loop",
        domain_metadata={"name": "baseline", "notes": "generic before-comparison agent"},
    )


def run_baseline(
    task: str,
    llm: LLMClient,
    *,
    max_steps: int = 20,
    model: Optional[str] = None,
) -> AgentResult:
    return run_agent(baseline_config(), llm, task, max_steps=max_steps, model=model)
