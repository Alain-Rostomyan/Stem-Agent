from pathlib import Path

from tools.registry import tool


@tool(
    param_descriptions={
        "path": "Filesystem path to read. May be absolute or relative to the working directory.",
        "max_bytes": "Optional cap on the number of bytes returned. Default 200_000.",
    },
)
def read_file(path: str, max_bytes: int = 200_000) -> str:
    """Read a UTF-8 text file and return its contents.

    On failure returns a string starting with 'ERROR:'. Use this for source
    files, config files, and small data files. Binary files will return an
    error.
    """
    try:
        p = Path(path)
        if not p.exists():
            return f"ERROR: file not found: {path}"
        if not p.is_file():
            return f"ERROR: not a regular file: {path}"
        data = p.read_bytes()
        if len(data) > max_bytes:
            data = data[:max_bytes]
            suffix = f"\n[...truncated at {max_bytes} bytes; file is {p.stat().st_size} bytes total]"
        else:
            suffix = ""
        try:
            return data.decode("utf-8") + suffix
        except UnicodeDecodeError:
            return f"ERROR: file is not valid UTF-8: {path}"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {type(exc).__name__}: {exc}"
