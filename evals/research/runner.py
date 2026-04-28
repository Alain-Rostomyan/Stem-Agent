"""Research eval runner.

For each question we run the agent and then score its final answer along two
axes:

  - factual correctness — an LLM judge compares the candidate answer to the
    canonical answer + acceptable aliases and returns a strict yes/no.
  - citation validity — we extract URLs from the candidate answer and probe
    them with a HEAD request; a URL counts as valid if it returns HTTP < 400.

Primary metric is accuracy. Citation validity is reported alongside.

The judge prompt is defined as JUDGE_SYSTEM / JUDGE_USER_TEMPLATE constants
in this module so it shows up in the writeup as a single source of truth.
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from agent.config import AgentConfig
from agent.runner import AgentResult, run_agent
from stem.llm_client import LLMClient


JUDGE_SYSTEM = (
    "You are a strict but fair grader of short factual answers. You will be given "
    "a question, the canonical correct answer, a list of acceptable variants, and "
    "a candidate answer. Decide whether the candidate expresses the same fact as "
    "the canonical answer. Be lenient about formatting (e.g. '1969', 'in 1969', "
    "and 'July 20, 1969' all count for a year question), but reject answers that "
    "contradict the canonical answer, are off by a meaningful amount, or are too "
    "vague to verify. Output a single JSON object with fields 'correct' (boolean) "
    "and 'rationale' (a brief one-sentence justification). Output nothing else."
)


JUDGE_USER_TEMPLATE = (
    "Question: {question}\n"
    "Canonical answer: {answer}\n"
    "Acceptable variants: {aliases}\n\n"
    "Candidate answer:\n{candidate}\n"
)


_URL_RE = re.compile(r"https?://[^\s)>\]]+")


def load_task_set(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def build_task_prompt(task: dict[str, Any]) -> str:
    return (
        f"Answer the following question concisely.\n\n"
        f"Question: {task['question']}\n\n"
        "Use the web_search tool to find supporting evidence. In your final answer, "
        "state the answer in one short sentence and include at least one URL citation "
        "(a literal http(s) link from your search) on a separate line, prefixed with "
        "'Source: '. Stop calling tools once you have an answer."
    )


def extract_urls(text: str) -> list[str]:
    if not text:
        return []
    # Strip trailing punctuation that isn't part of a URL.
    raw = _URL_RE.findall(text)
    cleaned = []
    for u in raw:
        cleaned.append(u.rstrip(".,;:'\""))
    return cleaned


def check_url(url: str, timeout_s: float = 5.0) -> bool:
    """Best-effort liveness probe. Returns True iff HEAD or GET status < 400."""
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(
                url,
                method=method,
                headers={"User-Agent": "Mozilla/5.0 (compatible; stem-agent/0.1)"},
            )
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                if 200 <= resp.status < 400:
                    return True
        except Exception:  # noqa: BLE001
            continue
    return False


def judge_answer(
    judge_llm: LLMClient,
    task: dict[str, Any],
    candidate: str,
    *,
    model: Optional[str] = None,
) -> dict[str, Any]:
    """Ask the judge whether `candidate` is factually equivalent to the canonical answer."""
    user = JUDGE_USER_TEMPLATE.format(
        question=task["question"],
        answer=task["answer"],
        aliases=", ".join(task.get("aliases") or [task["answer"]]),
        candidate=candidate or "(no answer)",
    )
    try:
        resp = judge_llm.complete(
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user", "content": user},
            ],
            model=model,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
    except Exception as e:  # noqa: BLE001
        return {"correct": False, "rationale": f"judge_error: {type(e).__name__}: {e}"}
    raw = (resp["choices"][0]["message"].get("content") or "").strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"correct": False, "rationale": f"judge_unparseable: {raw[:200]}"}
    return {
        "correct": bool(parsed.get("correct")),
        "rationale": str(parsed.get("rationale", "")),
        "raw": raw,
    }


@dataclass
class ResearchEvalResult:
    config_summary: dict[str, Any]
    task_set: str
    per_task: list[dict[str, Any]] = field(default_factory=list)
    n_tasks: int = 0
    n_correct: int = 0
    accuracy: float = 0.0
    avg_citations: float = 0.0
    citation_validity: float = 0.0
    llm_stats: Optional[dict[str, Any]] = None
    judge_stats: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_summary": self.config_summary,
            "task_set": self.task_set,
            "n_tasks": self.n_tasks,
            "n_correct": self.n_correct,
            "accuracy": self.accuracy,
            "avg_citations": self.avg_citations,
            "citation_validity": self.citation_validity,
            "llm_stats": self.llm_stats,
            "judge_stats": self.judge_stats,
            "per_task": self.per_task,
        }


def run_research_eval(
    config: AgentConfig,
    llm: LLMClient,
    task_set_path: str | Path,
    *,
    judge_llm: Optional[LLMClient] = None,
    judge_model: Optional[str] = None,
    max_steps_per_task: int = 10,
    model: Optional[str] = None,
    check_citations: bool = True,
    on_task_complete=None,
) -> ResearchEvalResult:
    """Run `config` against every research task. If judge_llm is None, use `llm`.

    Note: when judge_llm == llm, judge calls count against the same budget as
    the agent. For real "before/after" eval runs, pass a separate judge client.
    """
    judge_llm = judge_llm or llm
    data = load_task_set(task_set_path)
    tasks = data["tasks"]

    per_task: list[dict[str, Any]] = []
    n_correct = 0
    citation_counts: list[int] = []
    citation_validities: list[float] = []

    for task in tasks:
        prompt = build_task_prompt(task)
        try:
            agent_result: AgentResult = run_agent(
                config, llm, prompt,
                max_steps=max_steps_per_task, model=model,
            )
        except Exception as e:  # noqa: BLE001
            from stem.llm_client import BudgetExceeded
            per_task.append({
                "task_id": task["id"], "correct": False,
                "reason": "budget" if isinstance(e, BudgetExceeded) else "exception",
                "error": f"{type(e).__name__}: {e}",
            })
            if isinstance(e, BudgetExceeded):
                break
            continue

        candidate = agent_result.final_answer or ""
        urls = extract_urls(candidate)
        if check_citations and urls:
            valid = [u for u in urls if check_url(u)]
            validity = len(valid) / len(urls)
        else:
            valid = []
            validity = 0.0 if check_citations and not urls else 1.0
        citation_counts.append(len(urls))
        if urls:
            citation_validities.append(validity)

        verdict = judge_answer(judge_llm, task, candidate, model=judge_model)
        if verdict["correct"]:
            n_correct += 1

        record = {
            "task_id": task["id"],
            "question": task["question"],
            "canonical": task["answer"],
            "candidate": candidate,
            "correct": verdict["correct"],
            "judge_rationale": verdict.get("rationale", ""),
            "urls": urls,
            "valid_urls": valid,
            "n_urls": len(urls),
            "citation_validity": validity,
            "agent_stopped": agent_result.stopped_reason,
            "agent_steps": len(agent_result.steps),
        }
        per_task.append(record)
        if on_task_complete is not None:
            on_task_complete(record)

    n = len(per_task)
    avg_citations = (sum(citation_counts) / n) if n else 0.0
    citation_validity = (
        sum(citation_validities) / len(citation_validities)
        if citation_validities else 0.0
    )

    return ResearchEvalResult(
        config_summary={
            "system_prompt": config.system_prompt[:200],
            "enabled_tools": config.enabled_tools,
            "custom_tools": config.custom_tools,
            "architecture": config.architecture,
            "domain_metadata": config.domain_metadata,
        },
        task_set=str(task_set_path),
        per_task=per_task,
        n_tasks=n,
        n_correct=n_correct,
        accuracy=(n_correct / n) if n else 0.0,
        avg_citations=avg_citations,
        citation_validity=citation_validity,
        llm_stats=llm.stats(),
        judge_stats=judge_llm.stats() if judge_llm is not llm else None,
    )
