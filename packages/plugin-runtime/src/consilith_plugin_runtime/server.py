"""Authenticated worker supervisor; each call has a new disposable subprocess."""

from __future__ import annotations

import argparse
import contextlib
import hmac
import os
import socket
import socketserver
import subprocess
import sys
import threading
from typing import cast

from .client import safe_environment, stop_process
from .codec import read_message, write_message


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(float(os.environ.get("CONSILITH_PLUGIN_TIMEOUT", "300")))
        process = None
        timer = None
        try:
            request = read_message(self.rfile)
            secret = os.environ.get("CONSILITH_PLUGIN_TOKEN", "")
            if not secret or not hmac.compare_digest(str(request.pop("token", "")), secret):
                raise PermissionError("plugin authentication required")
            if request.get("op") == "health":
                write_message(self.wfile, {"type": "result", "value": {"ready": True}})
                return
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
                    cast(Server, self.server).entry_point,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=safe_environment(),
                start_new_session=os.name != "nt",
            )
            timer = threading.Timer(
                float(os.environ.get("CONSILITH_PLUGIN_TIMEOUT", "300")),
                stop_process,
                args=(process,),
            )
            timer.daemon = True
            timer.start()
            write_message(process.stdin, request)

            def callbacks():
                try:
                    while True:
                        reply = read_message(self.rfile)
                        if reply.get("type") != "callback_result":
                            raise ValueError("expected callback result")
                        write_message(process.stdin, reply)
                except Exception:
                    if process.poll() is None:
                        stop_process(process)

            threading.Thread(target=callbacks, daemon=True).start()
            while True:
                response = read_message(process.stdout)
                write_message(self.wfile, response)
                if response.get("type") in {"result", "error"}:
                    break
        except Exception as exc:
            with contextlib.suppress(OSError):
                write_message(
                    self.wfile,
                    {
                        "type": "error",
                        "error": {"kind": type(exc).__name__, "message": str(exc)[:1000]},
                    },
                )
        finally:
            if timer:
                timer.cancel()
            if process:
                stop_process(process)
                process.wait(timeout=5)
            with contextlib.suppress(OSError):
                self.connection.shutdown(socket.SHUT_RDWR)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, entry_point):
        self.entry_point = entry_point
        super().__init__(address, Handler)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--entry-point", required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=8840, type=int)
    args = parser.parse_args()
    if not os.environ.get("CONSILITH_PLUGIN_TOKEN"):
        raise SystemExit("CONSILITH_PLUGIN_TOKEN is required")
    with Server((args.host, args.port), args.entry_point) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
