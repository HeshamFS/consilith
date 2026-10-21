"""One invocation per process, with explicit bidirectional capability RPC."""

from __future__ import annotations

import argparse
import asyncio
import base64
import concurrent.futures
import hashlib
import importlib
import importlib.metadata
import importlib.util
import inspect
import json
import sys
import tempfile
import threading
from pathlib import Path
from types import ModuleType
from typing import Any

from packaging.requirements import Requirement

from .codec import (
    decode,
    encode,
    error_payload,
    exception_from,
    read_message,
    write_message,
)

_HASH_CACHE = Path(tempfile.gettempdir()) / "consilith-plugin-file-hashes.json"
try:
    _hashes = json.loads(_HASH_CACHE.read_text())
except (OSError, ValueError):
    _hashes = {}


def content_hash(path: Path) -> str:
    """Cache byte hashing by identity; every invocation inventories every asset."""
    stat = path.stat()
    signature = [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino]
    key = str(path.resolve())
    value = _hashes.get(key, {})
    if value.get("stat") == signature:
        return value["digest"]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    _hashes[key] = {"stat": signature, "digest": digest}
    return digest


def files_digest(root: Path) -> str:
    digest = hashlib.sha256()
    files = [root] if root.is_file() else sorted(root.rglob("*"))
    for path in files:
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_symlink():
            raise ValueError("scientific package contains a symbolic link")
        if path.is_file():
            name = path.name if root.is_file() else path.relative_to(root).as_posix()
            digest.update(name.encode() + b"\0" + content_hash(path).encode() + b"\0")
    return digest.hexdigest()


def dependencies(names: list[str]) -> dict:
    """Bind dependency versions and actual installed code/assets, including editables."""
    result = {}
    pending = list(names)
    selected_extras: dict[str, set[str]] = {}
    while pending:
        requirement = Requirement(pending.pop())
        if requirement.marker and not requirement.marker.evaluate():
            continue
        name = requirement.name.lower().replace("_", "-")
        extras = set(requirement.extras) | {""}
        if name in result and extras <= selected_extras[name]:
            continue
        selected_extras[name] = extras | selected_extras.get(name, set())
        distribution = importlib.metadata.distribution(name)
        parts = {}
        for item in distribution.files or []:
            if "__pycache__" in item.parts or item.suffix in {".pyc", ".pyo"}:
                continue
            path = Path(str(distribution.locate_file(item)))
            if path.is_symlink():
                raise ValueError("dependency contains a symbolic link")
            if path.is_file() and "dist-info" not in str(item) and not item.name.endswith(".pth"):
                parts[str(item)] = content_hash(path)
        # Editable scientific dependencies have only metadata in their wheel RECORD.
        top_level = distribution.read_text("top_level.txt") or name.replace("-", "_")
        for module_name in top_level.splitlines():
            try:
                spec = importlib.util.find_spec(module_name.strip())
            except (ImportError, ValueError):
                spec = None
            if spec and spec.origin and spec.origin not in {"built-in", "frozen"}:
                origin = Path(spec.origin)
                parts["module:" + module_name] = files_digest(
                    origin.parent if spec.submodule_search_locations else origin
                )
        result[name] = {"version": distribution.version, "files": parts}
        for declared in distribution.requires or []:
            dependency = Requirement(declared)
            if dependency.marker and not any(
                dependency.marker.evaluate({"extra": extra}) for extra in extras
            ):
                continue
            dependency.marker = None
            pending.append(str(dependency))
    return result


def prepare(reference: str) -> tuple[dict, dict, dict]:
    module_name, separator, factory_name = reference.partition(":")
    if not separator or not module_name or factory_name.startswith("_"):
        raise ValueError("extension must name a public module:factory")
    module = importlib.import_module(module_name)
    sections = getattr(module, factory_name)()
    assert module.__file__ is not None
    root = Path(module.__file__).resolve().parent
    manifest = sections.get("plugin", {}).get("manifest", {})
    package_id = manifest.get("id", module_name)
    # Role factories are deterministic zero-argument metadata, never execution ports.
    # Resolve once in the isolated worker so room admission needs no worker round trips.
    from consilith_contracts import AgentConfig

    sections["roles"] = {
        role: AgentConfig.model_validate(value() if callable(value) else value)
        for role, value in sections.get("roles", {}).items()
    }

    def resource(path: str) -> Path:
        target = (root / path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise ValueError("plugin resource is outside its package or missing")
        return target

    skills = sections.setdefault("skill_documents", {})
    for entry in manifest.get("skills", []):
        skills[entry["id"]] = resource(entry["path"]).read_text(encoding="utf-8")
    for path in sections.pop("skill_roots", {}).values():
        folder = Path(path).resolve()
        if not folder.is_relative_to(root):
            raise ValueError("skill root is outside package")
        for skill in folder.glob("*/SKILL.md"):
            skills[skill.parent.name] = skill.read_text(encoding="utf-8")
    referee = sections.setdefault("referee_skill_documents", {})
    for key, path in sections.pop("referee_skills", {}).items():
        referee[key] = resource(path).read_text(encoding="utf-8")
    documents = sections.setdefault("knowledge_documents", {})
    for entry in manifest.get("knowledge", []):
        payload = resource(entry["path"]).read_bytes()
        media = entry.get("mediaType", "text/plain")
        document = {
            "title": entry.get("title", entry["id"]),
            "mediaType": media,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "packageId": package_id,
        }
        if media.startswith("text/") or media in {"application/json", "application/yaml"}:
            document["content"] = payload.decode("utf-8")
        else:
            document["contentBase64"] = base64.b64encode(payload).decode("ascii")
        documents[entry["id"]] = document
    dependency_names = ["consilith-plugin-runtime", *manifest.get("packageDependencies", [])]
    distribution_name = manifest.get("distribution", module_name.replace("_", "-"))
    if distribution_name:
        try:
            distribution = importlib.metadata.distribution(distribution_name)
        except importlib.metadata.PackageNotFoundError:
            distribution = None
        if distribution is not None:
            dependency_names.extend(distribution.requires or [])
    lock = dependencies(dependency_names)
    pin = {
        "entry_point": reference,
        "version": manifest.get("version", getattr(module, "__version__", "0")),
        "contract_version": str(getattr(module, "CONTRACT_VERSION", "1")),
        "source": files_digest(root),
        "dependencies": lock,
    }
    digest = hashlib.sha256(
        json.dumps(pin, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    metadata = {**pin, "digest": digest, "manifest": manifest}
    _HASH_CACHE.write_text(json.dumps(_hashes))
    functions = {}

    def descriptor(value: Any, path: tuple[str, ...]) -> Any:
        if inspect.isfunction(value) or inspect.ismethod(value):
            functions[path] = value
            return {"$function": list(path), "async": inspect.iscoroutinefunction(value)}
        if isinstance(value, dict):
            return {"$map": {key: descriptor(item, (*path, key)) for key, item in value.items()}}
        if isinstance(value, ModuleType):
            return {
                "$object": {
                    name: descriptor(item, (*path, name))
                    for name, item in vars(value).items()
                    if not name.startswith("_")
                    and inspect.isfunction(item)
                    and item.__module__ == value.__name__
                }
            }
        return encode(value)

    return metadata, descriptor(sections, ()), functions


class Peer:
    def __init__(self, reader: Any, writer: Any):
        self.reader = reader
        self.writer = writer
        self.lock = threading.Lock()
        self.pending: dict[str, concurrent.futures.Future] = {}
        self.failure: Exception | None = None
        self.counter = 0
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        try:
            while True:
                message = read_message(self.reader)
                with self.lock:
                    future = self.pending.pop(message["id"])
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    if "error" in message:
                        future.set_exception(exception_from(message["error"]))
                    else:
                        future.set_result(decode(message["value"], self.port))
                except Exception as exc:
                    # The response already owns this future: it is no longer in pending.
                    # A schema/decode failure must complete it, not strand its awaiting caller.
                    future.set_exception(exc)
        except Exception as exc:
            with self.lock:
                self.failure = exc
                pending = list(self.pending.values())
                self.pending.clear()
            for future in pending:
                if future.set_running_or_notify_cancel():
                    future.set_exception(exc)

    def start_callback(self, handle: str, method: str, args: tuple, kwargs: dict):
        future: concurrent.futures.Future = concurrent.futures.Future()
        with self.lock:
            if self.failure is not None:
                future.set_exception(self.failure)
                return future
            key = str(self.counter)
            self.counter += 1
            self.pending[key] = future
            write_message(
                self.writer,
                {
                    "type": "callback",
                    "id": key,
                    "handle": handle,
                    "method": method,
                    "args": encode(args),
                    "kwargs": encode(kwargs),
                },
            )
        return future

    def callback(self, handle: str, method: str, args: tuple, kwargs: dict):
        return self.start_callback(handle, method, args, kwargs).result()

    def port(self, marker: dict):
        if "$capability" not in marker:
            raise ValueError("unknown host capability")
        return Port(self, marker)


class Port:
    def __init__(self, peer: Peer, marker: dict):
        self._peer = peer
        self._handle = marker["$capability"]
        self._methods = marker["methods"]
        self._attributes = {
            key: decode(value, peer.port) for key, value in marker.get("attributes", {}).items()
        }

    def __getattr__(self, name):
        if name in self._attributes:
            return self._attributes[name]
        if name not in self._methods:
            raise AttributeError("operation outside granted host capability")
        if self._methods[name]:

            async def async_method(*args, **kwargs):
                return await asyncio.wrap_future(
                    self._peer.start_callback(self._handle, name, args, kwargs)
                )

            return async_method
        else:

            def sync_method(*args, **kwargs):
                return self._peer.callback(self._handle, name, args, kwargs)

            return sync_method

    def __call__(self, *args, **kwargs):
        return self.__getattr__("__call__")(*args, **kwargs)

    async def __aenter__(self):
        return await self.__getattr__("__aenter__")()

    async def __aexit__(self, exc_type, exc, traceback):
        return await self.__getattr__("__aexit__")(None, None, None)


async def await_value(value):
    return await value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--entry-point", required=True)
    args = parser.parse_args()
    reader, writer = sys.stdin.buffer, sys.stdout.buffer
    # Plugins cannot corrupt the protocol with prints, nor expose their logs to clients.
    sys.stdout = sys.stderr
    try:
        request = read_message(reader)
        metadata, sections, functions = prepare(args.entry_point)
        if request["op"] == "describe":
            value = {"metadata": metadata, "sections": sections}
        elif request["op"] == "invoke":
            if request.get("expected_digest") != metadata["digest"]:
                raise ValueError("scientific package changed after admission")
            function = functions.get(tuple(request["path"]))
            if function is None:
                raise PermissionError("function was not exported by the plugin")
            peer = Peer(reader, writer)
            result = function(
                *decode(request["args"], peer.port), **decode(request["kwargs"], peer.port)
            )
            if inspect.isawaitable(result):
                result = asyncio.run(await_value(result))
            value = encode(result)
        else:
            raise ValueError("unknown worker operation")
        write_message(writer, {"type": "result", "value": value})
    except Exception as exc:
        write_message(
            writer,
            {"type": "error", "error": error_payload(exc)},
        )


if __name__ == "__main__":
    main()
