"""Roll up per-call JSONL logs in runs/ into a project-wide cost summary.

Usage:
    python -m scripts.cost_summary

Walks runs/*.jsonl, aggregates by file (one per script invocation) and by
model, and prints totals. Flags any calls logged with cost_usd=None — those
models are missing from stem/llm_client.py:_PRICING and the local total
under-reports.

OpenAI's billing dashboard at platform.openai.com/usage is the authoritative
source for real spend. This script gives fast local feedback between runs.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _empty() -> dict[str, Any]:
    return {"calls": 0, "in": 0, "out": 0, "cost": 0.0, "untracked": 0}


def _add(agg: dict[str, Any], rec: dict[str, Any]) -> None:
    pin = int(rec.get("prompt_tokens", 0) or 0)
    pout = int(rec.get("completion_tokens", 0) or 0)
    cost = rec.get("cost_usd")
    agg["calls"] += 1
    agg["in"] += pin
    agg["out"] += pout
    if cost is None:
        agg["untracked"] += 1
    else:
        agg["cost"] += float(cost)


def _fmt_row(name: str, a: dict[str, Any]) -> str:
    cost = f"${a['cost']:>8.4f}"
    if a["untracked"]:
        cost += f"  (+{a['untracked']} untracked)"
    return f"  {name:<60} {a['calls']:>6} {a['in']:>10} {a['out']:>10}  {cost}"


def main() -> int:
    runs = ROOT / "runs"
    files = sorted(runs.glob("*.jsonl"))
    if not files:
        print("No JSONL logs found in runs/.")
        return 0

    by_file: dict[str, dict[str, Any]] = {}
    by_model: dict[str, dict[str, Any]] = defaultdict(_empty)
    grand: dict[str, Any] = _empty()

    for f in files:
        agg = _empty()
        with f.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                model = rec.get("model", "?")
                _add(agg, rec)
                _add(by_model[model], rec)
        by_file[f.name] = agg
        for k in ("calls", "in", "out", "untracked"):
            grand[k] += agg[k]
        grand["cost"] += agg["cost"]

    header = f"{'name':<62} {'calls':>6} {'in_tok':>10} {'out_tok':>10}  cost"
    bar = "-" * len(header)
    print()
    print(header)
    print(bar)
    print("\nBy file:")
    for fname, a in by_file.items():
        print(_fmt_row(fname, a))
    print("\nBy model:")
    for model, a in sorted(by_model.items()):
        print(_fmt_row(model, a))
    print()
    print("=" * len(header))
    print(_fmt_row("TOTAL", grand))

    if grand["untracked"]:
        print(
            f"\nWARNING: {grand['untracked']} call(s) logged with cost_usd=None — "
            "their model is missing from stem/llm_client.py:_PRICING and the local "
            "total under-reports. Add them and re-run."
        )
    print(
        "\nNote: platform.openai.com/usage is the authoritative cost source. "
        "Treat this script as fast local feedback only."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
