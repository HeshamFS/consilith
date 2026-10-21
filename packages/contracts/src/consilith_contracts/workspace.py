"""Model-authored layout structure and bindings; no model-authored scientific values."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

HEADINGS = (
    "",
    "Your workspace",
    "Model and evidence",
    "Prepare and explore",
    "Compare and review",
    "Control desk",
    "Model space",
    "Evidence",
    "Results",
    "Overview",
    "Inputs",
    "Review",
    "Explore",
    "Run",
    "Next step",
    "Details",
    "Comparison",
    "Context",
    "Parameters",
)
DESCRIPTIONS = (
    "",
    "Prepare inputs, inspect the model, then run when ready.",
    "Set up your model. Keep the evidence in view.",
    "Explore inputs and outputs side by side.",
    "Review the available evidence before the next step.",
)


class WorkspaceBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]*$")
    component: Literal[
        "SchemaForm",
        "ParameterGrid",
        "ArtifactView",
        "ResultTable",
        "MetricGrid",
        "JobStatus",
        "EvidenceView",
    ]
    binding: str


class LayoutNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]*$")
    kind: Literal["stack", "grid", "split", "tabs", "section", "block"]
    title: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=240)
    ratio: Literal["balanced", "focus-start", "focus-end"] = "balanced"
    emphasis: Literal["plain", "surface", "hero", "quiet"] = "plain"
    density: Literal["comfortable", "compact"] = "comfortable"
    block: str | None = None
    children: list[LayoutNode] = Field(default_factory=list["LayoutNode"])


class WorkspacePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="Your workspace", max_length=100)
    intent: str = Field(
        default="Prepare inputs, inspect the model, then run when ready.", max_length=240
    )
    blocks: list[WorkspaceBlock]
    layout: LayoutNode

    def check(self, bindings: dict[str, list[str]]) -> None:
        if not self.blocks or len(self.blocks) > 128:
            raise ValueError("workspace requires between one and 128 blocks")
        if self.title not in HEADINGS or self.intent not in DESCRIPTIONS:
            raise ValueError("workspace prose must use approved presentation labels")
        seen: set[str] = {"root", "workspace"}
        used: set[str] = set()
        for block in self.blocks:
            if block.id in seen:
                raise ValueError("component ids must be unique and not reserved")
            if block.binding not in bindings or block.component not in bindings[block.binding]:
                raise ValueError(
                    f"unsupported component/binding: {block.component}/{block.binding}"
                )
            if block.binding in used:
                raise ValueError("each declared workspace binding occurs exactly once")
            seen.add(block.id)
            used.add(block.binding)
        if used != set(bindings):
            raise ValueError(
                "workspace must include all input, execution, artifact, result and "
                "evidence bindings"
            )
        refs: list[str] = []
        count = 0

        def walk(node: LayoutNode, depth: int) -> None:
            nonlocal count
            count += 1
            if node.title not in HEADINGS and node.title not in bindings:
                raise ValueError("layout heading must be a catalog label or binding reference")
            if node.description not in DESCRIPTIONS:
                raise ValueError("layout description must use approved presentation labels")
            if depth > 7 or count > 160:
                raise ValueError("layout exceeds nesting or node budget")
            if node.id in seen:
                raise ValueError("layout ids must be unique and separate from block ids")
            seen.add(node.id)
            if node.kind == "block":
                if node.children or node.block is None:
                    raise ValueError("block leaves require one reference and no children")
                refs.append(node.block)
            elif node.block is not None or not node.children:
                raise ValueError("layout containers require children, not block references")
            if node.kind == "split" and len(node.children) != 2:
                raise ValueError("split requires exactly two children")
            if node.kind == "tabs" and any(not child.title for child in node.children):
                raise ValueError("each tab requires a readable title")
            for child in node.children:
                walk(child, depth + 1)

        walk(self.layout, 0)
        if len(refs) != len(set(refs)) or set(refs) != {b.id for b in self.blocks}:
            raise ValueError("layout must reference every block exactly once")
