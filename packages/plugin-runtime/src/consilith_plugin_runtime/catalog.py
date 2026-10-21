"""Read operator-owned, atomically published discovery configuration."""

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def read_catalog(path: str | Path | None = None) -> dict:
    """Return revision, references, tokens and solver_urls without loading any plugins.

    Missing configuration returns None-like empty fields; a configured malformed or
    missing file fails closed. The revision binds bytes, never trusts a declared hash.
    """
    target = path or os.environ.get("CONSILITH_PLUGIN_CATALOG")
    if not target:
        return {"revision": "", "references": [], "tokens": {}, "solver_urls": {}}
    payload = Path(target).read_bytes()
    document = json.loads(payload)
    references, tokens = [], {}
    for plugin in document["plugins"]:
        reference, token = plugin["reference"], plugin["token"]
        url = urlsplit(reference)
        if url.scheme != "tcp" or not url.hostname or not url.port or url.username:
            raise ValueError("catalog plugins require tcp://host:port endpoints")
        if not isinstance(token, str) or len(token) < 32 or reference in tokens:
            raise ValueError("catalog requires unique endpoints and strong scoped tokens")
        references.append(reference)
        tokens[reference] = token
    urls = document.get("solverUrls", {})
    if not isinstance(urls, dict) or any(
        not isinstance(key, str) or urlsplit(value).scheme not in {"http", "https"}
        for key, value in urls.items()
    ):
        raise ValueError("invalid catalog MCP endpoints")
    return {
        "revision": hashlib.sha256(payload).hexdigest(),
        "references": references,
        "tokens": tokens,
        "solver_urls": urls,
    }
