"""CLI entry point: run the generic baseline agent on a domain's eval set.

Locks in the 'before' number used in the writeup.

Usage:
    python -m scripts.run_baseline --domain qa --split eval
    python -m scripts.run_baseline --domain research --split eval

Writes the result to runs/baseline_<domain>_<split>_<timestamp>.json plus a
canonical runs/baseline_<domain>.json (overwritten each run).

Requires OPENAI_API_KEY in the environment unless --dry-run is passed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.baseline import baseline_config  # noqa: E402
from evals.qa.runner import run_qa_eval  # noqa: E402
from evals.research.runner import run_research_eval  # noqa: E402
from stem.dotenv import load_dotenv  # noqa: E402
from stem.llm_client import LLMClient  # noqa: E402

# Load .env early so OPENAI_API_KEY is available regardless of how this is
# launched (VSCode terminal, plain shell, CI). A real shell `export` still wins.
load_dotenv(ROOT / ".env")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--domain", choices=["qa", "research"], required=True)
    p.add_argument("--split", choices=["probe", "eval"], default="eval")
    p.add_argument("--model", default=os.environ.get("STEM_DEFAULT_MODEL", "gpt-4o-mini"))
    p.add_argument(
        "--judge-model",
        default=os.environ.get("STEM_JUDGE_MODEL", "gpt-4o-mini"),
        help="Model used for the research LLM judge (research domain only).",
    )
    p.add_argument(
        "--max-calls",
        type=int,
        default=400,
        help="Hard ceiling on agent LLM calls across all tasks in the split.",
    )
    p.add_argument(
        "--judge-max-calls",
        type=int,
        default=200,
        help="Separate budget for the research judge.",
    )
    p.add_argument(
        "--max-steps-per-task",
        type=int,
        default=12,
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip OPENAI_API_KEY check and exit without making any calls.",
    )
    args = p.parse_args()

    if args.dry_run:
        print("[dry-run] would run baseline on", args.domain, args.split, "with model", args.model)
        return 0

    if not os.environ.get("OPENAI_API_KEY"):
        print(
            "ERROR: OPENAI_API_KEY is not set in the environment.\n"
            "Set it (or copy .env.example to .env and edit) before running this script.",
            file=sys.stderr,
        )
        return 2

    ts = time.strftime("%Y%m%dT%H%M%S")
    log_dir = ROOT / "runs"
    log_dir.mkdir(parents=True, exist_ok=True)
    agent_log = log_dir / f"baseline_{args.domain}_{args.split}_{ts}_agent.jsonl"

    llm = LLMClient(
        max_calls=args.max_calls,
        log_path=agent_log,
        default_model=args.model,
    )

    cfg = baseline_config()

    if args.domain == "qa":
        task_set = ROOT / "evals" / "qa" / f"{args.split}_set.json"
        result = run_qa_eval(
            cfg, llm, task_set,
            max_steps_per_task=args.max_steps_per_task,
            on_task_complete=lambda r: print(f"  {r['task_id']}: score={r['score']} ({r.get('reason')})"),
        )
        out = result.to_dict()
        print(f"\nQA {args.split}: pass_rate={result.pass_rate:.3f} ({result.n_passed}/{result.n_tasks})")
        print(f"LLM stats: {result.llm_stats}")
    else:
        # Research uses a separate judge client so the judge's calls don't eat
        # into the agent's budget.
        judge_log = log_dir / f"baseline_{args.domain}_{args.split}_{ts}_judge.jsonl"
        judge = LLMClient(
            max_calls=args.judge_max_calls,
            log_path=judge_log,
            default_model=args.judge_model,
        )
        task_set = ROOT / "evals" / "research" / f"{args.split}_set.json"
        result = run_research_eval(
            cfg, llm, task_set,
            judge_llm=judge,
            judge_model=args.judge_model,
            max_steps_per_task=args.max_steps_per_task,
            on_task_complete=lambda r: print(f"  {r['task_id']}: correct={r['correct']} (urls={r['n_urls']})"),
        )
        out = result.to_dict()
        print(
            f"\nResearch {args.split}: accuracy={result.accuracy:.3f} ({result.n_correct}/{result.n_tasks}); "
            f"avg_citations={result.avg_citations:.2f}; citation_validity={result.citation_validity:.2f}"
        )
        print(f"Agent LLM: {result.llm_stats}")
        print(f"Judge LLM: {result.judge_stats}")

    canonical = log_dir / f"baseline_{args.domain}.json"
    timestamped = log_dir / f"baseline_{args.domain}_{args.split}_{ts}.json"
    with timestamped.open("w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, default=str)
    with canonical.open("w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nResults written to:\n  {timestamped}\n  {canonical}  (canonical)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
