"""Web search via DuckDuckGo HTML endpoint, with on-disk caching.

Cached results live in `runs/web_search_cache.json` so repeated runs (probe set
re-scoring, eval re-runs) don't keep hammering the network. The cache is keyed
on (query, num_results).
"""

from __future__ import annotations

import hashlib
import html as _html
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from tools.registry import tool


_CACHE_PATH = Path(__file__).resolve().parents[2] / "runs" / "web_search_cache.json"


def _load_cache() -> dict[str, list[dict[str, str]]]:
    if _CACHE_PATH.exists():
        try:
            return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save_cache(cache: dict[str, list[dict[str, str]]]) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _cache_key(query: str, num_results: int) -> str:
    h = hashlib.sha1(f"{query}|{num_results}".encode("utf-8")).hexdigest()[:16]
    return f"{h}:{query[:64]}"


_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
    r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL,
)


def _strip_tags(s: str) -> str:
    return _html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _ddg_search(query: str, num_results: int) -> list[dict[str, str]]:
    url = "https://duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; stem-agent/0.1)",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    out: list[dict[str, str]] = []
    for m in _RESULT_RE.finditer(body):
        href, title, snippet = m.groups()
        # DDG wraps real URLs in /l/?uddg=...
        parsed = urllib.parse.urlparse(href)
        if parsed.path == "/l/" or parsed.path.startswith("//duckduckgo.com/l/"):
            qs = urllib.parse.parse_qs(parsed.query)
            if "uddg" in qs:
                href = qs["uddg"][0]
        out.append({
            "url": href,
            "title": _strip_tags(title),
            "snippet": _strip_tags(snippet),
        })
        if len(out) >= num_results:
            break
    return out


@tool(
    param_descriptions={
        "query": "Search query.",
        "num_results": "Number of results to return. Default 5.",
    },
)
def web_search(query: str, num_results: int = 5) -> str:
    """Search the web (via DuckDuckGo) and return the top results.

    Returns a numbered list of 'title — url\\n   snippet' entries. Results are
    cached on disk by (query, num_results) so re-running the same query is free.
    On network failure returns 'ERROR: ...'.
    """
    try:
        num_results = max(1, min(int(num_results), 10))
        cache = _load_cache()
        key = _cache_key(query, num_results)
        if key in cache:
            results = cache[key]
        else:
            results = _ddg_search(query, num_results)
            cache[key] = results
            _save_cache(cache)
        if not results:
            return "(no results)"
        lines: list[str] = []
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']} — {r['url']}")
            if r.get("snippet"):
                lines.append(f"   {r['snippet']}")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {type(exc).__name__}: {exc}"
