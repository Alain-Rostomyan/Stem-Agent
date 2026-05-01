"""Phase 2 smoke test (offline).

Validates stem-side modules without any OpenAI calls. Mirrors the
phase1_smoke_test.py pattern — fake LLM client, end-to-end exercise of the
public surface, asserts on the structured result.

Currently covers:
  1. stem.investigate.investigate happy path: builds the right user prompt,
     forwards to the LLM, returns an InvestigationResult populated correctly.
  2. stem.investigate.summarize_trace: compresses a per-task record into the
     expected one-block summary.
  3. stem.investigate.build_user_prompt: includes the traces section iff
     baseline_traces is non-empty.

Run with:  python phase2_smoke_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from stem.investigate import (  # noqa: E402
    InvestigationResult,
    build_user_prompt,
    investigate,
    summarize_trace,
)
from stem.llm_client import LLMClient  # noqa: E402


PASS = "[PASS]"
FAIL = "[FAIL]"


def _assert(cond: bool, msg: str) -> None:
    if cond:
        print(f"{PASS} {msg}")
    else:
        print(f"{FAIL} {msg}")
        raise AssertionError(msg)


def _fake_chat(content: str):
    return {
        "id": "chatcmpl-fake",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        "model": "gpt-5.1",
    }


SAMPLE_TASKS = [
    {"id": "t1", "shape": "agg", "question": "Of the 27 EU members, how many use the Euro?"},
    {"id": "t2", "shape": "filter", "question": "Of the 14 Russia-border countries, how many are EU?"},
]

SAMPLE_TRACE_FAILED = {
    "task_id": "t1",
    "question": "Of the 27 EU members, how many use the Euro?",
    "canonical": "20",
    "candidate": "",
    "correct": False,
    "agent_steps": 12,
    "agent_stopped": "max_steps",
    "judge_rationale": "no answer produced",
}

SAMPLE_TRACE_WRONG = {
    "task_id": "t9",
    "question": "Of 12 NATO founders, how many were monarchies?",
    "canonical": "7",
    "candidate": "Three: UK, Belgium, Norway.",
    "correct": False,
    "agent_steps": 6,
    "agent_stopped": "answer",
    "judge_rationale": "Says 3, canonical 7",
}

STARTER_TOOLS = [
    ("read_file", "Read the contents of a local file."),
    ("web_search", "Search the web for a query and return snippets."),
    ("run_python", "Execute Python code and return stdout."),
]


def test_summarize_trace_failed() -> None:
    summary = summarize_trace(SAMPLE_TRACE_FAILED, max_steps=12)
    _assert("task_id: t1" in summary, "summary includes task_id")
    _assert("agent_answer: (none" in summary,
            "missing candidate answer is rendered as '(none ...)'")
    _assert("12/12" in summary, "step ratio rendered")
    _assert("max_steps" in summary, "stop reason rendered")


def test_summarize_trace_wrong() -> None:
    summary = summarize_trace(SAMPLE_TRACE_WRONG, max_steps=12)
    _assert("agent_answer: Three: UK" in summary,
            "candidate answer rendered when present")
    _assert("judge: Says 3" in summary, "judge rationale rendered")


def test_build_user_prompt_includes_traces() -> None:
    with_traces = build_user_prompt(
        domain="research",
        domain_description="open-domain QA",
        sample_tasks=SAMPLE_TASKS,
        starter_tools=STARTER_TOOLS,
        baseline_traces=[SAMPLE_TRACE_FAILED, SAMPLE_TRACE_WRONG],
    )
    _assert("Baseline traces" in with_traces,
            "with traces: section header present")
    _assert("Trace 1" in with_traces and "Trace 2" in with_traces,
            "both traces rendered")


def test_build_user_prompt_omits_traces_when_none() -> None:
    without = build_user_prompt(
        domain="research",
        domain_description="open-domain QA",
        sample_tasks=SAMPLE_TASKS,
        starter_tools=STARTER_TOOLS,
        baseline_traces=None,
    )
    _assert("Baseline traces" not in without,
            "no traces: section header omitted")
    _assert("[shape: agg]" in without,
            "shape tag rendered alongside task")


def test_investigate_end_to_end_offline() -> None:
    captured = {"messages": None, "model": None}

    def fake_completion(messages, **kwargs):
        captured["messages"] = messages
        captured["model"] = kwargs.get("model")
        return _fake_chat(content="## Domain characterization\nfake analysis body")

    llm = LLMClient(max_calls=2, fake_completion=fake_completion, default_model="gpt-5.1")

    result = investigate(
        domain="research",
        domain_description="QA with citations",
        sample_tasks=SAMPLE_TASKS,
        starter_tools=STARTER_TOOLS,
        llm=llm,
        baseline_traces=[SAMPLE_TRACE_FAILED, SAMPLE_TRACE_WRONG],
        model="gpt-5.1",
    )
    _assert(isinstance(result, InvestigationResult), "returns an InvestigationResult")
    _assert(result.analysis.startswith("## Domain"), "analysis comes from LLM content")
    _assert(result.domain == "research", "domain echoed")
    _assert(result.n_sample_tasks == 2, "n_sample_tasks counted")
    _assert(result.n_baseline_traces == 2, "n_baseline_traces counted")
    _assert(captured["model"] == "gpt-5.1", "model forwarded to llm.complete")

    msgs = captured["messages"]
    _assert(msgs[0]["role"] == "system", "system message first")
    _assert(msgs[1]["role"] == "user", "user message second")
    _assert("research" in msgs[1]["content"], "domain name in user prompt")
    _assert("Baseline traces" in msgs[1]["content"], "traces section present")
    _assert(llm.calls_made == 1, "investigate makes exactly one LLM call")


def main() -> int:
    failures = 0
    suites = [
        ("investigate.summarize_trace: failed-no-answer", test_summarize_trace_failed),
        ("investigate.summarize_trace: wrong-answer", test_summarize_trace_wrong),
        ("investigate.build_user_prompt: with traces", test_build_user_prompt_includes_traces),
        ("investigate.build_user_prompt: without traces", test_build_user_prompt_omits_traces_when_none),
        ("investigate.investigate: end-to-end (fake LLM)", test_investigate_end_to_end_offline),
    ]
    for name, fn in suites:
        print(f"\n--- {name} ---")
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"{FAIL} {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"{FAIL} {name}: {type(e).__name__}: {e}")
    print()
    if failures:
        print(f"{FAIL} {failures} suite(s) failed")
        return 1
    print(f"{PASS} all phase 2 smoke tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
