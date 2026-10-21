"""Container process adapters. No scientific operations or host runtime files live here."""

from __future__ import annotations

import importlib
import json
import os
import sys
import urllib.request
from pathlib import Path


def main() -> None:
    role = sys.argv[1] if len(sys.argv) > 1 else "room"
    if role == "health":
        with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=2) as response:
            if response.status != 200:
                raise SystemExit(1)
        return

    import uvicorn

    if role == "room":
        from consilith_room.api.app import create_app
        from consilith_room.api.dev import write_capability
        from consilith_room.api.hub import Hub
        from consilith_room.platform.settings import get_settings

        settings = get_settings()
        hub = Hub(settings)
        if hub.dev_capability:
            write_capability(Path("/gateway-capability/dev.env"), hub.dev_capability)
        app = create_app(settings, hub)
    elif role == "referee":
        from consilith_contracts.extensions import configure_extension_loader, load_extensions
        from consilith_plugin_runtime import load
        from consilith_plugin_runtime.catalog import read_catalog

        configure_extension_loader(load)
        catalog = read_catalog()
        os.environ["CONSILITH_PLUGIN_TOKENS"] = json.dumps(catalog["tokens"])

        load_extensions(
            catalog["references"] or json.loads(os.environ.get("CONSILITH_EXTENSION_MODULES", "[]"))
        )
        from consilith_referee.service import create_app, load_or_create_key

        token = os.environ.get("CONSILITH_REFEREE_TOKEN", "")
        if len(token) < 16:
            raise SystemExit("CONSILITH_REFEREE_TOKEN must contain at least 16 characters")
        key_path = Path("/data/ed25519.pem")
        key = load_or_create_key(key_path)
        Path("/public-keys/referee.pem").write_bytes(key_path.with_suffix(".pub.pem").read_bytes())
        servers = catalog["solver_urls"] or json.loads(
            os.environ.get("CONSILITH_SOLVER_SERVERS", "{}")
        )
        app = create_app(key, "http://referee:8080", servers=servers, token=token)
    elif role == "solver":
        from mcp.server.transport_security import TransportSecuritySettings
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        module = importlib.import_module(os.environ["CONSILITH_SOLVER_MODULE"])
        hosts = json.loads(os.environ["CONSILITH_MCP_ALLOWED_HOSTS"])
        app = module.build_server().streamable_http_app(
            json_response=True,
            transport_security=TransportSecuritySettings(allowed_hosts=hosts),
        )

        async def health(_request):
            return JSONResponse({"status": "ok"})

        app.routes.append(Route("/health", health))
    else:
        raise SystemExit(f"unknown service role: {role}")
    uvicorn.run(app, host="0.0.0.0", port=8080, proxy_headers=False)


if __name__ == "__main__":
    main()
