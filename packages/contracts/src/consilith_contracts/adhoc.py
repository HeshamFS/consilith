"""The ad-hoc plan: what a model writes to show something in chat.

The model writes a refs-only plan and the server binds it to data, so no model ever writes a
number into a surface. The plan carries a title, a layout and blocks; each block names a
component and the handles (``r1``, ``in3``, ``a2``, ``run1``, ``p1``) the server issued for this
turn. There is no field for a value, a proposal, an approve button or a run button.

Everything here is a string, an enum, a list or a nested node, so the schema passes
``assert_model_safe_schema`` and any provider can emit it, including a brought agent through
``AgentOps.surface``.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .base import Contract

# The layout tree may nest this deep, the root being depth 1.
MAX_LAYOUT_DEPTH = 3

_BLOCK_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
_HANDLE = re.compile(r"^[a-z][a-z0-9_]{0,15}$")


class AdHocComponent(StrEnum):
    """The closed set of components a plan may use."""

    VALUES = "Values"
    METRICS = "Metrics"
    TABLE = "Table"
    CHART = "Chart"
    HEATMAP = "Heatmap"
    INPUTS = "Inputs"  # read-only, with an "Open in studio" link; no edit or run control
    ARTIFACT = "Artifact"
    EVIDENCE = "Evidence"
    JOB = "Job"
    NOTE = "Note"  # the block's title is the note's text


AdHocLayoutKind = Literal["stack", "grid", "tabs", "section"]


class AdHocBlock(Contract):
    """One piece of the surface: a component bound to handles."""

    id: str = Field(min_length=1, max_length=32)
    component: AdHocComponent
    # Free text through the prose guard. Every titled block is shown as written by the agent.
    title: str = Field(default="", max_length=200)
    # Handles from this turn's index. The binder resolves them; unknown handles are refused.
    refs: list[str] = Field(default_factory=list[str])

    @field_validator("id")
    @classmethod
    def id_is_an_identifier(cls, value: str) -> str:
        if not _BLOCK_ID.match(value):
            raise ValueError("a block id is a short identifier of letters, digits, _ and -")
        return value

    @field_validator("refs")
    @classmethod
    def refs_are_handles(cls, refs: list[str]) -> list[str]:
        for ref in refs:
            if not _HANDLE.match(ref):
                raise ValueError(f"{ref!r} is not a handle")
        return refs


class AdHocLayoutNode(Contract):
    """A node of the layout tree: a container with children, or one placed block."""

    kind: AdHocLayoutKind
    title: str = Field(default="", max_length=100)  # a section or tab heading
    block: str | None = Field(default=None, max_length=32)  # the block this node places
    children: list[AdHocLayoutNode] = Field(default_factory=list["AdHocLayoutNode"])

    @model_validator(mode="after")
    def block_or_children(self) -> AdHocLayoutNode:
        if self.block and self.children:
            raise ValueError("a layout node places one block or holds children, not both")
        if not self.block and not self.children:
            raise ValueError("a layout node places a block or holds children")
        return self


def _depth(node: AdHocLayoutNode) -> int:
    return 1 + max((_depth(child) for child in node.children), default=0)


def _placed(node: AdHocLayoutNode) -> list[str]:
    own = [node.block] if node.block else []
    return own + [block for child in node.children for block in _placed(child)]


class AdHocPlan(Contract):
    """A refs-only plan. The binder turns it into a validated A2UI surface."""

    title: str = Field(default="", max_length=100)  # free text through the prose guard
    layout: AdHocLayoutNode
    blocks: list[AdHocBlock] = Field(min_length=1)

    @model_validator(mode="after")
    def layout_and_blocks_agree(self) -> AdHocPlan:
        if _depth(self.layout) > MAX_LAYOUT_DEPTH:
            raise ValueError(f"the layout nests at most {MAX_LAYOUT_DEPTH} deep")
        ids = [block.id for block in self.blocks]
        if len(ids) != len(set(ids)):
            raise ValueError("block ids are unique")
        placed = _placed(self.layout)
        unknown = sorted(set(placed) - set(ids))
        if unknown:
            raise ValueError(f"the layout places unknown blocks: {', '.join(unknown)}")
        if len(placed) != len(set(placed)):
            raise ValueError("a block is placed once")
        missing = sorted(set(ids) - set(placed))
        if missing:
            raise ValueError(f"blocks the layout never places: {', '.join(missing)}")
        return self
