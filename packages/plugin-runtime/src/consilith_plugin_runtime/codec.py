"""JSON values and explicit capability handles. No pickle or arbitrary host imports."""

from __future__ import annotations

import base64
import dataclasses
import enum
import importlib
import inspect
import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel

MAX_LINE = 64 * 1024 * 1024
METHODS = frozenset(
    {
        "snapshot",
        "artifact",
        "current_results",
        "result",
        "run_tool",
        "formula",
        "review",
        "agent_for",
        "propose",
        "config",
        "lessons",
        "has_proposals",
        "warn",
        "resolve",
        "open_tension",
        "update_tension",
        "workflow",
        "tool",
        "activity",
        "public_skip",
        "edit_or_propose",
        "ruled_out",
        "note",
    }
)
ATTRIBUTES = frozenset({"model", "owners", "proposals", "warnings", "tensions", "revisions"})


class RemoteFailure(RuntimeError):
    pass


def error_payload(error: Exception) -> dict:
    payload = {"kind": type(error).__name__, "message": str(error)[:2000]}
    from consilith_contracts.cases import SpecError

    if isinstance(error, SpecError):
        payload["field"] = error.field
        payload["message"] = error.message
    origin = getattr(error, "rpc_origin", None)
    if isinstance(origin, str):
        payload["origin"] = origin
    return payload


def exception_from(payload: dict) -> Exception:
    from consilith_contracts.analysis import AnalysisError
    from consilith_contracts.cases import SpecError
    from consilith_contracts.execution import PluginExecutionError

    message = str(payload.get("message", "plugin operation failed"))[:2000]
    if payload.get("kind") == "SpecError":
        return SpecError(str(payload.get("field", "")), message)
    classes = {
        "ValueError": ValueError,
        "TypeError": TypeError,
        "LookupError": LookupError,
        "TimeoutError": TimeoutError,
        "PermissionError": PermissionError,
        "Denied": PermissionError,
        "AnalysisError": AnalysisError,
        "PluginExecutionError": PluginExecutionError,
        "ScientificExecutionError": PluginExecutionError,
    }
    error = classes.get(str(payload.get("kind", "")), RemoteFailure)(message)
    if isinstance(payload.get("origin"), str):
        error.rpc_origin = payload["origin"]
    return error


def type_name(value: Any) -> str:
    cls = type(value)
    return f"{cls.__module__}:{cls.__qualname__}"


def neutral_type(name: str) -> Any:
    module, _, qualname = name.partition(":")
    if not module.startswith("consilith_contracts.") or "<" in qualname:
        raise ValueError("only neutral contract types may cross the process boundary")
    cls = importlib.import_module(module)
    for part in qualname.split("."):
        if part.startswith("_"):
            raise ValueError("private contract types are not serializable")
        cls = getattr(cls, part)
    if not inspect.isclass(cls) or not (
        issubclass(cls, (BaseModel, enum.Enum)) or dataclasses.is_dataclass(cls)
    ):
        raise ValueError("unapproved contract type")
    return cls


def encode(value: Any, capability: Callable[[Any], Any] | None = None) -> Any:
    if isinstance(value, enum.Enum):
        return {"$enum": type_name(value), "value": encode(value.value, capability)}
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, bytes):
        return {"$bytes": base64.b64encode(value).decode("ascii")}
    if isinstance(value, BaseModel):
        name = type_name(value)
        neutral_type(name)
        return {"$model": name, "value": encode(value.model_dump(mode="python"), capability)}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        name = type_name(value)
        neutral_type(name)
        return {
            "$data": name,
            "value": encode(
                {field.name: getattr(value, field.name) for field in dataclasses.fields(value)},
                capability,
            ),
        }
    if isinstance(value, tuple):
        return {"$tuple": [encode(item, capability) for item in value]}
    if isinstance(value, (set, frozenset)):
        return {"$set": [encode(item, capability) for item in value]}
    if isinstance(value, list):
        return [encode(item, capability) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("RPC dictionary keys must be strings")
        return {"$map": {key: encode(item, capability) for key, item in value.items()}}
    if capability is not None:
        return capability(value)
    raise TypeError(f"unsupported boundary value: {type(value).__name__}")


def decode(value: Any, capability: Callable[[dict], Any] | None = None) -> Any:
    if isinstance(value, list):
        return [decode(item, capability) for item in value]
    if not isinstance(value, dict):
        return value
    if "$map" in value:
        return {key: decode(item, capability) for key, item in value["$map"].items()}
    if "$bytes" in value:
        return base64.b64decode(value["$bytes"], validate=True)
    if "$tuple" in value:
        return tuple(decode(item, capability) for item in value["$tuple"])
    if "$set" in value:
        return set(decode(item, capability) for item in value["$set"])
    for tag in ("$model", "$data", "$enum"):
        if tag in value:
            cls = neutral_type(value[tag])
            body = decode(value["value"], capability)
            if tag == "$model":
                return cls.model_validate(body)
            return cls(body) if tag == "$enum" else cls(**body)
    if "$object" in value:
        return SimpleNamespace(
            **{key: decode(item, capability) for key, item in value["$object"].items()}
        )
    if capability is not None:
        return capability(value)
    raise ValueError("unknown RPC value tag")


def read_message(reader: Any) -> dict:
    line = reader.readline(MAX_LINE + 1)
    if not line:
        raise RemoteFailure("plugin worker closed the channel")
    if len(line) > MAX_LINE:
        raise RemoteFailure("plugin worker message exceeded its bound")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise RemoteFailure("invalid plugin worker message")
    return value


def write_message(writer: Any, value: dict) -> None:
    line = json.dumps(value, allow_nan=False, separators=(",", ":")).encode() + b"\n"
    if len(line) > MAX_LINE:
        raise RemoteFailure("plugin worker message exceeded its bound")
    writer.write(line)
    writer.flush()
