"""Print a side-by-side diff of two evolved configs for the writeup.

Briefing step 25: 'Diff configs/qa.json against configs/research.json. Use
this diff as a figure in the writeup ("same stem, different organisms").'

Usage:
    python -m scripts.config_diff configs/research.json configs/qa.json
    python -m scripts.config_diff configs/qa.json configs/qa_ablation.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(path: str | Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def truncate(s: str, n: int = 80) -> str:
    s = s.replace("\n", " ").strip()
    return s if len(s) <= n else s[:n - 1] + "…"


def render(cfg: dict, label: str) -> dict[str, str]:
    sys_prompt = cfg.get("system_prompt", "")
    return {
        "label": label,
        "enabled_tools": ", ".join(cfg.get("enabled_tools") or []) or "(none)",
        "custom_tools": ", ".join(t.get("tool_name", "?") for t in cfg.get("custom_tools") or []) or "(none)",
        "few_shot_count": str(len(cfg.get("few_shot_examples") or [])),
        "architecture": cfg.get("architecture", "?"),
        "system_prompt_len": str(len(sys_prompt)),
        "system_prompt_first_line": truncate(sys_prompt.splitlines()[0] if sys_prompt else "", 80),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("a", help="First config path (e.g. configs/research.json)")
    p.add_argument("b", help="Second config path (e.g. configs/qa.json)")
    args = p.parse_args()

    a = render(load(args.a), Path(args.a).stem)
    b = render(load(args.b), Path(args.b).stem)

    rows = [
        ("enabled_tools",           "tools currently turned on"),
        ("custom_tools",            "tools the stem authored"),
        ("few_shot_count",          "few-shot examples in config"),
        ("architecture",            "single_loop vs planner_executor"),
        ("system_prompt_len",       "characters in system prompt"),
        ("system_prompt_first_line","first line of system prompt"),
    ]

    label_w = max(len(a["label"]), len(b["label"]), 12)
    print(f"{'field':<28}  {a['label']:<{label_w}}  {b['label']:<{label_w}}")
    print("-" * (28 + 2 + label_w + 2 + label_w))
    for key, _desc in rows:
        va = a.get(key, "")
        vb = b.get(key, "")
        marker = " " if va == vb else "*"
        print(f"{marker} {key:<26}  {va!s:<{label_w}}  {vb!s:<{label_w}}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
