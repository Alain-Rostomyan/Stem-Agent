"""Run sequential research eval pairs for variance/model comparisons.

This intentionally does not write canonical baseline files. It runs baseline
and evolved configs one after the other to avoid web-search starvation from
parallel DDG requests.

Examples:
    python -m scripts.sequential_research --pairs 4 --model gpt-4o-mini
    python -m scripts.sequential_research --pairs 1 --model gpt-5.1-mini
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
from agent.config import AgentConfig, load_config  # noqa: E402
from evals.research.runner import run_research_eval  # noqa: E402
from stem.dotenv import load_dotenv  # noqa: E402
from stem.llm_client import LLMClient  # noqa: E402


load_dotenv(ROOT / ".env")


def summarize(accs: list[float]) -> dict[str, float | int | None]:
    if not accs:
        return {"n": 0, "mean": None, "std": None, "min": None, "max": None}
    mean = sum(accs) / len(accs)
    var = sum((x - mean) ** 2 for x in accs) / len(accs)
    return {
        "n": len(accs),
        "mean": mean,
        "std": var ** 0.5,
        "min": min(accs),
        "max": max(accs),
    }


def run_one(
    *,
    label: str,
    cfg: AgentConfig,
    model: str,
    split: str,
    max_calls: int,
    judge_max_calls: int,
    max_steps_per_task: int,
    ts: str,
) -> dict:
    log_dir = ROOT / "runs"
    log_dir.mkdir(parents=True, exist_ok=True)
    agent_log = log_dir / f"seq_{label}_{model}_{split}_{ts}_agent.jsonl"
    judge_log = log_dir / f"seq_{label}_{model}_{split}_{ts}_judge.jsonl"
    out_path = log_dir / f"seq_{label}_{model}_{split}_{ts}.json"

    llm = LLMClient(max_calls=max_calls, log_path=agent_log, default_model=model)
    judge = LLMClient(max_calls=judge_max_calls, log_path=judge_log, default_model=model)
    task_set = ROOT / "evals" / "research" / f"{split}_set.json"

    print(f"\n[{time.strftime('%H:%M:%S')}] running {label} model={model}")
    result = run_research_eval(
        cfg,
        llm,
        task_set,
        judge_llm=judge,
        judge_model=model,
        max_steps_per_task=max_steps_per_task,
        on_task_complete=lambda r: print(
            f"  {r['task_id']}: correct={r['correct']} (urls={r['n_urls']})",
            flush=True,
        ),
    )
    out = result.to_dict()
    out["label"] = label
    out["model"] = model
    out["timestamp"] = ts
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, default=str)

    print(
        f"[{time.strftime('%H:%M:%S')}] {label}: "
        f"accuracy={result.accuracy:.3f} ({result.n_correct}/{result.n_tasks}); "
        f"agent_cost=${result.llm_stats.get('cost_usd', 0):.4f}; "
        f"judge_cost=${result.judge_stats.get('cost_usd', 0):.4f}",
        flush=True,
    )
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--pairs", type=int, default=1)
    p.add_argument("--model", default=os.environ.get("STEM_DEFAULT_MODEL", "gpt-4o-mini"))
    p.add_argument("--split", default="eval_v2", choices=["eval", "eval_v2"])
    p.add_argument("--max-calls", type=int, default=400)
    p.add_argument("--judge-max-calls", type=int, default=200)
    p.add_argument("--max-steps-per-task", type=int, default=12)
    p.add_argument("--sleep-s", type=float, default=30.0)
    args = p.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY is not set.", file=sys.stderr)
        return 2

    baseline = baseline_config()
    evolved = load_config(ROOT / "configs" / "research.json")
    started = time.strftime("%Y%m%dT%H%M%S")

    rows: list[dict] = []
    for i in range(args.pairs):
        seed = i + 1
        rows.append(run_one(
            label=f"baseline_seed{seed}",
            cfg=baseline,
            model=args.model,
            split=args.split,
            max_calls=args.max_calls,
            judge_max_calls=args.judge_max_calls,
            max_steps_per_task=args.max_steps_per_task,
            ts=started,
        ))
        if args.sleep_s:
            time.sleep(args.sleep_s)
        rows.append(run_one(
            label=f"evolved_seed{seed}",
            cfg=evolved,
            model=args.model,
            split=args.split,
            max_calls=args.max_calls,
            judge_max_calls=args.judge_max_calls,
            max_steps_per_task=args.max_steps_per_task,
            ts=started,
        ))
        if args.sleep_s and seed < args.pairs:
            time.sleep(args.sleep_s)

    by_cond = {
        "baseline": [r["accuracy"] for r in rows if str(r["label"]).startswith("baseline")],
        "evolved": [r["accuracy"] for r in rows if str(r["label"]).startswith("evolved")],
    }
    summary = {
        "model": args.model,
        "split": args.split,
        "pairs": args.pairs,
        "started": started,
        "conditions": {k: summarize(v) for k, v in by_cond.items()},
        "rows": [
            {
                "label": r["label"],
                "accuracy": r["accuracy"],
                "n_correct": r["n_correct"],
                "n_tasks": r["n_tasks"],
                "llm_stats": r["llm_stats"],
                "judge_stats": r["judge_stats"],
            }
            for r in rows
        ],
    }
    out_path = ROOT / "runs" / f"seq_research_summary_{args.model}_{args.split}_{started}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    print("\nsummary:")
    print(json.dumps(summary["conditions"], indent=2))
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
