"""Host-side proxies. Scientific implementations are never imported here."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import signal
import socket
import subprocess
import sys
import threading
from contextlib import contextmanager, suppress
from typing import Any
from urllib.parse import urlsplit

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


def load(reference: str, timeout_s: float | None = None) -> tuple[dict, dict]:
    timeout_s = plugin_timeout_s() if timeout_s is None else timeout_s
    description = exchange(reference, {"op": "describe"}, timeout_s)
    metadata = description["metadata"]

    def bind(marker: dict) -> Any:
        if "$function" not in marker:
            raise ValueError("unknown scientific descriptor marker")
        path = marker["$function"]

        def invoke(args: tuple, kwargs: dict, loop: asyncio.AbstractEventLoop | None = None) -> Any:
            ports = HostCapabilities(set(metadata.get("manifest", {}).get("policyPorts", [])))
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

    return metadata, decode(description["sections"], bind)
