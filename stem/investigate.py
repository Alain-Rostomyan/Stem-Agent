"""Stem investigation phase.

Given a problem class (a domain name, a handful of sample tasks, the available
starter tools, and optionally a few baseline traces), produce a written analysis
that `propose.py` can read to suggest specialization steps.

Per the briefing this is a single LLM call. Output is freeform markdown — not
JSON — because the analysis is consumed by another LLM call (propose), and that
LLM is fine with prose; no need to over-constrain the schema here.

The analysis is expected to cover, roughly:
  1. Domain characterization — what kind of problem is this?
  2. What a competent solver does — typical workflow / decomposition.
  3. Failure modes observed — if traces given, what went wrong and why.
  4. Tool / prompt / architecture opportunities — concrete affordances.

Callers are responsible for persisting the result (run_stem writes it to a run
log alongside other evolution artifacts).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from stem.llm_client import LLMClient


SYSTEM_PROMPT = (
    "You are a senior agent designer. You are given a problem class, a few "
    "sample tasks from that class, the tools currently available to a generic "
    "agent that tries to solve them, and optionally traces of how a generic "
    "baseline agent did. Your job is to produce a concise written analysis that "
    "another component will use to propose specialization steps (a better system "
    "prompt, additional tools to enable, custom tools to write, few-shot examples, "
    "or an architectural change).\n\n"
    "Be specific and concrete. Vague advice ('the agent should be smarter') is "
    "useless; concrete affordances ('a fetch_url tool that returns the text "
    "content of a webpage') are what we need. Where you spot a failure pattern "
    "in the traces, name it explicitly and tie it to a specific affordance that "
    "would fix it.\n\n"
    "Structure your analysis with these sections, in order, using markdown headers:\n"
    "  ## Domain characterization\n"
    "  ## What a competent solver does\n"
    "  ## Failure modes (if traces provided; otherwise omit)\n"
    "  ## Specialization opportunities\n\n"
    "Keep it under 700 words. Prefer bullet points over prose. End with the "
    "Specialization opportunities section."
)


@dataclass
class InvestigationResult:
    analysis: str
    domain: str
    n_sample_tasks: int
    n_baseline_traces: int
    model: str
    raw_response: dict[str, Any] = field(repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis": self.analysis,
            "domain": self.domain,
            "n_sample_tasks": self.n_sample_tasks,
            "n_baseline_traces": self.n_baseline_traces,
            "model": self.model,
            "raw_response": self.raw_response,
        }


def _format_tools_block(tools: list[tuple[str, str]]) -> str:
    if not tools:
        return "(no tools enabled)"
    lines = []
    for name, desc in tools:
        first_line = (desc or "").strip().splitlines()[0] if desc else ""
        lines.append(f"- `{name}`: {first_line}")
    return "\n".join(lines)


def _format_tasks_block(tasks: list[dict[str, Any]], max_tasks: int = 5) -> str:
    shown = tasks[:max_tasks]
    lines = []
    for i, t in enumerate(shown, start=1):
        q = t.get("question") or t.get("task") or "(no question field)"
        shape = t.get("shape")
        shape_tag = f" [shape: {shape}]" if shape else ""
        lines.append(f"{i}.{shape_tag} {q}")
    if len(tasks) > max_tasks:
        lines.append(f"... ({len(tasks) - max_tasks} more not shown)")
    return "\n".join(lines)


def summarize_trace(trace: dict[str, Any], max_steps: int = 12) -> str:
    """Compress a per-task trace record (from the eval runner) into a few lines.

    Two shapes show up in practice:
      - Research: `task_id`, `question`, `canonical`, `candidate`, `correct`,
        `agent_steps`, `agent_stopped`, plus optional `judge_rationale`.
      - QA: `task_id`, `score` (0/1), `reason` (e.g. "no_tests",
        "buggy_passed", "fixed_failed"), `buggy`, `fixed`, `agent_final`,
        `agent_steps`, `agent_stopped`. There's no natural-language question
        or canonical answer; the spec lives in the task set, not the per-task
        record.
    """
    parts: list[str] = []
    parts.append(f"task_id: {trace.get('task_id', '?')}")

    # Research-style fields, included only when present and non-empty.
    q_lines = (trace.get("question") or "").strip().splitlines()
    if q_lines:
        parts.append(f"question: {q_lines[0][:200]}")
    if trace.get("canonical") is not None:
        parts.append(f"canonical_answer: {trace.get('canonical')}")
    if "candidate" in trace:
        cand = (trace.get("candidate") or "").strip()
        if cand:
            parts.append(f"agent_answer: {cand[:300]}")
        else:
            parts.append("agent_answer: (none — agent never produced a final answer)")

    # QA-style fields. `reason` is the highest-signal diagnostic for the stem.
    if "score" in trace:
        parts.append(f"score: {trace.get('score')}; reason: {trace.get('reason', '?')}")
    if "agent_final" in trace:
        af = (trace.get("agent_final") or "").strip()
        if af:
            parts.append(f"agent_final (truncated): {af[:400]}")

    correct_field = trace.get("correct")
    if correct_field is None and "score" in trace:
        correct_field = bool(trace.get("score"))
    parts.append(
        f"steps: {trace.get('agent_steps', '?')}/{max_steps}; "
        f"stopped_reason: {trace.get('agent_stopped', '?')}; "
        f"correct: {correct_field if correct_field is not None else '?'}"
    )
    rationale = (trace.get("judge_rationale") or "").strip()
    if rationale:
        parts.append(f"judge: {rationale[:200]}")
    return "\n".join(parts)


def _format_traces_block(traces: list[dict[str, Any]], max_steps_per_task: int) -> str:
    if not traces:
        return ""
    blocks = []
    for i, t in enumerate(traces, start=1):
        blocks.append(f"### Trace {i}\n{summarize_trace(t, max_steps=max_steps_per_task)}")
    return "\n\n".join(blocks)


def build_user_prompt(
    *,
    domain: str,
    domain_description: str,
    sample_tasks: list[dict[str, Any]],
    starter_tools: list[tuple[str, str]],
    baseline_traces: Optional[list[dict[str, Any]]] = None,
    max_steps_per_task: int = 12,
) -> str:
    sections = [
        f"# Domain: {domain}",
        domain_description.strip() if domain_description else "(no description provided)",
        "",
        "## Available tools (the generic agent has these)",
        _format_tools_block(starter_tools),
        "",
        "## Sample tasks",
        _format_tasks_block(sample_tasks),
    ]
    if baseline_traces:
        sections += [
            "",
            "## Baseline traces (generic agent attempting these tasks)",
            _format_traces_block(baseline_traces, max_steps_per_task=max_steps_per_task),
        ]
    sections += [
        "",
        "Produce the analysis as instructed.",
    ]
    return "\n".join(sections)


def investigate(
    *,
    domain: str,
    domain_description: str,
    sample_tasks: list[dict[str, Any]],
    starter_tools: list[tuple[str, str]],
    llm: LLMClient,
    baseline_traces: Optional[list[dict[str, Any]]] = None,
    model: Optional[str] = "gpt-5.1",
    max_steps_per_task: int = 12,
) -> InvestigationResult:
    """Run the stem's investigation phase. One LLM call.

    Parameters
    ----------
    domain:
        Short identifier (e.g. "research", "qa").
    domain_description:
        A sentence or two about what the domain is. Comes from the caller, not
        guessed by the stem — keeps the stem honest about what it was told.
    sample_tasks:
        Probe-set task dicts. We only show the question text (and the shape tag
        if present); answers and notes are intentionally withheld so the stem
        can't shortcut by reading them.
    starter_tools:
        list of (name, short_description) for tools the agent has by default.
    baseline_traces:
        Optional list of per-task records from the baseline eval runner. When
        provided, the stem can see *how* the generic agent failed, which is the
        signal we actually want it to specialize against.
    """
    user = build_user_prompt(
        domain=domain,
        domain_description=domain_description,
        sample_tasks=sample_tasks,
        starter_tools=starter_tools,
        baseline_traces=baseline_traces,
        max_steps_per_task=max_steps_per_task,
    )
    resp = llm.complete(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        model=model,
        temperature=0.2,
    )
    content = (resp["choices"][0]["message"].get("content") or "").strip()
    return InvestigationResult(
        analysis=content,
        domain=domain,
        n_sample_tasks=len(sample_tasks),
        n_baseline_traces=len(baseline_traces or []),
        model=model or llm.default_model,
        raw_response=resp,
    )


def load_baseline_traces(path: str) -> list[dict[str, Any]]:
    """Convenience loader: pull `per_task` from a baseline result JSON."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return list(data.get("per_task") or [])
