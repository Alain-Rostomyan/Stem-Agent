from pathlib import Path

from tools.registry import tool


@tool(
    param_descriptions={
        "path": "Path to write. Parent directories are created if needed.",
        "content": "UTF-8 text to write. Overwrites any existing file.",
    },
)
def write_file(path: str, content: str) -> str:
    """Write UTF-8 text to a file, overwriting any existing content.

    Returns 'OK: wrote N bytes to <path>' on success, or 'ERROR: ...' on failure.
    Creates parent directories as needed.
    """
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        p.write_bytes(data)
        return f"OK: wrote {len(data)} bytes to {path}"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {type(exc).__name__}: {exc}"
