"""Agent Skills: SKILL.md folders, loaded per role (FR-PR-7), with progressive disclosure.

Only the frontmatter (name, description) is read up front; the body is read on demand.
A skill named in a drawer but missing on disk is reported as missing, never invented.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from consilith_contracts.extensions import all_extensions

SKILLS_DIR = Path("skills")  # explicit local override only; default roots are registered packages


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path | None = None
    content: str = ""

    def body(self) -> str:
        text = self.path.read_text(encoding="utf-8") if self.path else self.content
        return text.split("---", 2)[2].strip() if text.startswith("---") else text


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    head = text.split("---", 2)[1]
    out: dict[str, str] = {}
    key = ""
    for raw in head.splitlines():
        if not raw.strip():
            continue
        if raw.startswith((" ", "\t")) and key:
            out[key] = (out[key] + " " + raw.strip()).strip()
            continue
        if ":" in raw:
            key, _, value = raw.partition(":")
            key = key.strip()
            value = value.strip()
            out[key] = "" if value in (">-", ">", "|") else value.strip('"')
    return out


@lru_cache(maxsize=16)
def _catalog_at(root: Path) -> dict[str, Skill]:
    skills: dict[str, Skill] = {}
    if not root.is_dir():
        return skills
    for path in sorted(root.glob("*/SKILL.md")):
        meta = _frontmatter(path.read_text(encoding="utf-8"))
        name = meta.get("name", path.parent.name)
        if name != path.parent.name:
            continue
        skills[name] = Skill(name=name, description=meta.get("description", ""), path=path)
    return skills


def catalog(root: Path | None = None) -> dict[str, Skill]:
    if root is not None:
        return _catalog_at(root)
    skills: dict[str, Skill] = {}
    for name, doc in all_extensions("skill_documents").items():
        text = str(cast(dict[str, Any], doc).get("body", "")) if isinstance(doc, dict) else str(doc)
        meta = _frontmatter(text)
        description = (
            str(cast(dict[str, Any], doc).get("description", "")) if isinstance(doc, dict) else ""
        )
        skills[name] = Skill(name, description or meta.get("description", ""), content=text)
    return skills


def load(names: list[str], allowed: set[str] | None = None) -> tuple[list[Skill], list[str]]:
    """The loaded skills and the names that have no folder."""
    have = {n: s for n, s in catalog().items() if allowed is None or n in allowed}
    return [have[n] for n in names if n in have], [n for n in names if n not in have]
