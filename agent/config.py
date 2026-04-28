"""Config schema for a specialized agent.

A config is the artifact the stem produces. It's plain JSON so it diffs cleanly
across domains and across evolution steps (which is what makes the writeup
figures easy to generate).

```
{
  "system_prompt": "...",
  "enabled_tools": ["read_file", ...],
  "custom_tools": ["tools/custom/qa/extract_test_targets.py", ...],
  "few_shot_examples": [{"task": "...", "trace": "..."}, ...],
  "architecture": "single_loop" | "planner_executor",
  "domain_metadata": {"name": "qa", "notes": "..."}
}
```
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


Architecture = Literal["single_loop", "planner_executor"]
VALID_ARCHITECTURES: tuple[str, ...] = ("single_loop", "planner_executor")


@dataclass
class AgentConfig:
    system_prompt: str = "You are a helpful AI agent. Solve the user's task using the tools you have."
    enabled_tools: list[str] = field(default_factory=list)
    custom_tools: list[str] = field(default_factory=list)
    few_shot_examples: list[dict[str, Any]] = field(default_factory=list)
    architecture: Architecture = "single_loop"
    domain_metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not isinstance(self.system_prompt, str) or not self.system_prompt.strip():
            raise ValueError("system_prompt must be a non-empty string")
        if not isinstance(self.enabled_tools, list) or not all(isinstance(t, str) for t in self.enabled_tools):
            raise ValueError("enabled_tools must be a list[str]")
        if not isinstance(self.custom_tools, list) or not all(isinstance(t, str) for t in self.custom_tools):
            raise ValueError("custom_tools must be a list[str]")
        if not isinstance(self.few_shot_examples, list):
            raise ValueError("few_shot_examples must be a list")
        if self.architecture not in VALID_ARCHITECTURES:
            raise ValueError(
                f"architecture must be one of {VALID_ARCHITECTURES}, got {self.architecture!r}"
            )
        if not isinstance(self.domain_metadata, dict):
            raise ValueError("domain_metadata must be a dict")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentConfig":
        known = {
            "system_prompt", "enabled_tools", "custom_tools",
            "few_shot_examples", "architecture", "domain_metadata",
        }
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        cfg = cls(**{k: v for k, v in data.items() if k in known})
        cfg.validate()
        return cfg


def default_config(domain: str = "generic") -> AgentConfig:
    """Empty-ish starting config — just a generic prompt and the read-only tools."""
    return AgentConfig(
        system_prompt="You are a helpful AI agent. Solve the user's task using the tools you have.",
        enabled_tools=["read_file", "list_directory", "run_python", "call_llm"],
        custom_tools=[],
        few_shot_examples=[],
        architecture="single_loop",
        domain_metadata={"name": domain, "notes": ""},
    )


def load_config(path: str | Path) -> AgentConfig:
    """Load and validate a config JSON file."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return AgentConfig.from_dict(data)


def save_config(config: AgentConfig, path: str | Path) -> None:
    config.validate()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(config.to_dict(), f, indent=2, sort_keys=False)
        f.write("\n")
