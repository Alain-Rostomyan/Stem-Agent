"""Stem commit/rollback phase.

Wraps the git operations that land or revert an evolution step.

Accept flow:
  1. Save candidate AgentConfig to <config_path> as JSON.
  2. `git add` the config file plus any files write_tool produced
     (these come from test_result.written_files).
  3. `git commit -m "evolution step N: <action>" -m "<details>"`.

Reject flow:
  1. Delete any files write_tool produced (untracked at this point).
  2. `git reset --hard HEAD` to revert any tracked-file changes.

Two assumptions hold by construction and we rely on them:
  - test_proposal only writes new files, never modifies tracked ones,
    so accept never has to worry about reverting unrelated edits.
  - The repo's working tree is clean before the loop iteration starts;
    run_stem enforces this. If something dirty sneaks in, git reset
    --hard HEAD will discard it — that's a deliberate part of the
    "safeguards" story (always commit or always revert; no in-between).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agent.config import save_config
from stem.test_proposal import TestResult


@dataclass
class CommitOutcome:
    accepted: bool
    commit_hash: Optional[str]
    message: Optional[str]
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "accepted": self.accepted,
            "commit_hash": self.commit_hash,
            "message": self.message,
            "error": self.error,
        }


def _git(args: list[str], *, cwd: Path) -> tuple[int, str, str]:
    """Run git with args, return (returncode, stdout, stderr)."""
    proc = subprocess.run(
        ["git"] + args, cwd=str(cwd),
        capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _format_commit_message(test_result: TestResult, step_n: int) -> str:
    p = test_result.proposal
    parts = [f"evolution step {step_n}: {p.action}", ""]
    detail_lines: list[str] = []
    if p.action == "write_tool":
        detail_lines.append(f"new tool: {p.details.get('tool_name')}")
        desc = p.details.get("description")
        if desc:
            detail_lines.append(f"description: {desc}")
    elif p.action == "enable_tool":
        detail_lines.append(f"enabled tool: {p.details.get('tool_name')}")
    elif p.action == "switch_architecture":
        detail_lines.append(f"new architecture: {p.details.get('new_architecture')}")
    elif p.action == "modify_prompt":
        detail_lines.append("modified system prompt")
    elif p.action == "add_few_shot":
        detail_lines.append("added few-shot example")

    detail_lines.append(f"probe_score: {test_result.probe_score:.3f}")
    detail_lines.append(f"sanity: {test_result.sanity_reason}")
    if p.rationale:
        detail_lines.append("")
        detail_lines.append(f"rationale: {p.rationale}")

    parts.extend(detail_lines)
    return "\n".join(parts)


def accept_proposal(
    test_result: TestResult,
    *,
    step_n: int,
    config_path: Path,
    repo_root: Path = Path("."),
) -> CommitOutcome:
    """Save the candidate config, git add, git commit. Returns CommitOutcome."""
    if not test_result.sanity_passed:
        return CommitOutcome(
            accepted=False, commit_hash=None, message=None,
            error="cannot accept a proposal that failed sanity",
        )

    config_path = Path(config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    save_config(test_result.candidate_config, config_path)

    paths_to_add = [str(config_path)] + list(test_result.written_files)
    rc, _, err = _git(["add", "--"] + paths_to_add, cwd=repo_root)
    if rc != 0:
        return CommitOutcome(
            accepted=False, commit_hash=None, message=None,
            error=f"git add failed: {err}",
        )

    message = _format_commit_message(test_result, step_n)
    rc, _, err = _git(["commit", "-m", message], cwd=repo_root)
    if rc != 0:
        return CommitOutcome(
            accepted=False, commit_hash=None, message=message,
            error=f"git commit failed: {err}",
        )

    rc, sha, _ = _git(["rev-parse", "HEAD"], cwd=repo_root)
    return CommitOutcome(
        accepted=True,
        commit_hash=sha if rc == 0 else None,
        message=message,
    )


def reject_proposal(
    test_result: TestResult,
    *,
    repo_root: Path = Path("."),
) -> CommitOutcome:
    """Delete files write_tool produced, then `git reset --hard HEAD` to be safe."""
    errors: list[str] = []

    for path_str in test_result.written_files:
        p = Path(path_str)
        try:
            if p.exists():
                p.unlink()
        except Exception as e:  # noqa: BLE001
            errors.append(f"unlink {path_str}: {type(e).__name__}: {e}")

    rc, _, err = _git(["reset", "--hard", "HEAD"], cwd=repo_root)
    if rc != 0:
        errors.append(f"git reset failed: {err}")

    return CommitOutcome(
        accepted=False,
        commit_hash=None,
        message=f"rejected: {test_result.sanity_reason}",
        error="; ".join(errors) if errors else None,
    )
