"""Data-only plugin discovery contracts; implementations live outside the platform."""

from __future__ import annotations

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
    skills: list[PluginSkill] = Field(default_factory=list[PluginSkill])
    knowledge: list[PluginKnowledge] = Field(default_factory=list[PluginKnowledge])
    mcp_servers: list[PluginMCPServer] = Field(default_factory=list[PluginMCPServer])
    artifact_media_types: list[str] = Field(default_factory=list[str])
    replay_policy: ReplayPolicy = Field(default_factory=ReplayPolicy)
    policy_ports: list[str] = Field(default_factory=list[str])
    dependencies: list[str] = Field(default_factory=list[str])
