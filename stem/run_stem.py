"""Stem evolution loop — the main harness.

Wires the five Phase 2 modules together:
  - investigate (one-shot, before the loop)
  - loop body: propose -> test_proposal -> (accept | reject) -> stop_check
  - on stop: write final config out

CLI:
    python -m stem.run_stem --domain research

Acceptance rule: a tested proposal is accepted iff sanity_passed AND
probe_score >= last_accepted_score - regression_tolerance. Strict
regressions are rolled back; ties and small fluctuations are kept so the
loop can explore laterally.

Budget: the stem_llm has its own LLMClient with max_calls; we also pass
that limit to stop_check so the loop terminates *before* the LLMClient
raises BudgetExceeded mid-iteration.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.config import AgentConfig, default_config, save_config  # noqa: E402
from stem.commit import accept_proposal, reject_proposal  # noqa: E402
from stem.dotenv import load_dotenv  # noqa: E402
from stem.investigate import (  # noqa: E402
    InvestigationResult,
    investigate,
    load_baseline_traces,
)
from stem.llm_client import BudgetExceeded, LLMClient  # noqa: E402
from stem.propose import Proposal, propose  # noqa: E402
from stem.stop_check import should_stop  # noqa: E402
from stem.test_proposal import TestResult, test_proposal  # noqa: E402
from tools import registry  # noqa: E402


SANITY_TASKS: dict[str, dict[str, Any]] = {
    # A single trivial domain task. Sanity is "agent terminates with some
    # answer," not "agent gets it right." For research, a question whose
    # answer is in any LLM's parametric memory works fine.
    "research": {
        "id": "sanity_research",
        "task": "Answer briefly: what is the chemical symbol for the element gold?",
    },
    "qa": {
        "id": "sanity_qa",
        "task": "Reply with the single word OK.",
    },
}


@dataclass
class IterationRecord:
    step: int
    proposal: dict[str, Any]
    test_result: dict[str, Any]
    commit_outcome: dict[str, Any]
    accepted: bool
    probe_score: float
    last_accepted_score: float
    stem_calls_used: int


@dataclass
class EvolutionLog:
    domain: str
    started_at: str
    initial_config: dict[str, Any]
    investigation: dict[str, Any]
    iterations: list[IterationRecord] = field(default_factory=list)
    final_config: Optional[dict[str, Any]] = None
    stop_reason: str = "incomplete"
    final_probe_score: float = 0.0
    stem_stats: dict[str, Any] = field(default_factory=dict)
    agent_stats: dict[str, Any] = field(default_factory=dict)
    judge_stats: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def _git(args: list[str], *, cwd: Path) -> tuple[int, str, str]:
    proc = subprocess.run(
        ["git"] + args, cwd=str(cwd),
        capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _ensure_clean_tree(repo_root: Path) -> None:
    """The loop assumes a clean working tree per iteration. Refuse to start otherwise."""
    rc, out, _ = _git(["status", "--porcelain"], cwd=repo_root)
    if rc != 0:
        raise RuntimeError("git status failed; is this a git repo?")
    if out.strip():
        raise RuntimeError(
            "working tree is dirty — commit or stash before running the stem.\n"
            f"{out}"
        )


def _commit_initial_config(
    initial_config: AgentConfig,
    *,
    config_path: Path,
    repo_root: Path,
    domain: str,
) -> str:
    """Save the starting config and commit it as 'evolution step 0'.

    Idempotent: if the config already matches what's on disk and HEAD, return
    HEAD's sha without making a new commit. This lets the operator re-run the
    stem after a crash without manual `git reset` surgery.
    """
    save_config(initial_config, config_path)
    rc, _, err = _git(["add", "--", str(config_path)], cwd=repo_root)
    if rc != 0:
        raise RuntimeError(f"git add (initial config) failed: {err}")
    # Nothing staged means the config is already at HEAD — treat as success.
    rc_diff, _, _ = _git(["diff", "--cached", "--quiet"], cwd=repo_root)
    if rc_diff == 0:
        rc, sha, _ = _git(["rev-parse", "HEAD"], cwd=repo_root)
        return sha if rc == 0 else ""
    rc, _, err = _git(
        ["commit", "-m", f"evolution step 0: initial config for domain={domain}"],
        cwd=repo_root,
    )
    if rc != 0:
        raise RuntimeError(f"git commit (initial config) failed: {err}")
    rc, sha, _ = _git(["rev-parse", "HEAD"], cwd=repo_root)
    return sha if rc == 0 else ""


def run_stem_evolution(
    *,
    domain: str,
    domain_description: str,
    probe_tasks_path: Path,
    sanity_task: dict[str, Any],
    config_output_path: Path,
    stem_llm: LLMClient,
    agent_llm: LLMClient,
    starter_tool_names: list[str],
    judge_llm: Optional[LLMClient] = None,
    initial_config: Optional[AgentConfig] = None,
    baseline_traces: Optional[list[dict[str, Any]]] = None,
    max_iterations: int = 20,
    plateau_window: int = 3,
    plateau_threshold: float = 0.05,
    regression_tolerance: float = 0.0,
    max_steps_per_task: int = 12,
    sanity_max_steps: int = 5,
    stem_model: str = "gpt-5.1",
    agent_model: str = "gpt-4o-mini",
    judge_model: str = "gpt-4o-mini",
    custom_tools_root: Path = Path("tools/custom"),
    repo_root: Path = ROOT,
    on_iteration: Optional[Callable[[IterationRecord], None]] = None,
    on_probe_task: Optional[Callable[[dict[str, Any]], None]] = None,
) -> EvolutionLog:
    """Run the stem evolution loop end-to-end. Returns an EvolutionLog."""
    _ensure_clean_tree(repo_root)

    if initial_config is None:
        initial_config = default_config(domain=domain)
    initial_config.validate()

    _commit_initial_config(
        initial_config,
        config_path=config_output_path,
        repo_root=repo_root,
        domain=domain,
    )

    # ---- investigate (one shot)
    investigation = investigate(
        domain=domain,
        domain_description=domain_description,
        sample_tasks=_load_probe_tasks(probe_tasks_path),
        starter_tools=[(n, registry.get(n).description) for n in starter_tool_names],
        llm=stem_llm,
        baseline_traces=baseline_traces,
        model=stem_model,
        max_steps_per_task=max_steps_per_task,
    )

    log = EvolutionLog(
        domain=domain,
        started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        initial_config=initial_config.to_dict(),
        investigation={
            "model": investigation.model,
            "n_sample_tasks": investigation.n_sample_tasks,
            "n_baseline_traces": investigation.n_baseline_traces,
            "analysis": investigation.analysis,
        },
    )

    current_config = initial_config
    last_accepted_score = 0.0
    probe_history: list[float] = []

    for step in range(1, max_iterations + 1):
        stop, reason = should_stop(
            probe_history=probe_history,
            plateau_window=plateau_window,
            plateau_threshold=plateau_threshold,
            budget_calls_used=stem_llm.calls_made,
            budget_calls_limit=stem_llm.max_calls or 10**9,
        )
        if stop:
            log.stop_reason = reason
            break

        try:
            proposal = propose(
                domain=domain,
                current_config=current_config,
                analysis=investigation.analysis,
                recent_probe_scores=list(probe_history),
                starter_tool_names=starter_tool_names,
                llm=stem_llm,
                model=stem_model,
            )
        except BudgetExceeded:
            log.stop_reason = "budget"
            break

        result = test_proposal(
            proposal, current_config,
            domain=domain,
            probe_tasks_path=probe_tasks_path,
            sanity_task=sanity_task,
            llm=agent_llm,
            judge_llm=judge_llm,
            max_steps_per_task=max_steps_per_task,
            sanity_max_steps=sanity_max_steps,
            model=agent_model,
            custom_tools_root=custom_tools_root,
            on_probe_task_complete=on_probe_task,
        )

        accept = (
            result.sanity_passed
            and result.probe_score >= last_accepted_score - regression_tolerance
        )
        if accept:
            outcome = accept_proposal(
                result, step_n=step,
                config_path=config_output_path,
                repo_root=repo_root,
            )
            if outcome.accepted:
                current_config = result.candidate_config
                last_accepted_score = result.probe_score
                probe_history.append(result.probe_score)
            else:
                # Commit failed for an infrastructure reason — treat as reject.
                reject_proposal(result, repo_root=repo_root)
                accept = False
        else:
            outcome = reject_proposal(result, repo_root=repo_root)

        record = IterationRecord(
            step=step,
            proposal=proposal.to_dict(),
            test_result=result.to_dict(),
            commit_outcome=outcome.to_dict(),
            accepted=accept and outcome.accepted,
            probe_score=result.probe_score,
            last_accepted_score=last_accepted_score,
            stem_calls_used=stem_llm.calls_made,
        )
        log.iterations.append(record)
        if on_iteration is not None:
            on_iteration(record)
    else:
        log.stop_reason = "max_iterations"

    log.final_config = current_config.to_dict()
    log.final_probe_score = last_accepted_score
    log.stem_stats = stem_llm.stats()
    log.agent_stats = agent_llm.stats()
    log.judge_stats = judge_llm.stats() if judge_llm is not None else None
    return log


def _load_probe_tasks(path: Path) -> list[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as f:
        return list(json.load(f).get("tasks") or [])


# --------------------------------------------------------------------- CLI

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--domain", choices=["qa", "research"], required=True)
    p.add_argument("--max-iterations", type=int, default=20)
    p.add_argument("--stem-budget-calls", type=int, default=50)
    p.add_argument("--agent-budget-calls", type=int, default=400)
    p.add_argument("--judge-budget-calls", type=int, default=200)
    p.add_argument("--stem-model", default=os.environ.get("STEM_STEM_MODEL", "gpt-5.1"))
    p.add_argument("--agent-model", default=os.environ.get("STEM_DEFAULT_MODEL", "gpt-4o-mini"))
    p.add_argument("--judge-model", default=os.environ.get("STEM_JUDGE_MODEL", "gpt-4o-mini"))
    p.add_argument("--max-steps-per-task", type=int, default=12)
    p.add_argument("--regression-tolerance", type=float, default=0.0)
    p.add_argument("--baseline-result", default=None,
                   help="Path to a baseline_<domain>.json result; its per_task records "
                        "are used as baseline traces for investigate. Defaults to "
                        "runs/baseline_<domain>.json.")
    p.add_argument("--starter-tools", default=None,
                   help="Comma-separated subset of starter tools to expose to the stem "
                        "(e.g. 'run_python,call_llm' for the B-style ablation). When set, "
                        "the initial config's enabled_tools is also intersected with this "
                        "subset. Defaults to all registered starter tools.")
    p.add_argument("--run-name", default=None,
                   help="Override the output config and run-log basename. Defaults to "
                        "--domain. Use this to run an ablation without clobbering the "
                        "regular configs/<domain>.json (e.g. --run-name qa_ablation).")
    p.add_argument("--dry-run", action="store_true",
                   help="Skip the API key check and exit before running.")
    args = p.parse_args()

    load_dotenv(ROOT / ".env")

    if args.dry_run:
        print(f"[dry-run] would run stem on domain={args.domain}, "
              f"max_iterations={args.max_iterations}, "
              f"stem_budget={args.stem_budget_calls} calls @ {args.stem_model}")
        return 0

    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not set in environment.", file=sys.stderr)
        return 2

    registry.load_starter_tools()
    all_starter_names = sorted(registry.all_tools().keys())
    if args.starter_tools:
        requested = [s.strip() for s in args.starter_tools.split(",") if s.strip()]
        unknown = [s for s in requested if s not in all_starter_names]
        if unknown:
            print(f"ERROR: unknown starter tool(s): {unknown}. "
                  f"Known: {all_starter_names}", file=sys.stderr)
            return 2
        starter_tool_names = sorted(requested)
        print(f"starter tool subset: {starter_tool_names} "
              f"(of {len(all_starter_names)} registered)")
    else:
        starter_tool_names = all_starter_names

    run_name = args.run_name or args.domain
    ts = time.strftime("%Y%m%dT%H%M%S")
    log_dir = ROOT / "runs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stem_log = log_dir / f"stem_{run_name}_{ts}_stem.jsonl"
    agent_log = log_dir / f"stem_{run_name}_{ts}_agent.jsonl"
    judge_log = log_dir / f"stem_{run_name}_{ts}_judge.jsonl"
    evolution_log_path = log_dir / f"stem_{run_name}_{ts}.json"

    stem_llm = LLMClient(
        max_calls=args.stem_budget_calls,
        log_path=stem_log,
        default_model=args.stem_model,
    )
    agent_llm = LLMClient(
        max_calls=args.agent_budget_calls,
        log_path=agent_log,
        default_model=args.agent_model,
    )
    judge_llm = None
    if args.domain == "research":
        judge_llm = LLMClient(
            max_calls=args.judge_budget_calls,
            log_path=judge_log,
            default_model=args.judge_model,
        )

    probe_path = ROOT / "evals" / args.domain / "probe_set.json"
    config_out = ROOT / "configs" / f"{run_name}.json"

    # Build the initial config. When the operator restricted the starter set
    # (e.g. for the B-style ablation), the initial config's enabled_tools must
    # be the intersection of default_config()'s tools with the allowed subset
    # — otherwise the agent would start with tools the stem isn't even allowed
    # to know about.
    initial_cfg = default_config(domain=args.domain)
    if args.starter_tools:
        initial_cfg.enabled_tools = [
            t for t in initial_cfg.enabled_tools if t in starter_tool_names
        ]

    baseline_path = (
        Path(args.baseline_result) if args.baseline_result
        else ROOT / "runs" / f"baseline_{args.domain}.json"
    )
    baseline_traces: Optional[list[dict[str, Any]]] = None
    if baseline_path.exists():
        baseline_traces = load_baseline_traces(str(baseline_path))
        print(f"loaded {len(baseline_traces)} baseline traces from {baseline_path.name}")
    else:
        print(f"warning: no baseline result at {baseline_path}; investigate will run "
              "without traces")

    sanity_task = SANITY_TASKS[args.domain]
    domain_description = (
        "Open-domain question answering with citations: gather web evidence, "
        "produce a concise final answer with at least one URL citation."
        if args.domain == "research"
        else "Python test generation: read a target module, write tests under a "
             "given filename that catch a seeded bug."
    )

    def _on_iter(rec: IterationRecord) -> None:
        action = rec.proposal.get("action", "?")
        verdict = "ACCEPT" if rec.accepted else "REJECT"
        print(f"  step {rec.step}: {action:<22} {verdict} probe={rec.probe_score:.3f} "
              f"(best={rec.last_accepted_score:.3f}, stem_calls={rec.stem_calls_used})")

    def _on_probe(rec: dict[str, Any]) -> None:
        # Compact per-probe print so users can see where the agent is.
        tid = rec.get("task_id", "?")
        if "correct" in rec:
            print(f"    probe {tid}: correct={rec['correct']}")
        elif "score" in rec:
            print(f"    probe {tid}: score={rec['score']}")

    print(f"Running stem on domain={args.domain} (run_name={run_name})...")
    log = run_stem_evolution(
        domain=args.domain,
        domain_description=domain_description,
        probe_tasks_path=probe_path,
        sanity_task=sanity_task,
        config_output_path=config_out,
        stem_llm=stem_llm,
        agent_llm=agent_llm,
        judge_llm=judge_llm,
        starter_tool_names=starter_tool_names,
        initial_config=initial_cfg,
        baseline_traces=baseline_traces,
        max_iterations=args.max_iterations,
        max_steps_per_task=args.max_steps_per_task,
        regression_tolerance=args.regression_tolerance,
        stem_model=args.stem_model,
        agent_model=args.agent_model,
        judge_model=args.judge_model,
        on_iteration=_on_iter,
        on_probe_task=_on_probe,
    )

    with evolution_log_path.open("w", encoding="utf-8") as f:
        json.dump(log.to_dict(), f, indent=2, default=str)

    print()
    print(f"stop reason: {log.stop_reason}")
    print(f"final probe score: {log.final_probe_score:.3f}")
    print(f"iterations run: {len(log.iterations)}")
    accepted_count = sum(1 for r in log.iterations if r.accepted)
    print(f"accepted: {accepted_count}/{len(log.iterations)}")
    print(f"stem stats: {log.stem_stats}")
    print(f"agent stats: {log.agent_stats}")
    if log.judge_stats:
        print(f"judge stats: {log.judge_stats}")
    print()
    print(f"final config: {config_out}")
    print(f"evolution log: {evolution_log_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
