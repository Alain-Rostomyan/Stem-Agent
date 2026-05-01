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

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from agent.config import AgentConfig  # noqa: E402
from stem.investigate import (  # noqa: E402
    InvestigationResult,
    build_user_prompt,
    investigate,
    summarize_trace,
)
from stem.llm_client import LLMClient  # noqa: E402
from stem.propose import (  # noqa: E402
    Proposal,
    build_user_prompt as build_propose_prompt,
    propose,
    validate_proposal,
)


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


# ------------------------------------------------------------- propose tests

STARTER_TOOL_NAMES = [
    "read_file", "write_file", "list_directory", "run_shell_command",
    "run_python", "web_search", "call_llm",
]


def _baseline_cfg() -> AgentConfig:
    return AgentConfig(
        system_prompt="Generic prompt.",
        enabled_tools=["read_file", "list_directory", "run_python", "call_llm"],
        custom_tools=[],
        few_shot_examples=[],
        architecture="single_loop",
        domain_metadata={"name": "research", "notes": ""},
    )


VALID_TOOL_CODE = (
    "from tools.registry import tool\n"
    "import urllib.request\n"
    "\n"
    "@tool\n"
    "def fetch_url(url: str) -> str:\n"
    "    \"\"\"Fetch the text content of a URL.\"\"\"\n"
    "    try:\n"
    "        with urllib.request.urlopen(url, timeout=10) as resp:\n"
    "            return resp.read().decode('utf-8', errors='replace')\n"
    "    except Exception as e:\n"
    "        return f'fetch_url error: {e}'\n"
)


def test_validate_modify_prompt_ok() -> None:
    errs = validate_proposal(
        {"action": "modify_prompt", "rationale": "addresses search loops",
         "details": {"new_system_prompt": "Be more disciplined. Stop searching once you have data."}},
        current_config=_baseline_cfg(), starter_tool_names=STARTER_TOOL_NAMES,
    )
    _assert(errs == [], f"valid modify_prompt has no errors (got {errs})")


def test_validate_modify_prompt_unchanged_rejected() -> None:
    cfg = _baseline_cfg()
    errs = validate_proposal(
        {"action": "modify_prompt", "rationale": "x",
         "details": {"new_system_prompt": cfg.system_prompt}},
        current_config=cfg, starter_tool_names=STARTER_TOOL_NAMES,
    )
    _assert(any("identical to the current prompt" in e for e in errs),
            f"identical-prompt rejected (got {errs})")


def test_validate_enable_tool_already_enabled_rejected() -> None:
    errs = validate_proposal(
        {"action": "enable_tool", "rationale": "x",
         "details": {"tool_name": "read_file"}},  # already enabled
        current_config=_baseline_cfg(), starter_tool_names=STARTER_TOOL_NAMES,
    )
    _assert(any("already enabled" in e for e in errs), f"already-enabled rejected (got {errs})")


def test_validate_enable_tool_ok() -> None:
    errs = validate_proposal(
        {"action": "enable_tool", "rationale": "needed for fetching pages",
         "details": {"tool_name": "web_search"}},  # not in baseline enabled set
        current_config=_baseline_cfg(), starter_tool_names=STARTER_TOOL_NAMES,
    )
    _assert(errs == [], f"valid enable_tool has no errors (got {errs})")


def test_validate_write_tool_ok() -> None:
    errs = validate_proposal(
        {"action": "write_tool", "rationale": "fetch_url is the missing affordance",
         "details": {"tool_name": "fetch_url", "description": "Fetch URL contents",
                     "python_code": VALID_TOOL_CODE}},
        current_config=_baseline_cfg(), starter_tool_names=STARTER_TOOL_NAMES,
    )
    _assert(errs == [], f"valid write_tool has no errors (got {errs})")


def test_validate_write_tool_missing_decorator_rejected() -> None:
    bad = VALID_TOOL_CODE.replace("@tool\n", "")
    errs = validate_proposal(
        {"action": "write_tool", "rationale": "x",
         "details": {"tool_name": "fetch_url", "description": "x", "python_code": bad}},
        current_config=_baseline_cfg(), starter_tool_names=STARTER_TOOL_NAMES,
    )
    _assert(any("@tool" in e for e in errs), f"missing-@tool rejected (got {errs})")


def test_validate_add_few_shot_ok() -> None:
    errs = validate_proposal(
        {"action": "add_few_shot", "rationale": "demo of list-then-count",
         "details": {"example": {"task": "Of 27 EU members, how many use the Euro?",
                                 "reasoning": "List EU, list eurozone, intersect, count.",
                                 "answer": "20"}}},
        current_config=_baseline_cfg(), starter_tool_names=STARTER_TOOL_NAMES,
    )
    _assert(errs == [], f"valid add_few_shot has no errors (got {errs})")


def test_validate_switch_architecture_same_rejected() -> None:
    errs = validate_proposal(
        {"action": "switch_architecture", "rationale": "x",
         "details": {"new_architecture": "single_loop"}},  # same as baseline
        current_config=_baseline_cfg(), starter_tool_names=STARTER_TOOL_NAMES,
    )
    _assert(any("matches current" in e for e in errs), f"same-arch rejected (got {errs})")


def test_validate_unknown_action_rejected() -> None:
    errs = validate_proposal(
        {"action": "delete_everything", "rationale": "x", "details": {}},
        current_config=_baseline_cfg(), starter_tool_names=STARTER_TOOL_NAMES,
    )
    _assert(any("action must be one of" in e for e in errs),
            f"unknown action rejected (got {errs})")


def test_propose_end_to_end_offline() -> None:
    """Fake LLM returns a valid write_tool proposal — propose() returns valid=True."""
    response = json.dumps({
        "action": "write_tool",
        "rationale": "Investigation flagged fetch_url as the missing tool.",
        "details": {
            "tool_name": "fetch_url",
            "description": "Fetch text content of a URL",
            "python_code": VALID_TOOL_CODE,
        },
    })

    def fake_completion(messages, **kwargs):
        return _fake_chat(content=response)

    llm = LLMClient(max_calls=2, fake_completion=fake_completion, default_model="gpt-5.1")
    p = propose(
        domain="research",
        current_config=_baseline_cfg(),
        analysis="Failure modes include search loops and missing fetch_url.",
        recent_probe_scores=[0.2, 0.2, 0.2],
        starter_tool_names=STARTER_TOOL_NAMES,
        llm=llm,
        model="gpt-5.1",
    )
    _assert(isinstance(p, Proposal), "returns a Proposal")
    _assert(p.valid, f"valid (errors={p.validation_errors})")
    _assert(p.action == "write_tool", f"action carried through (got {p.action})")
    _assert(p.details["tool_name"] == "fetch_url", "details carried through")
    _assert(llm.calls_made == 1, "exactly one LLM call")


def test_propose_unparseable_response() -> None:
    """LLM returns garbage — propose() returns valid=False with json_decode_error."""
    def fake_completion(messages, **kwargs):
        return _fake_chat(content="this is not JSON at all")

    llm = LLMClient(max_calls=2, fake_completion=fake_completion, default_model="gpt-5.1")
    p = propose(
        domain="research",
        current_config=_baseline_cfg(),
        analysis="x", recent_probe_scores=[],
        starter_tool_names=STARTER_TOOL_NAMES, llm=llm,
    )
    _assert(not p.valid, "unparseable response yields valid=False")
    _assert(p.action == "(unparseable)", f"action sentinel set (got {p.action})")
    _assert(any("json_decode_error" in e for e in p.validation_errors),
            f"json_decode_error in validation_errors (got {p.validation_errors})")


def test_propose_user_prompt_shows_not_yet_enabled() -> None:
    cfg = _baseline_cfg()  # has read_file, list_directory, run_python, call_llm
    prompt = build_propose_prompt(
        domain="research", current_config=cfg,
        analysis="some analysis",
        recent_probe_scores=[0.2, 0.3],
        starter_tool_names=STARTER_TOOL_NAMES,
    )
    _assert("web_search" in prompt, "starter tool not yet enabled is listed")
    _assert("read_file" not in prompt.split("Starter tools NOT")[1].split("##")[0],
            "already-enabled tool is not listed in the not-yet-enabled section")


def main() -> int:
    failures = 0
    suites = [
        ("investigate.summarize_trace: failed-no-answer", test_summarize_trace_failed),
        ("investigate.summarize_trace: wrong-answer", test_summarize_trace_wrong),
        ("investigate.build_user_prompt: with traces", test_build_user_prompt_includes_traces),
        ("investigate.build_user_prompt: without traces", test_build_user_prompt_omits_traces_when_none),
        ("investigate.investigate: end-to-end (fake LLM)", test_investigate_end_to_end_offline),
        ("propose.validate: modify_prompt ok", test_validate_modify_prompt_ok),
        ("propose.validate: modify_prompt unchanged rejected", test_validate_modify_prompt_unchanged_rejected),
        ("propose.validate: enable_tool already enabled rejected", test_validate_enable_tool_already_enabled_rejected),
        ("propose.validate: enable_tool ok", test_validate_enable_tool_ok),
        ("propose.validate: write_tool ok", test_validate_write_tool_ok),
        ("propose.validate: write_tool missing @tool rejected", test_validate_write_tool_missing_decorator_rejected),
        ("propose.validate: add_few_shot ok", test_validate_add_few_shot_ok),
        ("propose.validate: switch_architecture same rejected", test_validate_switch_architecture_same_rejected),
        ("propose.validate: unknown action rejected", test_validate_unknown_action_rejected),
        ("propose.propose: end-to-end (fake LLM, write_tool)", test_propose_end_to_end_offline),
        ("propose.propose: unparseable response", test_propose_unparseable_response),
        ("propose.build_user_prompt: not-yet-enabled section", test_propose_user_prompt_shows_not_yet_enabled),
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
