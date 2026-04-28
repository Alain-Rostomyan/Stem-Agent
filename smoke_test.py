"""Phase 0 smoke test.

Runs offline (no OpenAI calls) using the LLMClient's fake_completion seam.
Validates:

1. All starter tools register and round-trip through openai_tool_specs.
2. AgentConfig save/load/validate.
3. LLMClient logs calls and enforces max_calls (BudgetExceeded).
4. The runner's single_loop correctly: makes an LLM call, parses tool_calls,
   executes the tool, appends a tool message, terminates on a no-tool-calls
   response, and returns a final answer.
5. The baseline config exposes all starter tools.

Run with:  python smoke_test.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# Make sure the repo root is importable when running this directly.
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from agent.config import AgentConfig, default_config, load_config, save_config  # noqa: E402
from agent.baseline import baseline_config, run_baseline  # noqa: E402
from agent.runner import run_agent  # noqa: E402
from stem.llm_client import BudgetExceeded, LLMClient  # noqa: E402
from tools import registry  # noqa: E402


PASS = "[PASS]"
FAIL = "[FAIL]"


def _assert(cond: bool, msg: str) -> None:
    if cond:
        print(f"{PASS} {msg}")
    else:
        print(f"{FAIL} {msg}")
        raise AssertionError(msg)


# ---- 1. registry / starter tools --------------------------------------------

def test_registry_loads_starters() -> None:
    registry.reset()
    names = registry.load_starter_tools()
    expected = {
        "read_file", "write_file", "list_directory",
        "run_shell_command", "run_python", "web_search", "call_llm",
    }
    got = set(registry.all_tools().keys())
    _assert(expected.issubset(got), f"all 7 starter tools registered (got {sorted(got)})")
    specs = registry.openai_tool_specs(sorted(got))
    _assert(len(specs) == len(got), "openai_tool_specs returns one entry per tool")
    for s in specs:
        _assert("function" in s and "name" in s["function"], f"spec well-formed: {s.get('function', {}).get('name')}")
        params = s["function"]["parameters"]
        _assert(params["type"] == "object", "parameters schema is object")


# ---- 2. config schema -------------------------------------------------------

def test_config_roundtrip() -> None:
    cfg = default_config("qa")
    cfg.validate()
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "qa.json"
        save_config(cfg, path)
        _assert(path.exists(), "save_config writes file")
        loaded = load_config(path)
        _assert(loaded.to_dict() == cfg.to_dict(), "config round-trips through JSON")
    # Validation rejects bad architecture
    bad = AgentConfig(architecture="not_a_thing")  # type: ignore[arg-type]
    try:
        bad.validate()
        _assert(False, "validate should reject bad architecture")
    except ValueError:
        _assert(True, "validate rejects bad architecture")


# ---- 3. LLMClient budget + logging ------------------------------------------

def _fake_chat(content: str = "ok", tool_calls=None):
    """Return an OpenAI-shaped completion dict."""
    msg: dict = {"role": "assistant", "content": content}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-fake",
        "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "model": "gpt-4o-mini",
    }


def test_budget_and_logging() -> None:
    with tempfile.TemporaryDirectory() as d:
        log_path = Path(d) / "llm.jsonl"
        client = LLMClient(
            max_calls=2,
            log_path=log_path,
            default_model="gpt-4o-mini",
            fake_completion=lambda **_: _fake_chat("hello"),
        )
        client.complete([{"role": "user", "content": "hi"}])
        client.complete([{"role": "user", "content": "again"}])
        try:
            client.complete([{"role": "user", "content": "third"}])
            _assert(False, "third call should raise BudgetExceeded")
        except BudgetExceeded:
            _assert(True, "BudgetExceeded raised on 3rd call when max_calls=2")
        _assert(client.calls_made == 2, f"calls_made == 2 (got {client.calls_made})")
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        _assert(len(lines) == 2, f"log has 2 lines (got {len(lines)})")
        rec = json.loads(lines[0])
        _assert(rec["model"] == "gpt-4o-mini", "log record carries model")
        _assert(rec["prompt_tokens"] == 10 and rec["completion_tokens"] == 5,
                "log record carries token usage")


# ---- 4. runner end-to-end with a fake LLM -----------------------------------

def test_runner_end_to_end() -> None:
    """Mock an LLM that calls list_directory once, then returns a final answer."""
    registry.reset()
    registry.load_starter_tools()

    state = {"step": 0}
    target_dir = str(ROOT)  # any real directory

    def fake_completion(**kwargs):
        state["step"] += 1
        if state["step"] == 1:
            tool_calls = [{
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "list_directory",
                    "arguments": json.dumps({"path": target_dir}),
                },
            }]
            return _fake_chat(content=None, tool_calls=tool_calls)
        # Step 2: produce final answer
        return _fake_chat(content="DONE: I listed the directory.")

    with tempfile.TemporaryDirectory() as d:
        client = LLMClient(
            max_calls=10,
            log_path=Path(d) / "run.jsonl",
            default_model="gpt-4o-mini",
            fake_completion=fake_completion,
        )
        cfg = baseline_config()
        result = run_agent(cfg, client, "list the project root", max_steps=5)

    _assert(result.stopped_reason == "answer", f"stopped on answer (got {result.stopped_reason})")
    _assert(result.final_answer == "DONE: I listed the directory.", "final answer returned")
    _assert(len(result.steps) == 2, f"two model steps (got {len(result.steps)})")
    # Verify a tool message landed in the transcript.
    tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
    _assert(len(tool_msgs) == 1, f"one tool message in transcript (got {len(tool_msgs)})")
    content = tool_msgs[0]["content"]
    saw_expected = "smoke_test.py" in content or "stem_agent_briefing.md" in content
    if not saw_expected:
        print(f"    debug: tool message content: {content[:400]}")
    _assert(saw_expected, "tool actually ran (saw expected file in listing)")


# ---- 5. baseline_config sanity ----------------------------------------------

def test_baseline_config() -> None:
    cfg = baseline_config()
    cfg.validate()
    _assert(len(cfg.enabled_tools) >= 7, f"baseline enables all starter tools (got {len(cfg.enabled_tools)})")
    _assert(cfg.architecture == "single_loop", "baseline uses single_loop")


def main() -> int:
    failures = 0
    for name, fn in [
        ("registry / starter tools", test_registry_loads_starters),
        ("config round-trip", test_config_roundtrip),
        ("budget + logging", test_budget_and_logging),
        ("runner end-to-end", test_runner_end_to_end),
        ("baseline config", test_baseline_config),
    ]:
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
    print(f"{PASS} all smoke tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
