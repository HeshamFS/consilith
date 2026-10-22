"""Host-side proxies. Scientific implementations are never imported here."""

from __future__ import annotations

import asyncio
import atexit
import inspect
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
from collections.abc import Mapping
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from consilith_contracts.plugins import PluginManifest
from pydantic import ValidationError

from .codec import (
    ATTRIBUTES,
    METHODS,
    RemoteFailure,
    decode,
    encode,
    error_payload,
    exception_from,
    read_message,
    write_message,
)
from .limits import plugin_timeout_s


def async_callable(function: Any) -> bool:
    return inspect.iscoroutinefunction(function) or (
        callable(function) and inspect.iscoroutinefunction(function.__call__)
    )


class HostCapabilities:
    def __init__(self, allowed_ports: set[str] | None = None) -> None:
        self.handles: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.callback_lock = threading.RLock()
        self.allowed_ports = set(METHODS | ATTRIBUTES) if allowed_ports is None else allowed_ports
        self.activities: dict[int, Any] = {}
        self.errors: dict[str, Exception] = {}

    def expose(self, value: Any) -> dict:
        if hasattr(value, "__aenter__") and hasattr(value, "__aexit__"):

            async def enter():
                result = await value.__aenter__()
                self.activities[id(value)] = value
                return result

            async def leave(*args):
                try:
                    return await value.__aexit__(*args)
                finally:
                    self.activities.pop(id(value), None)

            methods = {"__aenter__": enter, "__aexit__": leave}
            attributes = {}
        elif callable(value):
            methods = {"__call__": value}
            attributes = {}
        else:
            methods = {
                name: getattr(value, name)
                for name in METHODS & self.allowed_ports
                if callable(getattr(value, name, None))
            }
            if not methods:
                raise TypeError(
                    f"object does not implement a neutral capability port: {type(value).__name__}"
                )
            attributes = {
                name: encode(getattr(value, name), self.expose)
                for name in ATTRIBUTES & self.allowed_ports
                if hasattr(value, name)
            }
        with self.lock:
            handle = str(len(self.handles))
            self.handles[handle] = methods
        return {
            "$capability": handle,
            "methods": {name: async_callable(fn) for name, fn in methods.items()},
            "attributes": attributes,
        }

    def asynchronous(self, message: dict) -> bool:
        methods = self.handles.get(str(message.get("handle", "")), {})
        return async_callable(methods.get(str(message.get("method", ""))))

    def callback(self, message: dict) -> Any:
        methods = self.handles.get(str(message.get("handle", "")), {})
        method = message.get("method")
        if method not in methods:
            raise PermissionError("callback is outside the granted capability")
        args = decode(message["args"])
        kwargs = decode(message["kwargs"])
        if async_callable(methods[method]):
            return methods[method](*args, **kwargs)
        with self.callback_lock:
            return methods[method](*args, **kwargs)

    async def close(self):
        for manager in list(self.activities.values()):
            await manager.__aexit__(None, None, None)
        self.activities.clear()


async def await_value(value):
    return await value


HASH_DIR_ENV = "CONSILITH_PLUGIN_HASH_DIR"
_hash_dir: Path | None = None
_hash_dir_lock = threading.Lock()


def plugin_hash_dir() -> str | None:
    """A private, unguessable directory for the workers' file-hash cache, one per host process.

    ``mkdtemp`` creates it with mode 700 where the OS has modes, and the random part of its name
    is 96 bits. Workers verify its owner and mode again before they trust any cached digest, and
    without it they recompute every hash. It is removed when the host exits.
    """
    global _hash_dir
    with _hash_dir_lock:
        if _hash_dir is None:
            try:
                path = Path(tempfile.mkdtemp(prefix=f"consilith-plugin-{secrets.token_hex(12)}-"))
                path.chmod(0o700)
            except OSError:
                return None
            atexit.register(shutil.rmtree, path, ignore_errors=True)
            _hash_dir = path
        return str(_hash_dir)


def safe_environment() -> dict[str, str]:
    allowed = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "LOCALAPPDATA",
        "LANG",
        "LC_ALL",
    }
    result = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    # Local compatibility tests may install an unrelated package by adding its directory.
    # This carries import paths, never the room's environment or credential configuration.
    result["PYTHONPATH"] = os.pathsep.join(str(path) for path in sys.path if path)
    result["PYTHONDONTWRITEBYTECODE"] = "1"
    cache = plugin_hash_dir()
    if cache:
        result[HASH_DIR_ENV] = cache
    return result


def stop_process(process: subprocess.Popen) -> None:
    """Terminate only this invocation's process tree, including spawned descendants."""
    if os.name == "nt":
        if process.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=False,
            )
    else:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)


@contextmanager
def channel(reference: str, timeout_s: float):
    if reference.startswith("tcp://"):
        address = urlsplit(reference)
        if not address.hostname or not address.port or address.username or address.password:
            raise ValueError("plugin endpoint must be tcp://host:port")
        connection = socket.create_connection((address.hostname, address.port), timeout=timeout_s)
        connection.settimeout(timeout_s)
        try:
            with connection.makefile("rb") as reader, connection.makefile("wb") as writer:
                yield reader, writer
        finally:
            connection.close()
    else:
        process = subprocess.Popen(
            [
                (
                    getattr(sys, "_base_executable", sys.executable)
                    if os.name == "nt"
                    else sys.executable
                ),
                "-m",
                "consilith_plugin_runtime.worker",
                "--entry-point",
                reference,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=safe_environment(),
            cwd=os.path.abspath(os.curdir),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        timer = threading.Timer(timeout_s, stop_process, args=(process,))
        timer.daemon = True
        timer.start()
        try:
            yield process.stdout, process.stdin
        finally:
            timer.cancel()
            stop_process(process)
            process.wait(timeout=5)
            if process.stdin:
                process.stdin.close()
            if process.stdout:
                process.stdout.close()


def exchange(
    reference: str,
    request: dict,
    timeout_s: float,
    ports: HostCapabilities | None = None,
    loop: asyncio.AbstractEventLoop | None = None,
) -> Any:
    ports = ports or HostCapabilities()
    tokens = json.loads(os.environ.get("CONSILITH_PLUGIN_TOKENS", "{}"))
    request = {**request, "token": tokens.get(reference, "")}
    with channel(reference, timeout_s) as (reader, writer):
        lock = threading.Lock()

        def send(value: dict) -> None:
            with lock:
                write_message(writer, value)

        def reply(message: dict, result: Any = None, error: Exception | None = None) -> None:
            if error is not None:
                ports.errors[message["id"]] = error
                send(
                    {
                        "type": "callback_result",
                        "id": message["id"],
                        "error": {**error_payload(error), "origin": message["id"]},
                    }
                )
            else:
                send(
                    {
                        "type": "callback_result",
                        "id": message["id"],
                        "value": encode(result, ports.expose),
                    }
                )

        async def invoke_callback(message: dict) -> Any:
            value = (
                ports.callback(message)
                if ports.asynchronous(message)
                else await asyncio.to_thread(ports.callback, message)
            )
            return await value if inspect.isawaitable(value) else value

        send(request)
        while True:
            message = read_message(reader)
            if message.get("type") == "callback":
                if loop is not None:
                    future = asyncio.run_coroutine_threadsafe(invoke_callback(message), loop)

                    def completed(done: Any, original: dict = message) -> None:
                        try:
                            reply(original, done.result())
                        except Exception as exc:
                            reply(original, error=exc)

                    future.add_done_callback(completed)
                else:
                    try:
                        value = ports.callback(message)
                        if inspect.isawaitable(value):
                            value = asyncio.run(await_value(value))
                        reply(message, value)
                    except Exception as exc:
                        reply(message, error=exc)
                continue
            if message.get("type") == "error":
                payload = message.get("error", {})
                original = ports.errors.get(str(payload.get("origin", "")))
                raise original if original is not None else exception_from(payload)
            if message.get("type") != "result":
                raise RemoteFailure("invalid scientific worker response")
            return message["value"]


class CatalogMismatch(ValueError):
    """A plugin worker's manifest claims more than the operator catalog grants it."""


BOUND_SURFACES = ("mcpServers", "tools", "policyPorts")
_FROM_CATALOG: Any = object()


def catalog_binding(
    reference: str, path: str | os.PathLike[str] | None = None
) -> Mapping[str, Any] | None:
    """What the operator catalog grants one plugin endpoint, read from the catalog file.

    ``None`` means no catalog is in force or the reference is not in it (a dev subprocess). An
    endpoint the catalog lists without a ``binding`` gets an empty one. The file is the one the
    room and referee already read through ``read_catalog``; tokens in it are never returned.
    """
    target = path or os.environ.get("CONSILITH_PLUGIN_CATALOG")
    if not target:
        return None
    document = json.loads(Path(target).read_bytes())
    for plugin in document.get("plugins", []):
        if plugin.get("reference") == reference:
            binding = plugin.get("binding")
            return binding if isinstance(binding, dict) else {}
    return None


def _grants(value: Any) -> list[Any] | None:
    return list(value) if isinstance(value, list) else None


def check_manifest(
    manifest: Any, binding: Mapping[str, Any], *, strict: bool = False
) -> PluginManifest:
    """Refuse a worker manifest that goes beyond the operator catalog's grant.

    The grant is a ceiling on what the worker may claim: MCP servers (and the tools on each),
    plugin tools and host policy ports. A surface the catalog does not bind is not checked,
    unless ``strict`` (production), where every surface must be bound.
    """
    try:
        parsed = PluginManifest.model_validate(manifest)
    except ValidationError:
        raise CatalogMismatch("the plugin manifest is not valid") from None
    servers = _grants(binding.get("mcpServers"))
    if strict:
        unbound = [name for name in BOUND_SURFACES if name not in binding]
        if servers is not None and any(
            not isinstance(server, dict) or "tools" not in server for server in servers
        ):
            unbound.append("mcpServers.tools")
        if unbound:
            raise CatalogMismatch(
                f"the operator catalog does not bind {', '.join(unbound)} for this plugin"
            )
    if servers is not None:
        allowed: dict[str, set[str] | None] = {}
        for server in servers:
            if isinstance(server, dict) and isinstance(server.get("id"), str):
                tools = _grants(server.get("tools"))
                allowed[server["id"]] = None if tools is None else {str(t) for t in tools}
        for declared_server in parsed.mcp_servers:
            if declared_server.id not in allowed:
                raise CatalogMismatch(
                    f"the plugin declares MCP server {declared_server.id!r}, which the operator "
                    "catalog does not assign to it"
                )
            ceiling = allowed[declared_server.id]
            extra = set(declared_server.tools) - ceiling if ceiling is not None else set()
            if extra:
                raise CatalogMismatch(
                    f"the plugin declares tool(s) {sorted(extra)} on MCP server "
                    f"{declared_server.id!r} that the operator catalog does not grant"
                )
    for field, declared in (("tools", parsed.tools), ("policyPorts", parsed.policy_ports)):
        grant = _grants(binding.get(field))
        if grant is None:
            continue
        extra = set(declared) - {str(item) for item in grant}
        if extra:
            noun = "tool(s)" if field == "tools" else "host port(s)"
            raise CatalogMismatch(
                f"the plugin declares {noun} {sorted(extra)} "
                "that the operator catalog does not grant"
            )
    return parsed


def load(
    reference: str,
    timeout_s: float | None = None,
    binding: Mapping[str, Any] | None = _FROM_CATALOG,
) -> tuple[dict, dict]:
    """Describe a plugin release. The operator catalog, not the worker, owns what it may claim:
    its MCP servers, tools and host ports (``binding``, by default read from the catalog file)."""
    timeout_s = plugin_timeout_s() if timeout_s is None else timeout_s
    if binding is _FROM_CATALOG:
        binding = catalog_binding(reference)
    strict = os.environ.get("CONSILITH_ENV") == "production"
    description = exchange(reference, {"op": "describe"}, timeout_s)
    metadata = description["metadata"]
    # Until the manifest is checked, grant nothing beyond what the worker declared.
    granted_ports: set[str] = set(metadata.get("manifest", {}).get("policyPorts", []))

    def bind(marker: dict) -> Any:
        if "$function" not in marker:
            raise ValueError("unknown scientific descriptor marker")
        path = marker["$function"]

        def invoke(args: tuple, kwargs: dict, loop: asyncio.AbstractEventLoop | None = None) -> Any:
            ports = HostCapabilities(set(granted_ports))
            request = {
                "op": "invoke",
                "path": path,
                "expected_digest": metadata["digest"],
                "args": encode(args, ports.expose),
                "kwargs": encode(kwargs, ports.expose),
            }
            try:
                return decode(exchange(reference, request, timeout_s, ports, loop))
            finally:
                if ports.activities:
                    if loop:
                        asyncio.run_coroutine_threadsafe(ports.close(), loop).result(timeout=5)
                    else:
                        asyncio.run(ports.close())

        if marker.get("async"):

            async def async_function(*args: Any, **kwargs: Any) -> Any:
                loop = asyncio.get_running_loop()
                return await asyncio.to_thread(invoke, args, kwargs, loop)

            function = async_function
        else:

            def sync_function(*args: Any, **kwargs: Any) -> Any:
                return invoke(args, kwargs)

            function = sync_function
        function.__name__ = str(path[-1])
        return function

    sections = decode(description["sections"], bind)
    # A catalog written before bindings existed binds nothing: it is read as before, except in
    # production. Once any surface is bound, the worker must return a manifest to check.
    if binding is not None and (strict or any(name in binding for name in BOUND_SURFACES)):
        # Both copies of the manifest the room may read are held to the grant. The one the
        # registry trusts (sections first, as load_extensions reads it) sets the host ports.
        declared = [
            raw
            for raw in (sections.get("plugin", {}).get("manifest"), metadata.get("manifest"))
            if raw
        ]
        if not declared:
            raise CatalogMismatch(
                f"{reference}: a plugin bound by the catalog must return a manifest"
            )
        try:
            checked = [check_manifest(raw, binding, strict=strict) for raw in declared]
        except CatalogMismatch as exc:
            raise CatalogMismatch(f"{reference}: {exc}") from None
        granted_ports.clear()
        granted_ports.update(checked[0].policy_ports)
    return metadata, sections
