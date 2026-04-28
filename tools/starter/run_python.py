import os
import subprocess
import sys
import tempfile
from pathlib import Path

from tools.registry import tool


@tool(
    param_descriptions={
        "code": "Python source to execute. Runs in a fresh subprocess with the project root on PYTHONPATH.",
        "timeout_s": "Timeout in seconds. Default 30.",
    },
)
def run_python(code: str, timeout_s: int = 30) -> str:
    """Run a Python snippet in a fresh subprocess and return stdout/stderr + exit.

    The snippet runs with the project root on PYTHONPATH so it can import
    project modules (`from agent.config import ...`). Each call is a fresh
    interpreter — no state carries between calls. Use this for one-off
    computation, running ad-hoc scripts, or invoking pytest programmatically.
    """
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code)
            tmp_path = f.name
        env = os.environ.copy()
        # Put repo root on PYTHONPATH so 'agent', 'tools', 'stem' are importable.
        repo_root = str(Path(__file__).resolve().parents[2])
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = repo_root + (os.pathsep + existing if existing else "")
        try:
            proc = subprocess.run(
                [sys.executable, tmp_path],
                capture_output=True,
                timeout=timeout_s,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except subprocess.TimeoutExpired:
        return f"ERROR: timeout after {timeout_s}s"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {type(exc).__name__}: {exc}"
    out = (proc.stdout or "") + (proc.stderr or "")
    if len(out) > 10_000:
        out = "[...truncated to last 10000 chars]\n" + out[-10_000:]
    return f"{out}\nEXIT: {proc.returncode}"
