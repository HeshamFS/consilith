"""JSON Pointer handling (RFC 6901) and the map from a path to the lane it writes."""

from __future__ import annotations

from dataclasses import dataclass

from consilith_contracts import CASES, CaseDef

from .errors import Denied

TWIN_OPS = frozenset({"add", "replace", "remove", "test"})


def parse_pointer(path: str) -> list[str]:
    """Split and unescape a JSON Pointer. Refuses the root, empty segments and bad escapes."""
    if not path.startswith("/") or path == "/":
        raise Denied(f"path {path!r} is not a pointer below the document root")
    out: list[str] = []
    for raw in path[1:].split("/"):
        if raw == "":
            raise Denied(f"path {path!r} has an empty segment")
        i = 0
        while i < len(raw):
            if raw[i] == "~" and (i + 1 >= len(raw) or raw[i + 1] not in "01"):
                raise Denied(f"path {path!r} has a bad ~ escape")
            i += 1
        out.append(raw.replace("~1", "/").replace("~0", "~"))
    return out


def pointer(*segments: str) -> str:
    return "/" + "/".join(s.replace("~", "~0").replace("/", "~1") for s in segments)


@dataclass(frozen=True)
class LanePath:
    case: str
    lane: str
    rest: tuple[str, ...]

    @property
    def key(self) -> str:
        return f"{self.case}/{self.lane}"


def twin_lane(path: str, cases: dict[str, CaseDef] | None = None) -> LanePath:
    """The lane a twin path writes. Ancestors of a field (/twin, /twin/case, the lane root)
    are refused, so no write can reach across lanes."""
    seg = parse_pointer(path)
    if seg[0] != "twin":
        raise Denied(f"path {path!r} is not in the twin")
    if len(seg) < 4:
        raise Denied(f"path {path!r} writes above a lane field; write the fields themselves")
    case, lane = seg[1], seg[2]
    cdef = (CASES if cases is None else cases).get(case)
    if cdef is None:
        raise Denied(f"unknown case {case!r}")
    if cdef.lane(lane) is None:
        raise Denied(f"case {case!r} has no lane {lane!r}")
    return LanePath(case=case, lane=lane, rest=tuple(seg[3:]))
