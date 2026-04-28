"""QA eval runner.

Each task ships a buggy implementation and a held-out fixed reference. The
agent gets the spec + buggy code and must write a pytest test file. We score
by running pytest twice in a sandboxed tmpdir:

  - against the buggy code → tests should FAIL (exit code != 0, but with tests
    collected — exit 5 means "no tests" and counts as a fail of our own check)
  - against the fixed code → tests should PASS (exit 0)

A task scores 1 iff both conditions hold. Anything else (no test file written,
syntax errors, vacuous tests) scores 0. Aggregate metric is the mean.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from agent.config import AgentConfig
from agent.runner import AgentResult, run_agent
from stem.llm_client import LLMClient


PYTEST_TIMEOUT_S = 30


def load_task_set(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_task_prompt(task: dict[str, Any], work_dir: Path) -> str:
    """Construct the user prompt the agent sees for one QA task."""
    return (
        "You are writing pytest tests to catch a bug in a Python function.\n\n"
        f"Specification:\n{task['spec']}\n\n"
        f"The (possibly buggy) implementation lives at:\n  {work_dir / 'target.py'}\n\n"
        "Its current contents are:\n"
        "```python\n"
        f"{task['buggy_code']}"
        "```\n\n"
        f"Your task: write a pytest test file to {work_dir / 'test_target.py'} that:\n"
        f"  1. imports the function: `from target import {task['function_name']}`\n"
        "  2. tests its behavior against the specification above\n"
        "  3. would FAIL on the buggy implementation shown above, but PASS on a correct one\n\n"
        "Use the write_file tool to create the test file. Include at least 2 distinct\n"
        "assertions. When the file is written, reply with the single word: DONE."
    )


def _run_pytest(work_dir: Path, *, target_code: str) -> dict[str, Any]:
    """Write target.py = target_code, run pytest, return {exit_code, stdout, collected, ...}."""
    (work_dir / "target.py").write_text(target_code, encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(work_dir) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--no-header", "test_target.py"],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=PYTEST_TIMEOUT_S,
            env=env,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": "TIMEOUT", "collected": 0, "timed_out": True}

    out = proc.stdout or ""
    err = proc.stderr or ""
    # Heuristic: pytest with -q prints "N passed", "N failed", "N error", etc.
    collected = 0
    for token in ("passed", "failed", "error", "errors"):
        # naive but good enough; rely on exit_code as primary signal
        pass
    return {
        "exit_code": proc.returncode,
        "stdout": out[-4000:],
        "stderr": err[-2000:],
        "timed_out": False,
    }


def score_task(task: dict[str, Any], work_dir: Path) -> dict[str, Any]:
    """Given a tmpdir that already contains test_target.py, run pytest twice.

    Returns a dict with score (0/1) and detailed sub-results.
    """
    test_file = work_dir / "test_target.py"
    if not test_file.exists():
        return {
            "task_id": task["id"], "score": 0,
            "reason": "no_test_file",
            "buggy": None, "fixed": None,
        }
    buggy = _run_pytest(work_dir, target_code=task["buggy_code"])
    fixed = _run_pytest(work_dir, target_code=task["fixed_code"])
    # pytest exit codes: 0=ok, 1=failures, 2=errors, 5=no tests collected
    buggy_failed = buggy["exit_code"] not in (0, 5) and not buggy["timed_out"]
    fixed_passed = fixed["exit_code"] == 0
    if buggy["exit_code"] == 5 or fixed["exit_code"] == 5:
        reason = "no_tests_collected"
        score = 0
    elif buggy["timed_out"] or fixed["timed_out"]:
        reason = "timeout"
        score = 0
    elif buggy_failed and fixed_passed:
        reason = "ok"
        score = 1
    elif not buggy_failed and not fixed_passed:
        reason = "tests_useless_or_broken"
        score = 0
    elif not buggy_failed:
        reason = "tests_pass_on_buggy"
        score = 0
    else:
        reason = "tests_fail_on_fixed"
        score = 0
    return {
        "task_id": task["id"],
        "score": score,
        "reason": reason,
        "buggy": buggy,
        "fixed": fixed,
    }


@dataclass
class QAEvalResult:
    config_summary: dict[str, Any]
    task_set: str
    per_task: list[dict[str, Any]] = field(default_factory=list)
    pass_rate: float = 0.0
    n_tasks: int = 0
    n_passed: int = 0
    llm_stats: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_summary": self.config_summary,
            "task_set": self.task_set,
            "n_tasks": self.n_tasks,
            "n_passed": self.n_passed,
            "pass_rate": self.pass_rate,
            "llm_stats": self.llm_stats,
            "per_task": self.per_task,
        }


def run_qa_eval(
    config: AgentConfig,
    llm: LLMClient,
    task_set_path: str | Path,
    *,
    max_steps_per_task: int = 12,
    model: Optional[str] = None,
    on_task_complete=None,
) -> QAEvalResult:
    """Run `config` against every task in `task_set_path`. Return aggregate result."""
    data = load_task_set(task_set_path)
    tasks = data["tasks"]
    per_task: list[dict[str, Any]] = []
    n_passed = 0

    for task in tasks:
        with tempfile.TemporaryDirectory(prefix=f"qa_{task['id']}_") as tmp:
            work_dir = Path(tmp)
            prompt = build_task_prompt(task, work_dir)
            try:
                agent_result: AgentResult = run_agent(
                    config, llm, prompt,
                    max_steps=max_steps_per_task, model=model,
                )
            except Exception as e:  # noqa: BLE001
                from stem.llm_client import BudgetExceeded
                per_task.append({
                    "task_id": task["id"], "score": 0,
                    "reason": "budget" if isinstance(e, BudgetExceeded) else "exception",
                    "error": f"{type(e).__name__}: {e}",
                })
                if isinstance(e, BudgetExceeded):
                    break
                continue

            scored = score_task(task, work_dir)
            scored["agent_stopped"] = agent_result.stopped_reason
            scored["agent_steps"] = len(agent_result.steps)
            scored["agent_final"] = (agent_result.final_answer or "")[:500]
            per_task.append(scored)
            if scored["score"] == 1:
                n_passed += 1
            if on_task_complete is not None:
                on_task_complete(scored)

    n = len(per_task)
    result = QAEvalResult(
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
        n_passed=n_passed,
        pass_rate=(n_passed / n) if n else 0.0,
        llm_stats=llm.stats(),
    )
    return result
