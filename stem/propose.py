"""Stem proposal phase.

One LLM call that, given the current agent config + the investigation analysis +
recent probe-set history, proposes ONE concrete change to make next. The output
is a structured JSON object `{action, rationale, details}` that downstream
modules (test_proposal, commit) apply, score, and either land or roll back.

Action menu (briefing-locked):
  - modify_prompt       — replace the agent's system prompt
  - enable_tool         — turn on a starter tool that isn't currently enabled
  - write_tool          — emit the full Python source for a new @tool function
                          (saved under tools/custom/<domain>/<name>.py and
                          appended to custom_tools)
  - add_few_shot        — append one few-shot example to the config
  - switch_architecture — toggle between single_loop and planner_executor

The schema is strictly enforced post-LLM by `validate_proposal`. Invalid
proposals are surfaced to the caller as a flag on the Proposal object — they
don't raise — so the stem loop can record them as rejected-without-retry and
move on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.config import VALID_ARCHITECTURES, AgentConfig
from stem.llm_client import LLMClient


ACTION_TYPES = (
    "modify_prompt",
    "enable_tool",
    "write_tool",
    "add_few_shot",
    "switch_architecture",
)


SYSTEM_PROMPT = (
    "You are an agent designer. You receive the current configuration of a "
    "specialized agent, an analysis of the problem domain, and the agent's "
    "recent score on a small probe set. Propose ONE concrete change that is "
    "most likely to improve the agent's probe-set score on the next iteration.\n\n"
    "Choose exactly one action from this menu:\n"
    "  - modify_prompt:       replace the system prompt with a new one.\n"
    "  - enable_tool:         turn on a starter tool the agent doesn't currently have.\n"
    "  - write_tool:          author a brand-new custom tool in Python.\n"
    "  - add_few_shot:        append a worked-example demonstration.\n"
    "  - switch_architecture: toggle between single_loop and planner_executor.\n\n"
    "Decision guidance:\n"
    "  - Read the analysis carefully — it lists concrete affordances. Pick the\n"
    "    one most likely to address the dominant failure mode it surfaces.\n"
    "  - Look at what's already been tried (visible in current_config). If the\n"
    "    last few iterations all touched the system prompt and the score is\n"
    "    plateauing, switch to a different action type (write a tool, add a\n"
    "    few-shot, or change architecture).\n"
    "  - Prefer high-leverage changes. Writing a missing tool that the analysis\n"
    "    flags as load-bearing is usually higher-leverage than yet another\n"
    "    prompt tweak.\n\n"
    "Output a single JSON object with exactly these top-level fields and no others:\n"
    '  {\n'
    '    "action": "<one of the menu values>",\n'
    '    "rationale": "<one short paragraph explaining why this change addresses '
    'an observed failure or analysis-recommended affordance>",\n'
    '    "details": { <action-specific fields, see below> }\n'
    '  }\n\n'
    "Required `details` fields per action:\n"
    "  - modify_prompt:       {\"new_system_prompt\": \"<full replacement prompt>\"}\n"
    "  - enable_tool:         {\"tool_name\": \"<name of a starter tool not currently enabled>\"}\n"
    "  - write_tool:          {\"tool_name\": \"<snake_case name>\",\n"
    "                          \"description\": \"<one-line description>\",\n"
    "                          \"python_code\": \"<complete Python source for one\n"
    "                                            file: imports, the @tool-decorated\n"
    "                                            function with type hints and a\n"
    "                                            docstring, no main block>\"}\n"
    "  - add_few_shot:        {\"example\": {\"task\": \"<task statement>\",\n"
    "                                       \"reasoning\": \"<step-by-step worked solution>\",\n"
    "                                       \"answer\": \"<final answer in the\n"
    "                                                    expected output format>\"}}\n"
    "  - switch_architecture: {\"new_architecture\": \"single_loop\" | \"planner_executor\"}\n\n"
    "Tool-authoring rules (when action == write_tool):\n"
    "  - Import `tool` from `tools.registry`. Decorate one function with @tool.\n"
    "  - Use Python type hints on every parameter (the registry derives the\n"
    "    JSON schema from them).\n"
    "  - Return a string (or a JSON-serializable value that the agent harness\n"
    "    will str() before showing to the LLM).\n"
    "  - No top-level side effects, no `if __name__ == '__main__'` blocks.\n"
    "  - Use only the Python standard library and `requests` (already installed).\n"
    "    No `pip install` and no other third-party imports.\n"
    "  - Defensive: catch exceptions and return an informative error string\n"
    "    rather than raising; the agent should be able to recover.\n\n"
    "Return ONLY the JSON object. No prose before or after."
)


@dataclass
class Proposal:
    action: str
    rationale: str
    details: dict[str, Any]
    valid: bool
    validation_errors: list[str] = field(default_factory=list)
    raw_response: dict[str, Any] = field(default_factory=dict, repr=False)
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "rationale": self.rationale,
            "details": self.details,
            "valid": self.valid,
            "validation_errors": list(self.validation_errors),
            "model": self.model,
        }


def _format_recent_scores(history: list[float]) -> str:
    if not history:
        return "(no probe runs yet — this is the first proposal)"
    pairs = [f"iter -{len(history) - 1 - i}: {s:.3f}" for i, s in enumerate(history)]
    return "  " + "\n  ".join(pairs)


def _format_current_config(cfg: AgentConfig) -> str:
    return json.dumps(
        {
            "system_prompt": cfg.system_prompt,
            "enabled_tools": list(cfg.enabled_tools),
            "custom_tools": list(cfg.custom_tools),
            "few_shot_examples_count": len(cfg.few_shot_examples),
            "architecture": cfg.architecture,
            "domain_metadata": dict(cfg.domain_metadata),
        },
        indent=2,
        sort_keys=False,
    )


def build_user_prompt(
    *,
    domain: str,
    current_config: AgentConfig,
    analysis: str,
    recent_probe_scores: list[float],
    starter_tool_names: list[str],
) -> str:
    enabled = set(current_config.enabled_tools)
    not_yet_enabled = [t for t in starter_tool_names if t not in enabled]
    sections = [
        f"# Domain: {domain}",
        "",
        "## Investigation analysis",
        analysis.strip() if analysis.strip() else "(no analysis available)",
        "",
        "## Current agent config",
        "```json",
        _format_current_config(current_config),
        "```",
        "",
        "## Starter tools NOT currently enabled (candidates for enable_tool)",
        ", ".join(not_yet_enabled) if not_yet_enabled else "(all starter tools are already enabled)",
        "",
        "## Recent probe-set scores (chronological, oldest first)",
        _format_recent_scores(recent_probe_scores),
        "",
        "Now propose ONE change as instructed.",
    ]
    return "\n".join(sections)


# ------------------------------------------------------------------ validation

def validate_proposal(
    parsed: dict[str, Any],
    *,
    current_config: AgentConfig,
    starter_tool_names: list[str],
) -> list[str]:
    """Return a list of validation errors. Empty list = valid."""
    errors: list[str] = []

    if not isinstance(parsed, dict):
        return ["proposal is not a JSON object"]

    extra = set(parsed) - {"action", "rationale", "details"}
    if extra:
        errors.append(f"unexpected top-level keys: {sorted(extra)}")

    action = parsed.get("action")
    if action not in ACTION_TYPES:
        errors.append(f"action must be one of {ACTION_TYPES}, got {action!r}")
        # Without a valid action we can't check details meaningfully.
        return errors

    rationale = parsed.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        errors.append("rationale must be a non-empty string")

    details = parsed.get("details")
    if not isinstance(details, dict):
        errors.append("details must be a JSON object")
        return errors

    if action == "modify_prompt":
        nsp = details.get("new_system_prompt")
        if not isinstance(nsp, str) or not nsp.strip():
            errors.append("modify_prompt requires details.new_system_prompt (non-empty string)")
        elif nsp.strip() == current_config.system_prompt.strip():
            errors.append("modify_prompt: new_system_prompt is identical to the current prompt")

    elif action == "enable_tool":
        name = details.get("tool_name")
        if not isinstance(name, str) or not name.strip():
            errors.append("enable_tool requires details.tool_name (non-empty string)")
        elif name not in starter_tool_names:
            errors.append(
                f"enable_tool: {name!r} is not a known starter tool "
                f"(available: {starter_tool_names})"
            )
        elif name in current_config.enabled_tools:
            errors.append(f"enable_tool: {name!r} is already enabled")

    elif action == "write_tool":
        for field_name in ("tool_name", "description", "python_code"):
            v = details.get(field_name)
            if not isinstance(v, str) or not v.strip():
                errors.append(f"write_tool requires details.{field_name} (non-empty string)")
        # Light sanity checks on the code body.
        code = details.get("python_code") or ""
        if "from tools.registry import tool" not in code and "from tools.registry import" not in code:
            errors.append(
                "write_tool: python_code must import `tool` from tools.registry"
            )
        if "@tool" not in code:
            errors.append("write_tool: python_code must decorate a function with @tool")
        if "if __name__" in code:
            errors.append("write_tool: python_code must not include an `if __name__` block")
        name = details.get("tool_name") or ""
        if name and not name.replace("_", "").isalnum():
            errors.append("write_tool: tool_name must be snake_case (alphanumeric + underscores only)")
        if name and name in current_config.enabled_tools:
            errors.append(f"write_tool: tool_name {name!r} collides with an already-enabled tool")

    elif action == "add_few_shot":
        ex = details.get("example")
        if not isinstance(ex, dict):
            errors.append("add_few_shot requires details.example (object)")
        else:
            for field_name in ("task", "reasoning", "answer"):
                v = ex.get(field_name)
                if not isinstance(v, str) or not v.strip():
                    errors.append(f"add_few_shot.example.{field_name} must be a non-empty string")

    elif action == "switch_architecture":
        new_arch = details.get("new_architecture")
        if new_arch not in VALID_ARCHITECTURES:
            errors.append(
                f"switch_architecture: new_architecture must be one of {VALID_ARCHITECTURES}, "
                f"got {new_arch!r}"
            )
        elif new_arch == current_config.architecture:
            errors.append(
                f"switch_architecture: new_architecture {new_arch!r} matches current architecture"
            )

    return errors


# ------------------------------------------------------------------- entry pt

def propose(
    *,
    domain: str,
    current_config: AgentConfig,
    analysis: str,
    recent_probe_scores: list[float],
    starter_tool_names: list[str],
    llm: LLMClient,
    model: Optional[str] = "gpt-5.1",
) -> Proposal:
    """Run the proposal phase. One LLM call.

    Parameters
    ----------
    recent_probe_scores:
        Probe-set accuracies from the most recent N iterations, oldest first.
        Pass an empty list on the very first iteration.
    starter_tool_names:
        Full list of starter tool names. Used both for the prompt (showing the
        proposer what's enable_tool-able) and for post-validation.
    """
    user = build_user_prompt(
        domain=domain,
        current_config=current_config,
        analysis=analysis,
        recent_probe_scores=recent_probe_scores,
        starter_tool_names=starter_tool_names,
    )
    resp = llm.complete(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        model=model,
        temperature=0.3,
        response_format={"type": "json_object"},
    )
    raw_text = (resp["choices"][0]["message"].get("content") or "").strip()
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as e:
        return Proposal(
            action="(unparseable)",
            rationale="",
            details={"raw_text": raw_text[:2000]},
            valid=False,
            validation_errors=[f"json_decode_error: {e}"],
            raw_response=resp,
            model=model or llm.default_model,
        )

    errors = validate_proposal(
        parsed,
        current_config=current_config,
        starter_tool_names=starter_tool_names,
    )
    return Proposal(
        action=parsed.get("action", "(missing)"),
        rationale=parsed.get("rationale", "") if isinstance(parsed.get("rationale"), str) else "",
        details=parsed.get("details", {}) if isinstance(parsed.get("details"), dict) else {},
        valid=not errors,
        validation_errors=errors,
        raw_response=resp,
        model=model or llm.default_model,
    )
