"""Stem stopping criterion.

Decides whether the evolution loop should halt. Two reasons to stop, per
the briefing:
  1. Probe-set score plateau: the spread of the last `plateau_window`
     scores is below `plateau_threshold` (default: max-min < 0.05 across
     the most recent 3 scores).
  2. LLM-call budget exhausted: total calls used >= budget_calls_limit.

Whichever condition fires first wins. Returns a (stop, reason) tuple
so the caller can log why the loop ended.
"""

from __future__ import annotations

from typing import Tuple


def should_stop(
    *,
    probe_history: list[float],
    plateau_window: int = 3,
    plateau_threshold: float = 0.05,
    budget_calls_used: int,
    budget_calls_limit: int,
) -> Tuple[bool, str]:
    """Return (stop, reason). reason is one of: 'budget', 'plateau', 'continue'.

    Budget is checked first — even if we've also plateaued, "budget" is the
    more honest reason to surface in the writeup ("we ran out") than
    "plateau" ("we converged"), and run_stem can't make more calls anyway.
    """
    if budget_calls_used >= budget_calls_limit:
        return True, "budget"

    if len(probe_history) >= plateau_window:
        recent = probe_history[-plateau_window:]
        if max(recent) - min(recent) < plateau_threshold:
            return True, "plateau"

    return False, "continue"
