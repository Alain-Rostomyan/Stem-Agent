"""Tiny .env loader (no external dep).

Reads simple KEY=VALUE lines from a .env file and sets them in os.environ.
Existing environment variables win over .env (so you can still override with
a real shell export). Lines starting with # and blank lines are skipped.
Optional surrounding quotes on values are stripped. Anything more exotic
(multi-line strings, command substitution, variable interpolation) is out of
scope — keep your .env boring.

Used by entry-point scripts so the project runs identically inside VSCode's
terminal, an external shell, or CI, without depending on the IDE setting
`python.terminal.useEnvFile`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def load_dotenv(path: Optional[str | Path] = None, *, override: bool = False) -> int:
    """Load KEY=VALUE pairs from `path` into os.environ.

    Returns the number of variables it set. Silently does nothing if `path`
    doesn't exist (common case in CI / Docker where vars come from elsewhere).
    """
    p = Path(path) if path else Path(".env")
    if not p.is_file():
        return 0
    set_count = 0
    for raw_line in p.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        if not key:
            continue
        if not override and key in os.environ:
            continue
        os.environ[key] = value
        set_count += 1
    return set_count
