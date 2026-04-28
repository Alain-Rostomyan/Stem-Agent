"""Phase 1 smoke test (offline).

Validates the eval infrastructure without any OpenAI calls:

  1. QA `score_task` with a hand-written correct test -> score 1.
  2. QA `score_task` with a vacuous test -> score 0 (tests_useless_or_broken).
  3. QA `score_task` with no test file -> score 0 (no_test_file).
  4. QA `run_qa_eval` end-to-end via a fake LLM that emits a write_file tool
     call for a correct test and then DONE -> pass_rate 1.0 over 1 task.
  5. Research `judge_answer` with a fake judge -> respects the JSON verdict.
  6. Research `run_research_eval` end-to-end with a fake agent + fake judge ->
     accuracy 1.0 over 1 task.
  7. URL extraction utility round-trips multiple URLs.
  8. CLI dry-run for both domains exits 0 without touching OpenAI.

Run with:  python phase1_smoke_test.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from agent.baseline import baseline_config  # noqa: E402
from agent.config import AgentConfig  # noqa: E402
from evals.qa.runner import (  # noqa: E402
    build_task_prompt as qa_build_prompt,
    load_task_set as qa_load_task_set,
    run_qa_eval,
    score_task,
)
from evals.research.runner import (  # noqa: E402
    build_task_prompt as rs_build_prompt,
    extract_urls,
    judge_answer,
    run_research_eval,
)
from stem.llm_client import LLMClient  # noqa: E402
from tools import registry  # noqa: E402


PASS = "[PASS]"
FAIL = "[FAIL]"


def _assert(cond: bool, msg: str) -> None:
    if cond:
        print(f"{PASS} {msg}")
    else:
        print(f"{FAIL} {msg}")
        raise AssertionError(msg)


def _ensure_starters() -> None:
    if not registry.all_tools():
        registry.load_starter_tools()


def _fake_chat(content=None, tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-fake",
        "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        "model": "gpt-4o-mini",
    }


# ------------------------------------------------------------------- QA tests

QA_TASKS = qa_load_task_set(ROOT / "evals" / "qa" / "probe_set.json")["tasks"]
QA_ADD_ONE = next(t for t in QA_TASKS if t["id"] == "qa_probe_01_add_one")


GOOD_TEST_FOR_ADD_ONE = (
    "from target import add_one\n"
    "\n"
    "def test_zero():\n"
    "    assert add_one(0) == 1\n"
    "\n"
    "def test_five():\n"
    "    assert add_one(5) == 6\n"
)


VACUOUS_TEST = (
    "def test_trivial():\n"
    "    assert True\n"
)


def test_qa_score_correct_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp)
        (wd / "test_target.py").write_text(GOOD_TEST_FOR_ADD_ONE, encoding="utf-8")
        result = score_task(QA_ADD_ONE, wd)
    _assert(result["score"] == 1, f"correct test scores 1 (reason={result['reason']})")


def test_qa_score_vacuous_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp)
        (wd / "test_target.py").write_text(VACUOUS_TEST, encoding="utf-8")
        result = score_task(QA_ADD_ONE, wd)
    _assert(result["score"] == 0, "vacuous test scores 0")
    # A vacuous `assert True` passes on both impls, so the runner classifies it
    # as 'tests_pass_on_buggy' (the buggy run did not fail). That's the correct
    # classification — the offending property is that the buggy impl wasn't caught.
    _assert(result["reason"] == "tests_pass_on_buggy",
            f"vacuous test reason='tests_pass_on_buggy' (got {result['reason']})")


def test_qa_score_no_test_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp)
        result = score_task(QA_ADD_ONE, wd)
    _assert(result["score"] == 0 and result["reason"] == "no_test_file",
            f"no-test-file scores 0 with reason no_test_file (got {result['reason']})")


def test_qa_runner_end_to_end() -> None:
    """Fake an agent that writes a correct test via write_file then returns DONE."""
    _ensure_starters()
    state = {"step": 0, "wrote": False}

    def fake_completion(messages, **kwargs):
        state["step"] += 1
        # Find the work_dir from the user prompt — it embeds the absolute path.
        user_msg = next(m for m in messages if m["role"] == "user")
        text = user_msg["content"]
        # Pull the absolute path to test_target.py out of the prompt. The path
        # has no whitespace in it (tempdir + 'test_target.py'), so \S+ works on
        # both forward- and backslashes.
        import re
        m = re.search(r"(\S+test_target\.py)", text)
        if not m:
            raise AssertionError("could not find test_target.py path in prompt")
        test_path = m.group(1)
        if state["step"] == 1:
            args = {"path": test_path, "content": GOOD_TEST_FOR_ADD_ONE}
            tool_calls = [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "write_file", "arguments": json.dumps(args)},
            }]
            state["wrote"] = True
            return _fake_chat(content=None, tool_calls=tool_calls)
        return _fake_chat(content="DONE")

    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "agent.jsonl"
        llm = LLMClient(max_calls=20, log_path=log_path, fake_completion=fake_completion)
        # One-task subset to keep this fast.
        single_task_path = Path(d) / "single.json"
        single_task_path.write_text(
            json.dumps({"domain": "qa", "split": "probe", "task_kind": "x",
                        "description": "", "tasks": [QA_ADD_ONE]}),
            encoding="utf-8",
        )
        result = run_qa_eval(baseline_config(), llm, single_task_path, max_steps_per_task=5)
    _assert(state["wrote"], "fake agent issued the write_file tool call")
    _assert(result.n_tasks == 1, "1 task ran")
    _assert(result.n_passed == 1, f"task scored 1 (got {result.n_passed}; per_task={result.per_task})")
    _assert(result.pass_rate == 1.0, f"pass_rate == 1.0 (got {result.pass_rate})")


# ------------------------------------------------------------- research tests

RS_TASK = {
    "id": "rs_t",
    "question": "What is the chemical symbol for gold?",
    "answer": "Au",
    "aliases": ["Au"],
}


def test_research_judge_correct() -> None:
    fake = lambda **_: _fake_chat(content=json.dumps({"correct": True, "rationale": "matches"}))
    judge_llm = LLMClient(max_calls=10, fake_completion=fake)
    verdict = judge_answer(judge_llm, RS_TASK, "The symbol is Au.")
    _assert(verdict["correct"] is True, "judge returns correct=True for matching answer")


def test_research_judge_incorrect() -> None:
    fake = lambda **_: _fake_chat(content=json.dumps({"correct": False, "rationale": "wrong element"}))
    judge_llm = LLMClient(max_calls=10, fake_completion=fake)
    verdict = judge_answer(judge_llm, RS_TASK, "The symbol is Ag.")
    _assert(verdict["correct"] is False, "judge returns correct=False for non-matching answer")


def test_research_judge_unparseable() -> None:
    fake = lambda **_: _fake_chat(content="not even close to JSON")
    judge_llm = LLMClient(max_calls=10, fake_completion=fake)
    verdict = judge_answer(judge_llm, RS_TASK, "Au")
    _assert(verdict["correct"] is False, "unparseable judge response defaults to correct=False")
    _assert("judge_unparseable" in verdict["rationale"], "rationale flags the parse failure")


def test_research_runner_end_to_end() -> None:
    _ensure_starters()
    state = {"step": 0}

    def fake_completion(messages, **kwargs):
        # Detect whether this is the agent or the judge by the system prompt.
        sys_msg = next((m for m in messages if m["role"] == "system"), None)
        is_judge = sys_msg is not None and sys_msg["content"].startswith("You are a strict but fair grader")
        if is_judge:
            return _fake_chat(content=json.dumps({"correct": True, "rationale": "matches canonical Au"}))
        state["step"] += 1
        # Agent path: just return a final answer with a URL.
        return _fake_chat(
            content="The chemical symbol for gold is Au.\nSource: https://example.com/au"
        )

    with tempfile.TemporaryDirectory() as d:
        agent_llm = LLMClient(max_calls=10, fake_completion=fake_completion,
                              log_path=Path(d) / "a.jsonl")
        judge_llm = LLMClient(max_calls=10, fake_completion=fake_completion,
                              log_path=Path(d) / "j.jsonl")
        single_path = Path(d) / "single.json"
        single_path.write_text(
            json.dumps({"domain": "research", "split": "probe", "task_kind": "x",
                        "description": "", "tasks": [RS_TASK]}),
            encoding="utf-8",
        )
        result = run_research_eval(
            baseline_config(), agent_llm, single_path,
            judge_llm=judge_llm,
            check_citations=False,  # don't actually hit the network
            max_steps_per_task=3,
        )
    _assert(result.n_tasks == 1, "1 task ran")
    _assert(result.n_correct == 1, f"correct=1 (got {result.n_correct}; per_task={result.per_task})")
    _assert(result.per_task[0]["n_urls"] == 1, "one URL was extracted from the answer")
    _assert(result.per_task[0]["urls"][0] == "https://example.com/au",
            f"URL extracted correctly (got {result.per_task[0]['urls']})")


def test_extract_urls() -> None:
    text = "See https://a.example/page1 and http://b.example/x?q=1, also (https://c.example/y)."
    urls = extract_urls(text)
    _assert(set(urls) == {
        "https://a.example/page1",
        "http://b.example/x?q=1",
        "https://c.example/y",
    }, f"extract_urls returns the three URLs (got {urls})")


# ------------------------------------------------------------------ CLI tests

def test_cli_dry_run() -> None:
    for domain in ("qa", "research"):
        proc = subprocess.run(
            [sys.executable, "-m", "scripts.run_baseline", "--domain", domain, "--dry-run"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        _assert(proc.returncode == 0, f"CLI dry-run for {domain} exits 0 (stderr={proc.stderr[:200]})")
        _assert("would run baseline" in proc.stdout, f"CLI dry-run prints intent for {domain}")


# -------------------------------------------------------------------- driver

def main() -> int:
    failures = 0
    suites = [
        ("qa.score_task: correct test -> 1", test_qa_score_correct_test),
        ("qa.score_task: vacuous test -> 0", test_qa_score_vacuous_test),
        ("qa.score_task: no test file -> 0", test_qa_score_no_test_file),
        ("qa.run_qa_eval end-to-end (fake LLM)", test_qa_runner_end_to_end),
        ("research.judge_answer: correct", test_research_judge_correct),
        ("research.judge_answer: incorrect", test_research_judge_incorrect),
        ("research.judge_answer: unparseable", test_research_judge_unparseable),
        ("research.run_research_eval end-to-end (fake LLM)", test_research_runner_end_to_end),
        ("research.extract_urls", test_extract_urls),
        ("scripts.run_baseline --dry-run", test_cli_dry_run),
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
    print(f"{PASS} all phase 1 smoke tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
