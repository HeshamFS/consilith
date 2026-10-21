"""The room document: plain JSON, changed only by RFC 6902 patches."""

from __future__ import annotations

import copy
from typing import Any, cast

import jsonpatch  # pyright: ignore[reportMissingTypeStubs]
from consilith_contracts import CaseDef, digest
from consilith_contracts.extensions import (
    PackagePin,
    all_extensions,
    default_case,
    describe_case,
    package_pins,
    plugin_for,
    plugins_for,
    registry_operation,
)

from .errors import Conflict
from .roles import default_config

Doc = dict[str, Any]
Patch = list[dict[str, Any]]


# Room agents have no owner and edit no lane (FR-AG-5); they show a status like any agent.
# The referee is a separate service, so it is not a member of the room document.
ROOM_AGENT_IDS = ("conductor", "swarm", "solvers")


def _room_agent(aid: str) -> dict[str, Any]:
    return {"id": aid, "kind": "room", "owner": None, "status": "idle", "statusDetail": ""}


def case_defaults(case: CaseDef) -> dict[str, dict[str, Any]]:
    """Give each room its own copy of the package-declared initial input values."""
    return {
        lane.id: {
            field.name: copy.deepcopy(field.default)
            for field in lane.fields
            if field.default is not None
        }
        for lane in case.lanes
    }


@registry_operation
def empty_document(room: str, title: str, studio: str = "") -> Doc:
    cases_installed = all_extensions("cases")
    selected = studio or default_case()
    owner = plugin_for("cases", selected)
    plugins = plugins_for([owner["id"]]) if owner else []
    ids = [p["id"] for p in plugins]
    cases = {
        cid: cases_installed[cid]
        for p in plugins
        for cid in p["capabilities"]
        if cid in cases_installed
    }
    if selected not in cases:
        cases[selected] = cases_installed[selected]
    roles = {role["id"] for p in plugins for role in p["roles"]}
    roles.update(lane.default_role for case in cases.values() for lane in case.lanes)
    twins = {cid: case_defaults(case) for cid, case in cases.items()}
    return {
        "room": {
            "id": room,
            "title": title,
            "people": {},
            "agents": {aid: _room_agent(aid) for aid in ROOM_AGENT_IDS},
            "activeCase": selected,
            "activePlugins": ids,
            "plugins": plugins,
            "capabilities": [describe_case(case) for case in cases.values()],
        },
        "schemas": {cid: c.wire() for cid, c in cases.items()},
        "packagePins": package_pins(ids),
        "roleDefaults": {role: default_config(role).wire() for role in roles},
        "lanes": {cid: {ln: None for ln in c.lane_ids()} for cid, c in cases.items()},
        "twin": twins,
        "revisions": {
            cid: {lane: digest(value) for lane, value in twin.items()}
            for cid, twin in twins.items()
        },
        "results": {},
        "runs": {},
        "proposals": {},
        "decisions": {},
        "warnings": {},
        "tensions": {},
        "ledger": {},
        "memory": {"room": {}, "case": {}},
        "queue": {},
        "followups": {},
    }


@registry_operation
def activation_patch(doc: Doc, plugin_id: str) -> Patch:
    """Add a dependency closure without replacing any existing room schema or package pin."""
    cases_installed = all_extensions("cases")
    additions = plugins_for([plugin_id])
    active = list(doc["room"].get("activePlugins", []))
    fresh = [plugin for plugin in additions if plugin["id"] not in active]
    if not fresh:
        return []
    current_pins = package_pins([plugin["id"] for plugin in additions])
    old_pins = {
        PackagePin.model_validate(pin).entry_point: PackagePin.model_validate(pin).wire()
        for pin in doc.get("packagePins", [])
    }
    for pin in current_pins:
        key = PackagePin.model_validate(pin).entry_point
        previous = old_pins.get(key)
        if previous is not None and previous != pin:
            raise Conflict("activation would replace a package pinned by this room")
        old_pins[key] = pin
    capabilities = list(doc["room"].get("capabilities", []))
    patch: Patch = []
    defaults = dict(doc.get("roleDefaults", {}))
    for plugin in fresh:
        for cid in plugin["capabilities"]:
            if cid in doc.get("schemas", {}):
                raise Conflict(f"capability {cid} already belongs to this room")
            case = cases_installed[cid]
            twin = case_defaults(case)
            capabilities.append(describe_case(case))
            sections: dict[str, Any] = {
                "schemas": case.wire(),
                "lanes": {lane: None for lane in case.lane_ids()},
                "twin": twin,
                "revisions": {lane: digest(value) for lane, value in twin.items()},
            }
            for section, value in sections.items():
                patch.append({"op": "add", "path": f"/{section}/{cid}", "value": value})
        for role in plugin["roles"]:
            defaults[role["id"]] = default_config(role["id"]).wire()
    for path, value in (
        ("/room/activePlugins", active + [plugin["id"] for plugin in fresh]),
        ("/room/plugins", list(doc["room"].get("plugins", [])) + fresh),
        ("/room/capabilities", capabilities),
        ("/packagePins", list(old_pins.values())),
        ("/roleDefaults", defaults),
    ):
        patch.append({"op": "add", "path": path, "value": value})
    return patch


def snapshot_catalog(doc: Doc) -> Doc:
    """Project historical schemas without substituting current plugin definitions or pins."""
    out = copy.deepcopy(doc)
    info = out["room"]
    if "capabilities" not in info:
        capabilities: list[dict[str, Any]] = []
        for raw in out.get("schemas", {}).values():
            case = CaseDef.model_validate(raw)
            capability = case.model_dump(mode="json", by_alias=False)
            capability["roles"] = [
                {"id": role, "label": role}
                for role in dict.fromkeys(lane.default_role for lane in case.lanes)
            ]
            capabilities.append(capability)
        info["capabilities"] = capabilities
    info.setdefault("plugins", [])
    info.setdefault("activePlugins", [])
    return out


def apply(doc: Doc, patch: Patch) -> Doc:
    """Apply a patch to a copy. A failed ``test`` or a bad path raises Conflict."""
    try:
        out: Any = jsonpatch.apply_patch(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            copy.deepcopy(doc), patch, in_place=True
        )
    except (jsonpatch.JsonPatchException, jsonpatch.JsonPointerException) as exc:  # pyright: ignore[reportUnknownMemberType]
        raise Conflict(f"patch does not apply: {exc}") from exc
    return cast(Doc, out)


def get(doc: Doc, *path: str) -> Any:
    node: Any = doc
    for p in path:
        if not isinstance(node, dict) or p not in node:
            return None
        node = cast(dict[str, Any], node)[p]
    return node


def lane_revision(doc: Doc, case: str, lane: str) -> str:
    return digest(get(doc, "twin", case, lane) or {})
