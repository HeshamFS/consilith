"""Fencing for text the model reads but must not obey (BUILD_SPEC section 6, "Injection handling").

Everything third-party or externally originated goes into a prompt as
``<untrusted source="..." id="...">...</untrusted>``: tool and skill text, tool outputs, lesson
text, proposal reasons, warnings, peer text and artifact text. The closing tag inside the text is
escaped so it cannot end the fence early, and the text has a size cap. Authority never comes from
fenced text: ``ToolHost`` decides.
"""

from __future__ import annotations

import re

DEFAULT_CAP = 2000  # characters; a tool result is capped at about 2k tokens elsewhere

_TAG = re.compile(r"<\s*(/?)\s*untrusted", re.IGNORECASE)
_ATTR_UNSAFE = re.compile(r"[^A-Za-z0-9_.:/#@ -]")


def _attr(value: str) -> str:
    return _ATTR_UNSAFE.sub("_", value)[:120]


def escape(text: str) -> str:
    """Neutralise every opening and closing ``untrusted`` tag inside ``text``."""
    return _TAG.sub(lambda m: f"&lt;{m.group(1)}untrusted", text)


def fence(text: str, *, source: str, id: str = "", cap: int = DEFAULT_CAP) -> str:
    """The text inside an untrusted fence, escaped and cut at ``cap`` characters."""
    body = escape(text)
    if len(body) > cap:
        body = body[:cap] + f"... [cut at {cap} characters]"
    ident = f' id="{_attr(id)}"' if id else ""
    return f'<untrusted source="{_attr(source)}"{ident}>{body}</untrusted>'
