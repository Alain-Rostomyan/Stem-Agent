import shlex
import subprocess
import sys

from tools.registry import tool


@tool(
    param_descriptions={
        "cmd": "Shell command to run. On Windows it goes through cmd.exe; elsewhere /bin/sh.",
        "timeout_s": "Timeout in seconds. Default 30. Hard-killed on expiry.",
        "cwd": "Working directory. Default the agent's CWD.",
    },
)
def run_shell_command(cmd: str, timeout_s: int = 30, cwd: str = ".") -> str:
    """Run a shell command and return combined stdout/stderr (capped) + exit code.

    Output is the last ~10 KB of combined stdout/stderr followed by a final
    line 'EXIT: <code>'. Killed processes return 'ERROR: timeout after Ns'.
    Avoid long-running or interactive commands; this is meant for short
    test/build/inspect commands.
    """
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            timeout=timeout_s,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: timeout after {timeout_s}s"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {type(exc).__name__}: {exc}"
    out = (proc.stdout or "") + (proc.stderr or "")
    if len(out) > 10_000:
        out = "[...truncated to last 10000 chars]\n" + out[-10_000:]
    return f"{out}\nEXIT: {proc.returncode}"
