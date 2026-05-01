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
from typing import Optional

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
from stem.commit import (  # noqa: E402
    CommitOutcome,
    _format_commit_message,
    accept_proposal,
    reject_proposal,
)
from stem.stop_check import should_stop  # noqa: E402
from stem.test_proposal import (  # noqa: E402
    TestResult,
    apply_proposal_to_config,
    run_sanity_check,
    test_proposal,
)
from tools import registry  # noqa: E402


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


def _ensure_starters_loaded() -> None:
    if not registry.all_tools():
        registry.load_starter_tools()


def _make_proposal(action: str, details: dict, valid: bool = True) -> Proposal:
    return Proposal(
        action=action, rationale="r", details=details, valid=valid,
        validation_errors=[] if valid else ["test forced invalid"],
    )


# -------------------------------------------------- apply_proposal_to_config

def test_apply_modify_prompt() -> None:
    cfg = _baseline_cfg()
    p = _make_proposal("modify_prompt", {"new_system_prompt": "Be terse and structured."})
    new_cfg, written = apply_proposal_to_config(p, cfg, domain="research")
    _assert(new_cfg.system_prompt == "Be terse and structured.", "prompt replaced")
    _assert(cfg.system_prompt == "Generic prompt.", "original config not mutated")
    _assert(written == [], "modify_prompt has no filesystem side effects")


def test_apply_enable_tool() -> None:
    cfg = _baseline_cfg()
    p = _make_proposal("enable_tool", {"tool_name": "web_search"})
    new_cfg, written = apply_proposal_to_config(p, cfg, domain="research")
    _assert("web_search" in new_cfg.enabled_tools, "web_search added")
    _assert("web_search" not in cfg.enabled_tools, "original config unchanged")
    _assert(written == [], "enable_tool has no filesystem side effects")


def test_apply_write_tool_writes_file() -> None:
    cfg = _baseline_cfg()
    p = _make_proposal("write_tool", {
        "tool_name": "fetch_url", "description": "Fetch URL",
        "python_code": VALID_TOOL_CODE,
    })
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        new_cfg, written = apply_proposal_to_config(
            p, cfg, domain="research",
            custom_tools_root=Path(d),
        )
        path = Path(d) / "research" / "fetch_url.py"
        _assert(path.exists(), f"tool file written at {path}")
        _assert(path.read_text(encoding="utf-8") == VALID_TOOL_CODE, "tool file content matches")
        _assert(str(path) in new_cfg.custom_tools, "custom_tools updated")
        _assert("fetch_url" in new_cfg.enabled_tools, "tool also added to enabled_tools")
        _assert(written == [str(path)], "written list returned")


def test_apply_add_few_shot() -> None:
    cfg = _baseline_cfg()
    ex = {"task": "What is 2+2?", "reasoning": "add", "answer": "4"}
    p = _make_proposal("add_few_shot", {"example": ex})
    new_cfg, _ = apply_proposal_to_config(p, cfg, domain="research")
    _assert(new_cfg.few_shot_examples == [ex], "few-shot example appended")


def test_apply_switch_architecture() -> None:
    cfg = _baseline_cfg()  # single_loop
    p = _make_proposal("switch_architecture", {"new_architecture": "planner_executor"})
    new_cfg, _ = apply_proposal_to_config(p, cfg, domain="research")
    _assert(new_cfg.architecture == "planner_executor", "architecture switched")
    _assert(cfg.architecture == "single_loop", "original config unchanged")


def test_apply_invalid_proposal_raises() -> None:
    cfg = _baseline_cfg()
    p = _make_proposal("modify_prompt", {"new_system_prompt": "x"}, valid=False)
    raised = False
    try:
        apply_proposal_to_config(p, cfg, domain="research")
    except ValueError:
        raised = True
    _assert(raised, "apply_proposal_to_config raises on invalid proposal")


# ------------------------------------------------------------- sanity check

def test_sanity_check_passes_with_answer() -> None:
    _ensure_starters_loaded()
    cfg = _baseline_cfg()

    def fake_completion(messages, **kwargs):
        # Agent immediately produces a final answer (no tool calls).
        return _fake_chat(content="The answer is something.")

    llm = LLMClient(max_calls=10, fake_completion=fake_completion, default_model="gpt-4o-mini")
    ok, reason, ans = run_sanity_check(
        cfg, llm, {"task": "What is the chemical symbol for water?"}, max_steps=3,
    )
    _assert(ok, f"sanity passes when agent gives an answer (reason={reason})")
    _assert("something" in (ans or ""), f"sanity returns the agent's answer (got {ans!r})")


def test_sanity_check_fails_on_empty_answer() -> None:
    _ensure_starters_loaded()
    cfg = _baseline_cfg()

    def fake_completion(messages, **kwargs):
        return _fake_chat(content="")  # empty answer

    llm = LLMClient(max_calls=10, fake_completion=fake_completion, default_model="gpt-4o-mini")
    ok, reason, _ = run_sanity_check(cfg, llm, {"task": "x"}, max_steps=3)
    _assert(not ok, "sanity fails on empty answer")
    _assert("sanity_no_answer" in reason, f"reason flags no_answer (got {reason})")


# ------------------------------------------------------ test_proposal flows

def test_test_proposal_short_circuits_on_invalid() -> None:
    p = _make_proposal("modify_prompt", {"new_system_prompt": "x"}, valid=False)
    llm = LLMClient(max_calls=10, fake_completion=lambda **_: _fake_chat(content="y"))
    result = test_proposal(
        p, _baseline_cfg(),
        domain="research",
        probe_tasks_path=Path("/nonexistent.json"),
        sanity_task={"task": "x"},
        llm=llm,
    )
    _assert(isinstance(result, TestResult), "returns a TestResult")
    _assert(not result.sanity_passed, "sanity not run / not passed")
    _assert(result.sanity_reason == "proposal_invalid", "reason flags invalid proposal")
    _assert(llm.calls_made == 0, "no LLM calls made for invalid proposal")


def test_test_proposal_short_circuits_on_sanity_fail() -> None:
    """When sanity fails, the probe set is not run."""
    _ensure_starters_loaded()
    cfg = _baseline_cfg()
    p = _make_proposal("modify_prompt", {"new_system_prompt": "Be terse."})

    state = {"sanity_call": 0}

    def fake_completion(messages, **kwargs):
        # Always produce empty content to fail sanity.
        state["sanity_call"] += 1
        return _fake_chat(content="")

    llm = LLMClient(max_calls=20, fake_completion=fake_completion)
    result = test_proposal(
        p, cfg,
        domain="research",
        probe_tasks_path=Path("/nonexistent.json"),  # if probe ran, this would explode
        sanity_task={"task": "trivial"},
        llm=llm,
    )
    _assert(not result.sanity_passed, "sanity failed")
    _assert(result.probe_score == 0.0, "probe not run -> score 0")
    _assert(result.probe_per_task == [], "probe not run -> empty per_task")


# --------------------------------------------------- stop_check tests

def test_stop_check_budget() -> None:
    stop, reason = should_stop(
        probe_history=[0.1, 0.2, 0.3, 0.4, 0.5],  # would not plateau
        budget_calls_used=100, budget_calls_limit=100,
    )
    _assert(stop and reason == "budget", f"budget exhausted -> stop=budget (got {stop},{reason})")


def test_stop_check_plateau() -> None:
    stop, reason = should_stop(
        probe_history=[0.20, 0.40, 0.42, 0.43, 0.44],  # last 3 within 0.05
        budget_calls_used=10, budget_calls_limit=100,
    )
    _assert(stop and reason == "plateau", f"plateau detected (got {stop},{reason})")


def test_stop_check_continue_short_history() -> None:
    stop, reason = should_stop(
        probe_history=[0.1, 0.1],  # only 2 scores, not enough for window=3
        budget_calls_used=10, budget_calls_limit=100,
    )
    _assert(not stop and reason == "continue",
            f"too few scores -> continue (got {stop},{reason})")


def test_stop_check_continue_still_improving() -> None:
    stop, reason = should_stop(
        probe_history=[0.20, 0.30, 0.45],  # spread 0.25, still improving
        budget_calls_used=10, budget_calls_limit=100,
    )
    _assert(not stop and reason == "continue",
            f"still improving -> continue (got {stop},{reason})")


def test_stop_check_budget_beats_plateau() -> None:
    stop, reason = should_stop(
        probe_history=[0.4, 0.4, 0.4],  # plateau true
        budget_calls_used=100, budget_calls_limit=100,  # also out of budget
    )
    _assert(stop and reason == "budget",
            f"budget reported before plateau (got {stop},{reason})")


# --------------------------------------------------- commit tests

def _make_test_result(
    action: str = "modify_prompt",
    details: Optional[dict] = None,
    sanity_passed: bool = True,
    written_files: Optional[list[str]] = None,
    probe_score: float = 0.4,
) -> TestResult:
    cfg = _baseline_cfg()
    if action == "modify_prompt":
        details = details or {"new_system_prompt": "Be terse."}
        cfg.system_prompt = details["new_system_prompt"]
    elif action == "write_tool":
        details = details or {"tool_name": "fetch_url",
                              "description": "Fetch URL",
                              "python_code": VALID_TOOL_CODE}
        cfg.custom_tools = list(cfg.custom_tools) + (written_files or [])
    p = Proposal(action=action, rationale="x", details=details or {}, valid=True)
    return TestResult(
        proposal=p, candidate_config=cfg,
        sanity_passed=sanity_passed, sanity_reason="ok" if sanity_passed else "no_answer",
        sanity_answer="OK" if sanity_passed else None,
        probe_score=probe_score,
        probe_per_task=[],
        written_files=written_files or [],
    )


def _setup_temp_repo(tmp: Path) -> Path:
    """Create a fresh git repo with one initial commit."""
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=str(tmp), check=True)
    # Set local user.email/name so commits succeed without global config.
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(tmp), check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=str(tmp), check=True)
    (tmp / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(tmp), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=str(tmp), check=True)
    return tmp


def test_commit_accept_creates_commit() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        repo = _setup_temp_repo(Path(d))
        cfg_path = repo / "configs" / "research.json"
        tr = _make_test_result()
        outcome = accept_proposal(tr, step_n=1, config_path=cfg_path, repo_root=repo)
        _assert(outcome.accepted, f"accept reports accepted=True (error={outcome.error})")
        _assert(outcome.commit_hash and len(outcome.commit_hash) >= 7,
                f"commit hash returned (got {outcome.commit_hash!r})")
        _assert(cfg_path.exists(), "config file written")
        # Verify the commit is real.
        import subprocess
        msg = subprocess.run(
            ["git", "log", "-1", "--format=%s%n%b"],
            cwd=str(repo), capture_output=True, text=True, check=True,
        ).stdout
        _assert("evolution step 1: modify_prompt" in msg,
                f"commit message contains action header (got {msg[:200]!r})")


def test_commit_accept_with_written_files() -> None:
    """write_tool action: written_files get added alongside the config."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        repo = _setup_temp_repo(Path(d))
        # Pretend test_proposal already wrote the tool file.
        tool_dir = repo / "tools" / "custom" / "research"
        tool_dir.mkdir(parents=True)
        tool_path = tool_dir / "fetch_url.py"
        tool_path.write_text(VALID_TOOL_CODE, encoding="utf-8")

        tr = _make_test_result(action="write_tool", written_files=[str(tool_path)])
        cfg_path = repo / "configs" / "research.json"
        outcome = accept_proposal(tr, step_n=2, config_path=cfg_path, repo_root=repo)
        _assert(outcome.accepted, f"accepted (error={outcome.error})")

        import subprocess
        files = subprocess.run(
            ["git", "show", "--name-only", "--format=", "HEAD"],
            cwd=str(repo), capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        _assert(any("fetch_url.py" in f for f in files),
                f"tool file in commit (files={files})")
        _assert(any("research.json" in f for f in files),
                f"config in commit (files={files})")


def test_commit_reject_removes_written_files() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        repo = _setup_temp_repo(Path(d))
        tool_dir = repo / "tools" / "custom" / "research"
        tool_dir.mkdir(parents=True)
        tool_path = tool_dir / "broken.py"
        tool_path.write_text("x = 1\n", encoding="utf-8")

        tr = _make_test_result(action="write_tool", written_files=[str(tool_path)],
                               sanity_passed=False, probe_score=0.0)
        outcome = reject_proposal(tr, repo_root=repo)
        _assert(not outcome.accepted, "reject reports accepted=False")
        _assert(not tool_path.exists(), f"written file deleted (still exists at {tool_path})")


def test_commit_accept_refuses_failed_sanity() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        repo = _setup_temp_repo(Path(d))
        cfg_path = repo / "configs" / "research.json"
        tr = _make_test_result(sanity_passed=False)
        outcome = accept_proposal(tr, step_n=1, config_path=cfg_path, repo_root=repo)
        _assert(not outcome.accepted, "accept refuses failed-sanity proposal")
        _assert("sanity" in (outcome.error or "").lower(),
                f"error mentions sanity (got {outcome.error!r})")


def test_commit_format_message_includes_score_and_action() -> None:
    tr = _make_test_result(probe_score=0.467)
    msg = _format_commit_message(tr, step_n=3)
    _assert(msg.startswith("evolution step 3: modify_prompt"),
            f"header line correct (got {msg.splitlines()[0]!r})")
    _assert("probe_score: 0.467" in msg, "probe score rendered")
    _assert("rationale:" in msg, "rationale section present")


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
        ("test_proposal.apply: modify_prompt", test_apply_modify_prompt),
        ("test_proposal.apply: enable_tool", test_apply_enable_tool),
        ("test_proposal.apply: write_tool writes file", test_apply_write_tool_writes_file),
        ("test_proposal.apply: add_few_shot", test_apply_add_few_shot),
        ("test_proposal.apply: switch_architecture", test_apply_switch_architecture),
        ("test_proposal.apply: invalid proposal raises", test_apply_invalid_proposal_raises),
        ("test_proposal.sanity: passes on answer", test_sanity_check_passes_with_answer),
        ("test_proposal.sanity: fails on empty answer", test_sanity_check_fails_on_empty_answer),
        ("test_proposal.test_proposal: short-circuits on invalid", test_test_proposal_short_circuits_on_invalid),
        ("test_proposal.test_proposal: short-circuits on sanity fail", test_test_proposal_short_circuits_on_sanity_fail),
        ("stop_check: budget exhausted", test_stop_check_budget),
        ("stop_check: plateau detected", test_stop_check_plateau),
        ("stop_check: short history -> continue", test_stop_check_continue_short_history),
        ("stop_check: still improving -> continue", test_stop_check_continue_still_improving),
        ("stop_check: budget reported before plateau", test_stop_check_budget_beats_plateau),
        ("commit.accept: creates commit", test_commit_accept_creates_commit),
        ("commit.accept: includes written files", test_commit_accept_with_written_files),
        ("commit.reject: removes written files", test_commit_reject_removes_written_files),
        ("commit.accept: refuses failed-sanity", test_commit_accept_refuses_failed_sanity),
        ("commit.format_message: includes score+action", test_commit_format_message_includes_score_and_action),
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
