from pathlib import Path

from tools.registry import tool


@tool(
    param_descriptions={
        "path": "Directory to list. Default '.'.",
        "recursive": "If true, walk the tree (capped at max_entries).",
        "max_entries": "Cap on entries returned. Default 200.",
    },
)
def list_directory(path: str = ".", recursive: bool = False, max_entries: int = 200) -> str:
    """List directory contents.

    Returns one entry per line. Directories are suffixed with '/'. Truncates
    once max_entries is reached.
    """
    try:
        p = Path(path)
        if not p.exists():
            return f"ERROR: not found: {path}"
        if not p.is_dir():
            return f"ERROR: not a directory: {path}"
        out: list[str] = []
        if recursive:
            for child in p.rglob("*"):
                rel = child.relative_to(p)
                suffix = "/" if child.is_dir() else ""
                out.append(f"{rel.as_posix()}{suffix}")
                if len(out) >= max_entries:
                    out.append(f"[...truncated at {max_entries} entries]")
                    break
        else:
            for child in sorted(p.iterdir()):
                suffix = "/" if child.is_dir() else ""
                out.append(f"{child.name}{suffix}")
                if len(out) >= max_entries:
                    out.append(f"[...truncated at {max_entries} entries]")
                    break
        return "\n".join(out) if out else "(empty directory)"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {type(exc).__name__}: {exc}"
