"""Launch K parallel research-domain seeds for variance bars.

Use case: the validation rerun showed gpt-4o-mini's research score
swings 27pt across two runs of the same config on the same eval. To
report a meaningful headline number we need mean ± std across
multiple seeds. This script launches K runs of (baseline) and K runs
of (evolved) against the v2 eval set in parallel and prints a small
summary at the end.

Usage:
    python -m scripts.multiseed_research --seeds 4
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def launch(label: str, args: list[str]) -> tuple[str, subprocess.Popen, Path]:
    log_path = ROOT / "runs" / f"multiseed_{label}_{time.strftime('%H%M%S')}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    f = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "scripts.run_baseline", *args],
        cwd=ROOT,
        stdout=f,
        stderr=subprocess.STDOUT,
        env=os.environ.copy(),
    )
    return label, proc, log_path


def parse_accuracy(log_path: Path) -> float | None:
    """Pull `accuracy=N.NNN` out of the log."""
    if not log_path.exists():
        return None
    text = log_path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if "accuracy=" in line:
            try:
                return float(line.split("accuracy=")[1].split(";")[0].split()[0])
            except (IndexError, ValueError):
                continue
    return None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--seeds", type=int, default=4,
                   help="Number of new seeds per condition (baseline / evolved). "
                        "Excludes the original v2 run which is already done.")
    p.add_argument("--split", default="eval_v2")
    args = p.parse_args()

    procs: list[tuple[str, subprocess.Popen, Path]] = []
    for i in range(args.seeds):
        procs.append(launch(
            f"research_baseline_seed{i + 2}",
            ["--domain", "research", "--split", args.split],
        ))
        procs.append(launch(
            f"research_evolved_seed{i + 2}",
            ["--domain", "research", "--split", args.split,
             "--config-path", "configs/research.json"],
        ))

    print(f"launched {len(procs)} seeds")
    for label, proc, log_path in procs:
        print(f"  {label}: pid={proc.pid} log={log_path.name}")

    print("\nwaiting for completion...")
    for label, proc, _ in procs:
        proc.wait()
        print(f"  {label}: rc={proc.returncode}")

    print("\nresults:")
    by_condition: dict[str, list[float]] = {}
    for label, _, log_path in procs:
        cond = "_".join(label.split("_")[:2])  # research_baseline | research_evolved
        acc = parse_accuracy(log_path)
        if acc is not None:
            by_condition.setdefault(cond, []).append(acc)
            print(f"  {label}: accuracy={acc:.3f}")
        else:
            print(f"  {label}: no accuracy parsed")

    for cond, accs in by_condition.items():
        n = len(accs)
        mean = sum(accs) / n
        var = sum((a - mean) ** 2 for a in accs) / n
        std = var ** 0.5
        print(f"\n  {cond}: n={n}, mean={mean:.3f}, std={std:.3f}, "
              f"min={min(accs):.3f}, max={max(accs):.3f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
