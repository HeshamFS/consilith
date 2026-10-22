"""The unified tool catalog (section 5): one catalog per (room, person), one rendered tool block.

Four families keep separate namespaces and are never mixed:

- platform: ``room.<op>`` (also ``ui.*``, ``peer.*``), from the ops registry (``platform_tools``);
- plugin MCP: ``plugin/<pluginId>/<server>/<tool>``, from the manifest, ``tools/list``, drawer;
- skills: ``skill/<pluginId>/<name>``, from plugin extensions and the agent's drawer;
- knowledge: ``kb/<pluginId>/<id>``, from plugin extensions.

Every provider sees the same provider-neutral aliases, ``<kind>_<slug>_<6-char hash>``, whose hash
comes from the canonical ref alone. Plugin tools are listed and described here but run only
through governed workflows. A blocked entry stays visible with the reason, so the agent can tell
the person what to ask for. Nothing here returns a server address or version.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
from collections import OrderedDict
from collections.abc import Callable, Sequence
from typing import Any, Protocol, cast, runtime_checkable

from consilith_contracts import (
    AgentConfig,
    Autonomy,
    Availability,
    CatalogEntry,
    ReachableVia,
    RiskClass,
    ToolKind,
    ToolSpec,
    assert_model_safe_schema,
)
from consilith_contracts.agents import at_least
from consilith_contracts.extensions import optional_extension, plugin_descriptors
from consilith_contracts.manifest_meta import ManifestMetaError, server_tool_names

from ..domain.errors import Denied, NotFound
from ..domain.plugin_access import active_plugins, tool_index
from ..domain.service import RoomService, personal_agent_for
from ..protocols.mcp_client import SolverRegistry
from . import platform_tools

# What the peer audience may use: nothing else of the catalog (section 5, last line of the
# per-agent permission filtering).
PEER_REFS = frozenset({"peer.reply", "proposal.create"})
# How many entries a search returns. A fixed constant: the model never types a number.
SEARCH_LIMIT = 20
_FAMILIES = ("platform", "plugin", "skill", "kb")
_OP_KINDS = frozenset({ToolKind.PLATFORM, ToolKind.UI, ToolKind.PEER})
_KIND_ORDER = {
    ToolKind.PLATFORM: 0,
    ToolKind.UI: 1,
    ToolKind.PEER: 2,
    ToolKind.PLUGIN: 3,
    ToolKind.SKILL: 4,
    ToolKind.KB: 5,
}
_ALIAS_MAX = 64
_HASH_LEN = 6
_LEGACY_SERVER = "default"  # the ref segment for a plugin that declares no MCP servers


# --- aliases ---------------------------------------------------------------------------------


def _slug(text: str, room_for: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug[: max(room_for, 1)].strip("_") or "x"


def alias_for(ref: str, kind: ToolKind) -> str:
    """``<kind>_<slug>_<6-char hash>``. The hash comes from the canonical ref alone, so the alias
    is the same for every provider, model, person and room. It matches ``^[a-zA-Z0-9_-]{1,64}$``,
    the rule the native tool-name fields share."""
    digest = hashlib.sha256(ref.encode("utf-8")).hexdigest()[:_HASH_LEN]
    body = ref.split("/", 1)[1] if "/" in ref else ref
    # ``peer.reply`` reads as ``peer_reply``, not ``peer_peer_reply``: a doubled kind confused a
    # model into reasoning about the name instead of calling it.
    if body.startswith(f"{kind.value}.") and len(body) > len(kind.value) + 1:
        body = body[len(kind.value) + 1 :]
    room_for = _ALIAS_MAX - len(kind.value) - _HASH_LEN - 2
    return f"{kind.value}_{_slug(body, room_for)}_{digest}"


def reverse_map(entries: Sequence[CatalogEntry]) -> dict[str, str]:
    """``alias -> canonical ref``. Two refs never share an alias (checked when the catalog is
    built)."""
    return {e.spec.alias: e.spec.ref for e in entries}


def resolve_alias(entries: Sequence[CatalogEntry], alias: str) -> CatalogEntry | None:
    for entry in entries:
        if entry.spec.alias == alias:
            return entry
    return None


# --- the read ops ----------------------------------------------------------------------------


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _read_spec(ref: str, description: str, schema: dict[str, Any]) -> ToolSpec:
    return ToolSpec(
        ref=ref,
        alias=alias_for(ref, ToolKind.PLATFORM),
        kind=ToolKind.PLATFORM,
        description=description,
        input_schema=schema,
        risk=RiskClass.R0,
        min_autonomy=Autonomy.OBSERVE,
    )


READ_SPECS: tuple[ToolSpec, ...] = (
    _read_spec(
        "catalog.search",
        "Find tools, skills and knowledge by words; returns refs and one-liners",
        _schema(
            {
                "query": {"type": "string", "maxLength": 200},
                "family": {"type": "string", "enum": list(_FAMILIES)},
            },
            ["query"],
        ),
    ),
    _read_spec(
        "catalog.describe",
        "Show one entry in full, with its schema, by ref or alias",
        _schema({"ref": {"type": "string", "maxLength": 300}}, ["ref"]),
    ),
    _read_spec(
        "skill.read",
        "Read one skill the agent may use, by ref",
        _schema({"ref": {"type": "string", "maxLength": 300}}, ["ref"]),
    ),
    _read_spec(
        "kb.read",
        "Read one knowledge document of an active plugin, by ref",
        _schema({"ref": {"type": "string", "maxLength": 300}}, ["ref"]),
    ),
)
for _spec in READ_SPECS:
    assert_model_safe_schema(_spec.input_schema)


def _default_platform_specs() -> list[ToolSpec]:
    """The read ops plus whatever the ops registry publishes. WS-L's registry may expose
    ``op_specs()``; until it does the catalog lists only its own reads."""
    published = cast(
        Callable[[], Sequence[ToolSpec]] | None, getattr(platform_tools, "op_specs", None)
    )
    extra = list(published()) if callable(published) else []
    own = {s.ref for s in READ_SPECS}
    return [*READ_SPECS, *(s for s in extra if s.ref not in own)]


# --- the catalog -----------------------------------------------------------------------------


@runtime_checkable
class ToolCatalogPort(Protocol):
    """What the loop and the prompt builder need from a catalog."""

    def for_person(self, room: RoomService, person: str) -> list[CatalogEntry]: ...

    def render_block(self, entries: Sequence[CatalogEntry]) -> str: ...

    def digest(self, block: str) -> str: ...


def _sha(value: object) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _line(text: str, limit: int = 140) -> str:
    one = " ".join(text.split())
    return one if len(one) <= limit else one[: limit - 1] + "…"


def _server_names(server: dict[str, Any]) -> list[str]:
    try:
        return server_tool_names(server)
    except ManifestMetaError:
        return []


class ToolCatalog:
    """Per (room, person) catalog entries, the one rendered tool block and its digest."""

    def __init__(
        self,
        registry: SolverRegistry | None = None,
        *,
        platform_specs: Callable[[], Sequence[ToolSpec]] = _default_platform_specs,
        cache_size: int = 256,
    ) -> None:
        self.registry = registry
        self._platform_specs = platform_specs
        self._cache_size = cache_size
        self._cache: OrderedDict[tuple[Any, ...], tuple[CatalogEntry, ...]] = OrderedDict()

    # keys ----------------------------------------------------------------------------------
    def _config(self, room: RoomService, person: str) -> AgentConfig:
        try:
            return room.agent_config(personal_agent_for(person))
        except NotFound:
            return AgentConfig()

    @staticmethod
    def drawer_digest(config: AgentConfig) -> str:
        """The part of the agent's drawer that changes what the catalog shows."""
        return _sha(
            {
                "skills": sorted(config.skills),
                "allowed": sorted(config.tools_allowed),
                "denied": sorted(config.tools_denied),
                "autonomy": config.autonomy.value,
            }
        )

    @staticmethod
    def activation_revision(room: RoomService) -> str:
        """Changes when a plugin is activated or a pin moves, or when the installed set does."""
        return _sha(
            {
                "active": sorted(f"{p['id']}@{p.get('version', '')}" for p in active_plugins(room)),
                "pins": room.read("packagePins") or [],
                "installed": sorted(
                    f"{p['id']}@{p.get('version', '')}" for p in plugin_descriptors()
                ),
            }
        )

    def cache_key(self, room: RoomService, person: str, audience: str = "owner") -> tuple[Any, ...]:
        """``(room, person, registry revision, drawer digest, activation revision)``, plus the
        audience (a peer turn has its own, smaller catalog)."""
        revision = self.registry.revision if self.registry is not None else 0
        return (
            room.id,
            person,
            revision,
            self.drawer_digest(self._config(room, person)),
            self.activation_revision(room),
            audience,
        )

    # entries -------------------------------------------------------------------------------
    def for_person(
        self, room: RoomService, person: str, audience: str = "owner"
    ) -> list[CatalogEntry]:
        key = self.cache_key(room, person, audience)
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
            return list(hit)
        entries = self._build(room, person, audience)
        self._cache[key] = tuple(entries)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return list(entries)

    def _build(self, room: RoomService, person: str, audience: str) -> list[CatalogEntry]:
        config = self._config(room, person)
        entries = self._platform_entries(config, audience)
        if audience == "owner":
            entries += self._plugin_entries(room, config)
        entries.sort(key=lambda e: (_KIND_ORDER[e.spec.kind], e.spec.ref))
        seen: dict[str, str] = {}
        for entry in entries:
            other = seen.setdefault(entry.spec.alias, entry.spec.ref)
            if other != entry.spec.ref:
                raise ValueError(f"alias {entry.spec.alias!r} is shared by two refs")
        return entries

    def _platform_entries(self, config: AgentConfig, audience: str) -> list[CatalogEntry]:
        out: list[CatalogEntry] = []
        for raw in self._platform_specs():
            if raw.risk is RiskClass.R4:  # never offered to an agent
                continue
            if audience == "peer" and raw.ref not in PEER_REFS:
                continue
            spec = raw.model_copy(update={"alias": alias_for(raw.ref, raw.kind)})
            assert_model_safe_schema(spec.input_schema)
            if at_least(config.autonomy, spec.min_autonomy):
                out.append(CatalogEntry(spec=spec))
            else:
                out.append(
                    CatalogEntry(
                        spec=spec,
                        availability=Availability.BLOCKED_BY_AUTONOMY,
                        reason=f"needs autonomy {spec.min_autonomy.value}; "
                        "the owner raises it in Your agent",
                    )
                )
        return out

    def _plugin_entries(self, room: RoomService, config: AgentConfig) -> list[CatalogEntry]:
        out: list[CatalogEntry] = []
        active = {str(p["id"]): p for p in active_plugins(room)}
        index = tool_index(room)
        installed = {str(p["id"]): p for p in plugin_descriptors()}
        for pid in sorted({*active, *installed}):
            if "/" in pid:
                continue
            plugin = active.get(pid) or installed[pid]
            inactive = pid not in active
            why_inactive = f"plugin {pid} is installed but not activated in this room"
            out += self._tool_entries(pid, plugin, config, index, inactive, why_inactive)
            for skill in plugin.get("skills", []):
                sid = str(skill["id"])
                if "/" in sid:
                    continue
                spec = self._doc_spec(
                    f"skill/{pid}/{sid}", ToolKind.SKILL, str(skill.get("description", ""))
                )
                if inactive:
                    out.append(
                        self._blocked(spec, Availability.BLOCKED_BY_ACTIVATION, why_inactive)
                    )
                elif sid not in config.skills:
                    out.append(
                        self._blocked(
                            spec,
                            Availability.BLOCKED_BY_DRAWER,
                            f"ask the owner to enable skill {sid} in Your agent",
                        )
                    )
                else:
                    out.append(CatalogEntry(spec=spec))
            for item in plugin.get("knowledge", []):
                kid = str(item["id"])
                if "/" in kid:
                    continue
                spec = self._doc_spec(f"kb/{pid}/{kid}", ToolKind.KB, str(item.get("title", "")))
                if inactive:
                    out.append(
                        self._blocked(spec, Availability.BLOCKED_BY_ACTIVATION, why_inactive)
                    )
                else:
                    out.append(CatalogEntry(spec=spec))
        return out

    def _tool_entries(
        self,
        pid: str,
        plugin: dict[str, Any],
        config: AgentConfig,
        index: dict[str, Any],
        inactive: bool,
        why_inactive: str,
    ) -> list[CatalogEntry]:
        out: list[CatalogEntry] = []
        servers = {
            name: str(s["id"])
            for s in plugin.get("mcpServers", [])
            if "/" not in str(s.get("id", ""))
            for name in _server_names(s)
        }
        reach = [] if inactive else self._reachable_via(plugin)
        for tool in sorted(str(t) for t in plugin.get("tools", [])):
            owner = None if inactive else index.get(tool)
            server = (owner.server if owner else servers.get(tool, "")) or _LEGACY_SERVER
            ref = f"plugin/{pid}/{server}/{tool}"
            info = self.registry.tools.get(tool) if self.registry is not None else None
            spec = ToolSpec(
                ref=ref,
                alias=alias_for(ref, ToolKind.PLUGIN),
                kind=ToolKind.PLUGIN,
                description=info.description if info else "",
                input_schema=dict(info.input_schema) if info else {},
                risk=RiskClass.R2,
                min_autonomy=Autonomy.RUN_SOLVERS,
            )
            blocked: tuple[Availability, str] | None = None
            if inactive:
                blocked = (Availability.BLOCKED_BY_ACTIVATION, why_inactive)
            elif owner is None:
                blocked = (
                    Availability.BLOCKED_BY_ACTIVATION,
                    "not served by exactly one of its plugin's own servers",
                )
            elif self._unserved(tool, owner.server):
                blocked = (
                    Availability.BLOCKED_BY_ACTIVATION,
                    "its server is unreachable or was set aside; ask the operator",
                )
            elif not config.tool_allowed(tool):
                blocked = (
                    Availability.BLOCKED_BY_DRAWER,
                    f"ask the owner to enable tool {tool} in Your agent",
                )
            elif not at_least(config.autonomy, spec.min_autonomy):
                blocked = (
                    Availability.BLOCKED_BY_AUTONOMY,
                    f"needs autonomy {spec.min_autonomy.value}; the owner raises it in Your agent",
                )
            if blocked is None:
                out.append(CatalogEntry(spec=spec, reachable_via=reach))
            else:
                out.append(
                    CatalogEntry(
                        spec=spec, availability=blocked[0], reason=blocked[1], reachable_via=reach
                    )
                )
        return out

    def _unserved(self, tool: str, server: str) -> bool:
        """True when discovery has run and this tool is not served by its plugin's own server."""
        reg = self.registry
        if reg is None or not (reg.tools or reg.unreachable or reg.quarantined):
            return False
        info = reg.tools.get(tool)
        return info is None or bool(server and info.server != server)

    @staticmethod
    def _reachable_via(plugin: dict[str, Any]) -> list[ReachableVia]:
        """The (capability, mode) pairs whose governed workflow can run the plugin's tools."""
        pairs: list[ReachableVia] = []
        for case in sorted(str(c) for c in plugin.get("capabilities", [])):
            if not optional_extension("workflows", case):
                continue
            modes = {
                str(optional_extension("default_modes", case) or "default"),
                *map(str, optional_extension("queue_modes", case) or []),
            }
            pairs += [ReachableVia(capability=case, mode=mode) for mode in sorted(modes)]
        return pairs

    @staticmethod
    def _doc_spec(ref: str, kind: ToolKind, description: str) -> ToolSpec:
        return ToolSpec(
            ref=ref,
            alias=alias_for(ref, kind),
            kind=kind,
            description=description,
            risk=RiskClass.R0,
            min_autonomy=Autonomy.OBSERVE,
        )

    @staticmethod
    def _blocked(spec: ToolSpec, availability: Availability, reason: str) -> CatalogEntry:
        return CatalogEntry(spec=spec, availability=availability, reason=reason)

    # rendering -----------------------------------------------------------------------------
    def render_block(self, entries: Sequence[CatalogEntry]) -> str:
        """The one tool block every provider sees. Full portable schemas for the platform ops,
        one-liners for plugin tools, skills and knowledge (``catalog.describe`` gives the rest).
        The text depends on the entries alone: no provider or model name, no address."""
        ordered = sorted(entries, key=lambda e: (_KIND_ORDER[e.spec.kind], e.spec.ref))
        lines = ["TOOLS (name a tool by its alias, exactly as written)"]
        sections: tuple[tuple[str, frozenset[ToolKind]], ...] = (
            ("PLATFORM OPS (full schema)", _OP_KINDS),
            (
                "PLUGIN TOOLS (run only through governed runs; catalog.describe gives the schema)",
                frozenset({ToolKind.PLUGIN}),
            ),
            ("SKILLS (skill.read)", frozenset({ToolKind.SKILL})),
            ("KNOWLEDGE (kb.read)", frozenset({ToolKind.KB})),
        )
        for title, kinds in sections:
            group = [e for e in ordered if e.spec.kind in kinds]
            if not group:
                continue
            lines.append(f"== {title} ==")
            for entry in group:
                lines.append(self._entry_line(entry))
                if entry.spec.kind in _OP_KINDS:
                    schema = json.dumps(
                        entry.spec.input_schema, sort_keys=True, separators=(",", ":")
                    )
                    lines.append(f"  schema: {schema}")
        return "\n".join(lines)

    @staticmethod
    def _entry_line(entry: CatalogEntry) -> str:
        spec = entry.spec
        head = f"{spec.alias} [{spec.risk.value}] {spec.ref}"
        text = _line(spec.description)
        line = f"{head}: {text}" if text else head
        if spec.kind is ToolKind.PLUGIN:
            if entry.reachable_via:
                via = ", ".join(f"{v.capability}/{v.mode}" for v in entry.reachable_via)
                line += f" (run via {via})"
            else:
                line += " (no governed run reaches it)"
        if entry.availability is not Availability.AVAILABLE:
            line += f" [{entry.availability.value}: {_line(entry.reason, 100)}]"
        return line

    def digest(self, block: str) -> str:
        """SHA-256 of the rendered block: equal states give equal digests by construction."""
        return hashlib.sha256(block.encode("utf-8")).hexdigest()

    # the agent-facing view -----------------------------------------------------------------
    def agent_view(self, entries: Sequence[CatalogEntry]) -> list[dict[str, Any]]:
        """Ids, descriptions, schemas and risk only. No ``ToolInfo.url``, no server address, no
        ``server_version``: the registry is read for descriptions and schemas, never copied."""
        return [
            {
                "id": e.spec.ref,
                "alias": e.spec.alias,
                "kind": e.spec.kind.value,
                "description": e.spec.description,
                "inputSchema": e.spec.input_schema,
                "risk": e.spec.risk.value,
                "minAutonomy": e.spec.min_autonomy.value,
                "availability": e.availability.value,
                "reason": e.reason,
                "reachableVia": [
                    {"capability": v.capability, "mode": v.mode} for v in e.reachable_via
                ],
            }
            for e in entries
        ]

    def find(
        self, room: RoomService, person: str, ref_or_alias: str, audience: str = "owner"
    ) -> CatalogEntry:
        for entry in self.for_person(room, person, audience):
            if ref_or_alias in (entry.spec.ref, entry.spec.alias):
                return entry
        raise NotFound(f"no catalog entry {ref_or_alias!r}")


# --- reads -----------------------------------------------------------------------------------

_catalog = ToolCatalog()


def configure_catalog(catalog: ToolCatalog) -> None:
    """Point the read ops at the catalog the API serves (it holds the live registry)."""
    global _catalog
    _catalog = catalog


def get_catalog() -> ToolCatalog:
    return _catalog


def _family(kind: ToolKind) -> str:
    return "platform" if kind in _OP_KINDS else kind.value


async def catalog_search(
    room: RoomService, person: str, *, query: str, family: str = ""
) -> dict[str, Any]:
    """Entries whose ref, alias or words match every word of the query."""
    words = [w for w in re.split(r"\W+", query.lower()) if w]
    hits: list[CatalogEntry] = []
    for entry in _catalog.for_person(room, person):
        if family and _family(entry.spec.kind) != family:
            continue
        haystack = f"{entry.spec.ref} {entry.spec.alias} {entry.spec.description}".lower()
        if all(w in haystack for w in words):
            hits.append(entry)
    shown = hits[:SEARCH_LIMIT]
    return {
        "entries": [
            {
                "ref": e.spec.ref,
                "alias": e.spec.alias,
                "kind": e.spec.kind.value,
                "description": _line(e.spec.description),
                "availability": e.availability.value,
                "reason": e.reason,
            }
            for e in shown
        ],
        "more": len(hits) > len(shown),
    }


async def catalog_describe(room: RoomService, person: str, *, ref: str) -> dict[str, Any]:
    """One entry in full, with its schema."""
    return _catalog.agent_view([_catalog.find(room, person, ref)])[0]


async def _read_document(
    room: RoomService, person: str, ref: str, kind: ToolKind, resource_kind: str
) -> dict[str, Any]:
    from .plugin_dispatch import _resource  # pyright: ignore[reportPrivateUsage]

    entry = _catalog.find(room, person, ref)
    if entry.spec.kind is not kind:
        raise NotFound(f"{ref!r} is not a {resource_kind}")
    if entry.availability is not Availability.AVAILABLE:
        raise Denied(entry.reason, availability=entry.availability.value)
    resource_id = entry.spec.ref.split("/", 2)[2]
    found = _resource(room, personal_agent_for(person), resource_kind, resource_id)
    return {"ref": entry.spec.ref, **found}


async def skill_read(room: RoomService, person: str, *, ref: str) -> dict[str, Any]:
    return await _read_document(room, person, ref, ToolKind.SKILL, "skill")


async def kb_read(room: RoomService, person: str, *, ref: str) -> dict[str, Any]:
    return await _read_document(room, person, ref, ToolKind.KB, "knowledge")


READ_BODIES: dict[str, platform_tools.OpBody] = {
    "catalog.search": catalog_search,
    "catalog.describe": catalog_describe,
    "skill.read": skill_read,
    "kb.read": kb_read,
}


# Skill and knowledge bodies are plugin-authored text (class B): reading one taints the turn.
TAINTING_READS = frozenset({"skill.read", "kb.read"})


def as_op(ref: str, read: platform_tools.OpBody) -> platform_tools.OpBody:
    """A catalog read in the ToolHost's op shape: ``(OpContext, args) -> OpResult``."""

    async def body(ctx: platform_tools.OpContext, args: dict[str, Any]) -> platform_tools.OpResult:
        data = await read(ctx.room, ctx.person, **args)
        shown = data.get("ref") or args.get("query") or ""
        return platform_tools.OpResult(
            summary=f"{ref} {shown}".strip(), data=data, taint=ref in TAINTING_READS
        )

    body.__doc__ = read.__doc__
    body.read = read  # type: ignore[attr-defined]  # the plain read, for tests
    return body


def register_read_ops(
    register: Callable[..., Callable[[platform_tools.OpBody], platform_tools.OpBody]] | None = None,
) -> None:
    """Register the four reads with the ops registry's ``op`` decorator (WS-L), with the schema
    the model is shown, so the ToolHost validates the same arguments the prompt describes."""
    decorator = register if register is not None else platform_tools.op
    for spec in READ_SPECS:
        decorator(
            spec.ref,
            risk=spec.risk,
            min_autonomy=spec.min_autonomy,
            schema=spec.input_schema,
            description=spec.description,
            label=spec.ref,
        )(as_op(spec.ref, READ_BODIES[spec.ref]))


with contextlib.suppress(NotImplementedError):  # the ops registry may not have landed yet
    register_read_ops()
