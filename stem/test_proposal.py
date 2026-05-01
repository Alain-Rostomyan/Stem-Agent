"""Stem proposal-testing phase.

Applies a Proposal to a deep-copied AgentConfig, then runs:
  1. A sanity check — a single trivial task where the agent must terminate
     cleanly and produce a non-empty final answer. Catches "proposal broke the
     agent" (e.g., bad python_code, prompt that won't terminate, etc.).
  2. The probe set — measures probe-set accuracy/pass-rate. Used by the stem
     loop to decide whether the proposal moved the score and is worth keeping.

Apply-side effects are intentional and minimal: write_tool action writes a
single file under tools/custom/<domain>/<tool_name>.py and adds it to
candidate_config.custom_tools / enabled_tools. We do NOT clean these up
on rejection — commit.py uses `git reset --hard HEAD` to handle rollback,
so disk state stays in sync with the committed config.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from agent.config import AgentConfig
from agent.runner import run_agent
from stem.llm_client import LLMClient
from stem.propose import Proposal


@dataclass
class TestResult:
    proposal: Proposal
    candidate_config: AgentConfig
    sanity_passed: bool
    sanity_reason: str
    sanity_answer: Optional[str]
    probe_score: float
    probe_per_task: list[dict[str, Any]]
    written_files: list[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal": self.proposal.to_dict(),
            "candidate_config": self.candidate_config.to_dict(),
            "sanity_passed": self.sanity_passed,
            "sanity_reason": self.sanity_reason,
            "sanity_answer": self.sanity_answer,
            "probe_score": self.probe_score,
            "probe_per_task": self.probe_per_task,
            "written_files": list(self.written_files),
            "error": self.error,
        }


def apply_proposal_to_config(
    proposal: Proposal,
    current_config: AgentConfig,
    *,
    domain: str,
    custom_tools_root: Path = Path("tools/custom"),
) -> tuple[AgentConfig, list[str]]:
    """Return (new_config, list_of_filesystem_paths_written).

    Pure for everything except write_tool, which writes one .py file under
    custom_tools_root/<domain>/. Raises ValueError if proposal is invalid.
    """
    if not proposal.valid:
        raise ValueError(
            f"cannot apply an invalid proposal (errors={proposal.validation_errors})"
        )
    new_cfg = copy.deepcopy(current_config)
    written: list[str] = []

    action = proposal.action
    details = proposal.details

    if action == "modify_prompt":
        new_cfg.system_prompt = details["new_system_prompt"]

    elif action == "enable_tool":
        name = details["tool_name"]
        if name not in new_cfg.enabled_tools:
            new_cfg.enabled_tools = list(new_cfg.enabled_tools) + [name]

    elif action == "write_tool":
        tool_name = details["tool_name"]
        code = details["python_code"]
        domain_dir = custom_tools_root / domain
        domain_dir.mkdir(parents=True, exist_ok=True)
        path = domain_dir / f"{tool_name}.py"
        path.write_text(code, encoding="utf-8")
        written.append(str(path))
        new_cfg.custom_tools = list(new_cfg.custom_tools) + [str(path)]
        if tool_name not in new_cfg.enabled_tools:
            new_cfg.enabled_tools = list(new_cfg.enabled_tools) + [tool_name]

    elif action == "add_few_shot":
        new_cfg.few_shot_examples = list(new_cfg.few_shot_examples) + [details["example"]]

    elif action == "switch_architecture":
        new_cfg.architecture = details["new_architecture"]

    else:
        raise ValueError(f"unknown action: {action!r}")

    new_cfg.validate()
    return new_cfg, written


def run_sanity_check(
    candidate: AgentConfig,
    llm: LLMClient,
    sanity_task: dict[str, Any],
    *,
    max_steps: int = 5,
    model: Optional[str] = None,
) -> tuple[bool, str, Optional[str]]:
    """Return (passed, reason, final_answer).

    Sanity passes iff:
      - run_agent doesn't raise (custom-tool import errors, etc., would land here);
      - the agent stops with reason 'answer' or 'max_steps' (not 'error'/'budget');
      - the agent produces a non-empty final_answer string.

    Correctness of the answer is *not* checked. The point is to catch
    proposals that break the agent's basic ability to function.
    """
    task_text = sanity_task.get("task") or sanity_task.get("question") or ""
    if not task_text:
        return False, "sanity_task_missing_text", None
    try:
        result = run_agent(candidate, llm, task_text, max_steps=max_steps, model=model)
    except Exception as e:  # noqa: BLE001
        return False, f"sanity_exception:{type(e).__name__}:{e}", None
    if result.stopped_reason == "error":
        return False, f"sanity_agent_error:{result.error}", None
    if result.stopped_reason == "budget":
        return False, "sanity_budget_exceeded", None
    if not result.final_answer or not result.final_answer.strip():
        return (
            False,
            f"sanity_no_answer (stopped={result.stopped_reason})",
            result.final_answer,
        )
    return True, "ok", result.final_answer


def _run_probe(
    candidate: AgentConfig,
    llm: LLMClient,
    probe_tasks_path: Path,
    *,
    domain: str,
    judge_llm: Optional[LLMClient] = None,
    max_steps_per_task: int = 12,
    model: Optional[str] = None,
    on_task_complete: Optional[Callable[[dict[str, Any]], None]] = None,
) -> tuple[float, list[dict[str, Any]]]:
    """Dispatch to the right eval runner for the domain. Returns (score, per_task)."""
    if domain == "research":
        from evals.research.runner import run_research_eval
        result = run_research_eval(
            candidate, llm, probe_tasks_path,
            judge_llm=judge_llm,
            max_steps_per_task=max_steps_per_task,
            model=model,
            check_citations=False,
            on_task_complete=on_task_complete,
        )
        return result.accuracy, result.per_task
    if domain == "qa":
        from evals.qa.runner import run_qa_eval
        result = run_qa_eval(
            candidate, llm, probe_tasks_path,
            max_steps_per_task=max_steps_per_task,
            on_task_complete=on_task_complete,
        )
        return result.pass_rate, result.per_task
    raise ValueError(f"unknown domain: {domain!r}")


def test_proposal(
    proposal: Proposal,
    current_config: AgentConfig,
    *,
    domain: str,
    probe_tasks_path: Path,
    sanity_task: dict[str, Any],
    llm: LLMClient,
    judge_llm: Optional[LLMClient] = None,
    max_steps_per_task: int = 12,
    sanity_max_steps: int = 5,
    model: Optional[str] = None,
    custom_tools_root: Path = Path("tools/custom"),
    on_probe_task_complete: Optional[Callable[[dict[str, Any]], None]] = None,
) -> TestResult:
    """Apply a proposal to a candidate config, run sanity + probe set, return result.

    Order of short-circuits:
      - invalid proposal → returns immediately, sanity_reason='proposal_invalid'.
      - apply fails (e.g. file write) → returns with error, no sanity attempted.
      - sanity fails → returns with sanity_passed=False, probe_score=0, no probe run.
      - probe raises → returns with sanity_passed=True but error set.
    """
    if not proposal.valid:
        return TestResult(
            proposal=proposal,
            candidate_config=current_config,
            sanity_passed=False,
            sanity_reason="proposal_invalid",
            sanity_answer=None,
            probe_score=0.0,
            probe_per_task=[],
            error=f"validation_errors={proposal.validation_errors}",
        )

    try:
        candidate, written = apply_proposal_to_config(
            proposal, current_config,
            domain=domain, custom_tools_root=custom_tools_root,
        )
    except Exception as e:  # noqa: BLE001
        return TestResult(
            proposal=proposal,
            candidate_config=current_config,
            sanity_passed=False,
            sanity_reason="apply_failed",
            sanity_answer=None,
            probe_score=0.0,
            probe_per_task=[],
            error=f"apply_proposal: {type(e).__name__}: {e}",
        )

    sanity_ok, sanity_reason, sanity_answer = run_sanity_check(
        candidate, llm, sanity_task,
        max_steps=sanity_max_steps, model=model,
    )
    if not sanity_ok:
        return TestResult(
            proposal=proposal,
            candidate_config=candidate,
            sanity_passed=False,
            sanity_reason=sanity_reason,
            sanity_answer=sanity_answer,
            probe_score=0.0,
            probe_per_task=[],
            written_files=written,
        )

    try:
        probe_score, per_task = _run_probe(
            candidate, llm, probe_tasks_path,
            domain=domain,
            judge_llm=judge_llm,
            max_steps_per_task=max_steps_per_task,
            model=model,
            on_task_complete=on_probe_task_complete,
        )
    except Exception as e:  # noqa: BLE001
        return TestResult(
            proposal=proposal,
            candidate_config=candidate,
            sanity_passed=True,
            sanity_reason=sanity_reason,
            sanity_answer=sanity_answer,
            probe_score=0.0,
            probe_per_task=[],
            written_files=written,
            error=f"probe_run: {type(e).__name__}: {e}",
        )

    return TestResult(
        proposal=proposal,
        candidate_config=candidate,
        sanity_passed=True,
        sanity_reason="ok",
        sanity_answer=sanity_answer,
        probe_score=probe_score,
        probe_per_task=per_task,
        written_files=written,
    )
