"""Data-only plugin discovery contracts; implementations live outside the platform."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import Contract


class PluginRole(Contract):
    id: str = Field(min_length=1)
    label: str
    description: str = ""
    autonomy: str = "suggest"
    goal: str = ""
    skills: list[str] = Field(default_factory=list[str])


class PluginSkill(Contract):
    id: str
    description: str = ""
    path: str = ""


class PluginKnowledge(Contract):
    id: str
    title: str
    media_type: str = "text/markdown"
    path: str = ""


class PluginMCPServer(Contract):
    id: str
    tools: list[str] = Field(default_factory=list[str])


class ToolMeta(Contract):
    """Display facts about one tool, declared by its plugin. Hints only: this authorises nothing
    in this release. The ``readOnly`` hint in particular never makes a tool callable."""

    # The execution tier: 0 hand calculation, 1 quick, 2 accurate (``results.Tier``, which this
    # module cannot import without a cycle).
    tier: Literal[0, 1, 2] | None = None
    read_only: bool = False
    progress: bool = False  # the tool reports progress while it runs


class RunMeta(Contract):
    """How long one (capability, mode) takes. Duration is declared where the person asks for
    it, because one workflow may call several tools."""

    estimate_s: float | None = Field(default=None, gt=0)
    long_running: bool = False


class ReplayPolicy(Contract):
    relative_tolerance: float = Field(default=0, ge=0)
    absolute_tolerance: float = Field(default=0, ge=0)


class PluginManifest(Contract):
    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    version: str = Field(min_length=1)
    title: str
    distribution: str = ""
    schema_refs: dict[str, str] = Field(default_factory=dict[str, str])
    package_dependencies: list[str] = Field(default_factory=list[str])
    description: str = ""
    capabilities: list[str] = Field(default_factory=list[str])
    instructions: str = Field(default="", max_length=12000)
    prompt_knowledge: list[str] = Field(default_factory=list[str], max_length=8)
    roles: list[PluginRole] = Field(default_factory=list[PluginRole])
    tools: list[str] = Field(default_factory=list[str])
    # Optional metadata beside ``tools`` (which stays a list of names). Unset means none, and an
    # unset map is left out of the wire form, so a manifest without them is unchanged.
    # ``tool_meta`` maps a tool name to its ToolMeta; ``run_meta`` maps a capability (case id)
    # and then a mode to its RunMeta.
    tool_meta: dict[str, ToolMeta] | None = None
    run_meta: dict[str, dict[str, RunMeta]] | None = None
    skills: list[PluginSkill] = Field(default_factory=list[PluginSkill])
    knowledge: list[PluginKnowledge] = Field(default_factory=list[PluginKnowledge])
    mcp_servers: list[PluginMCPServer] = Field(default_factory=list[PluginMCPServer])
    artifact_media_types: list[str] = Field(default_factory=list[str])
    replay_policy: ReplayPolicy = Field(default_factory=ReplayPolicy)
    policy_ports: list[str] = Field(default_factory=list[str])
    dependencies: list[str] = Field(default_factory=list[str])
