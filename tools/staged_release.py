"""Stage isolated plugin services, validate the complete registry, then switch discovery."""

from __future__ import annotations

import hashlib
import inspect
import json
import secrets
import subprocess
import sys
from pathlib import Path

import deployment


def validator_diagnostic(stderr: str | bytes | None, candidate: dict) -> str:
    text = (
        stderr.decode(errors="replace")
        if isinstance(stderr, bytes)
        else stderr or "No validator detail"
    )
    for entry in candidate.get("plugins", []):
        token = entry.get("token")
        if token:
            text = text.replace(token, "[redacted]")
    text = "".join(
        character for character in text if character.isprintable() or character in "\n\t"
    )
    return text[-4000:]


def validate_tool_inventory(descriptors: list, inventories: dict) -> None:
    known = {tool for tools in inventories.values() for tool in tools}
    for plugin in descriptors:
        if not set(plugin.get("tools", [])) <= known:
            raise ValueError("plugin declares tools missing from MCP discovery")
        for server in plugin.get("mcpServers", []):
            if server["id"] not in inventories or not set(server.get("tools", [])) <= set(
                inventories[server["id"]]
            ):
                raise ValueError("plugin MCP server/tool declaration does not match discovery")


VALIDATE = (
    inspect.getsource(validate_tool_inventory)
    + """
import asyncio,json,os,sys,signal
from consilith_contracts.extensions import configure_extension_loader,load_extensions
from consilith_contracts.extensions import plugin_descriptors
from consilith_plugin_runtime import load
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
c=json.load(sys.stdin)
signal.alarm(150)
os.environ['CONSILITH_PLUGIN_TOKENS']=json.dumps({p['reference']:p['token'] for p in c['plugins']})
configure_extension_loader(lambda reference: load(reference, timeout_s=45))
load_extensions([p['reference'] for p in c['plugins']])
async def check():
 inventories={}
 for server,url in c['solverUrls'].items():
  async with streamable_http_client(url) as (read,write):
   async with ClientSession(read,write) as session:
    await session.initialize()
    listing=await session.list_tools()
    inventories[server]=[tool.name for tool in listing.tools]
 validate_tool_inventory(plugin_descriptors(),inventories)
asyncio.run(asyncio.wait_for(check(),60))
print('Full registry and MCP discovery validated')
signal.alarm(0)
"""
)
ACTIVATE = """
import urllib.request
with urllib.request.urlopen('http://127.0.0.1:8080/capabilities',timeout=90) as r:
 assert r.status == 200
print('Running platform accepted catalog')
"""


def atomic_bytes(path: Path, content: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".next")
    temporary.write_bytes(content)
    temporary.replace(path)


def switch_catalog(
    candidate: dict, aliases: dict, validate, activate, images: dict | None = None
) -> None:
    """Rollback both durable discovery files and running registry on activation failure."""
    catalog_path = deployment.state_dir() / "catalog/plugins.json"
    state_path = deployment.state_dir() / "active-services.json"
    old_catalog = catalog_path.read_bytes()
    old_state = state_path.read_bytes() if state_path.exists() else b"{}\n"
    images_path = deployment.state_dir() / "active-images.json"
    old_images = images_path.read_bytes() if images_path.exists() else b"{}\n"
    validate(candidate)
    try:
        deployment.publish_catalog(candidate)
        atomic_bytes(state_path, (json.dumps(aliases, indent=2) + "\n").encode())
        if images is not None:
            atomic_bytes(images_path, (json.dumps(images, indent=2) + "\n").encode())
        activate()
    except BaseException:
        atomic_bytes(catalog_path, old_catalog)
        atomic_bytes(state_path, old_state)
        atomic_bytes(images_path, old_images)
        activate()
        raise


def update(document: dict, plugin: str | None, docker: str, health) -> None:
    deployment.validate_manifest(document)
    entries = deployment.release_entries(document)
    workers = {
        "plugin-" + p["id"] for p in document["plugins"] if plugin is None or p["id"] == plugin
    }
    if not workers:
        raise ValueError("no matching plugin in deployment manifest")
    selected = workers | {s for name in workers for s in entries[name].get("mcpServices", [])}
    old_aliases = deployment.active_services()
    aliases = dict(old_aliases)
    nonce = secrets.token_hex(6)
    aliases.update(
        {
            name: "release-"
            + nonce
            + "-"
            + name[:24]
            + "-"
            + hashlib.sha256(name.encode()).hexdigest()[:6]
            for name in selected
        }
    )
    path, catalog = deployment.render(
        document, aliases=aliases, output=f"compose.stage-{nonce}.json"
    )
    # Preserve every unrelated installed endpoint even if this update manifest only
    # describes the selected release. Removal is a separate explicit full deployment.
    live = json.loads((deployment.state_dir() / "catalog/plugins.json").read_text())
    previous_refs = {f"tcp://{old_aliases.get(name, name)}:8840" for name in workers}
    staged_refs = {f"tcp://{aliases[name]}:8840" for name in workers}
    catalog["plugins"] = [p for p in live["plugins"] if p["reference"] not in previous_refs] + [
        p for p in catalog["plugins"] if p["reference"] in staged_refs
    ]
    catalog["solverUrls"] = {
        **live["solverUrls"],
        **{name: catalog["solverUrls"][name] for name in selected - workers},
    }
    images = deployment.active_images()
    rendered = json.loads(path.read_text())
    for name in selected:
        entry = entries[name]
        if entry.get("build"):
            repository = entry["image"].split("@")[0]
            if ":" in repository.rsplit("/", 1)[-1]:
                repository = repository.rsplit(":", 1)[0]
            images[name] = repository + ":release-" + nonce
        else:
            images[name] = entry["image"]
        rendered["services"][aliases[name]]["image"] = images[name]
    path.write_text(json.dumps(rendered, indent=2) + "\n")
    project = document["project"]
    compose = [docker, "compose", "--project-name", project, "-f", str(path)]
    staged = [aliases[name] for name in sorted(selected)]
    platforms = {}
    for name in ("room", "referee"):
        ids = subprocess.check_output(
            [
                docker,
                "ps",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--filter",
                f"label=com.docker.compose.service={name}",
            ],
            text=True,
        ).split()
        if len(ids) != 1:
            raise ValueError(f"exactly one running {name} is required for staged updates")
        platforms[name] = ids[0]
    # Docker keeps its initial NetworkMode name even after network disconnect.
    # Removing that empty anchor makes future network attachments fail, so retain
    # only networks still referenced by a running platform container's HostConfig.
    network_anchors = {
        subprocess.check_output(
            [docker, "inspect", "--format", "{{.HostConfig.NetworkMode}}", container],
            text=True,
        ).strip()
        for container in platforms.values()
    }
    connected = []
    committed = False

    def run(*args, **kwargs):
        return subprocess.run([docker, *args], check=True, **kwargs)

    def validate(candidate):
        for container in platforms.values():
            try:
                run(
                    "exec",
                    "-i",
                    container,
                    "python",
                    "-c",
                    VALIDATE,
                    input=json.dumps(candidate),
                    text=True,
                    timeout=180,
                    capture_output=True,
                )
            except subprocess.CalledProcessError as exc:
                print(
                    "Candidate validation failed:\n" + validator_diagnostic(exc.stderr, candidate),
                    file=sys.stderr,
                )
                raise

    def activate():
        run("exec", platforms["room"], "python", "-c", ACTIVATE, timeout=120, capture_output=True)

    try:
        for name in sorted(selected):
            deployment.bundle(name, entries[name])
        builds = [aliases[name] for name in sorted(selected) if entries[name].get("build")]
        if builds:
            subprocess.run([*compose, "build", *builds], check=True)
        subprocess.run(
            [
                *compose,
                "up",
                "--no-build",
                "--detach",
                "--wait",
                "--wait-timeout",
                "150",
                "--no-deps",
                *staged,
            ],
            check=True,
        )
        health(compose, staged)
        for service in staged:
            network = project + "_capability-" + service
            for container in platforms.values():
                run("network", "connect", network, container)
                connected.append((network, container))
        switch_catalog(catalog, aliases, validate, activate, images)
        committed = True
        # Remove only the superseded services, after successful live activation.
        for logical in sorted(selected):
            old = old_aliases.get(logical, logical)
            ids = subprocess.check_output(
                [
                    docker,
                    "ps",
                    "-aq",
                    "--filter",
                    f"label=com.docker.compose.project={project}",
                    "--filter",
                    f"label=com.docker.compose.service={old}",
                ],
                text=True,
            ).split()
            if ids:
                run("rm", "--force", *ids)
            old_network = project + "_capability-" + old
            for container in platforms.values():
                subprocess.run(
                    [docker, "network", "disconnect", old_network, container], capture_output=True
                )
            if old_network not in network_anchors:
                subprocess.run([docker, "network", "rm", old_network], capture_output=True)
        deployment.render(document)
        print("Staged plugin release committed; platform containers and images unchanged")
    finally:
        if not committed:
            for network, container in reversed(connected):
                subprocess.run(
                    [docker, "network", "disconnect", network, container], capture_output=True
                )
            subprocess.run([*compose, "rm", "--stop", "--force", *staged], capture_output=True)
            for service in staged:
                subprocess.run(
                    [docker, "network", "rm", project + "_capability-" + service],
                    capture_output=True,
                )
                (deployment.state_dir() / (service + ".env")).unlink(missing_ok=True)
            for name in selected:
                if entries[name].get("build"):
                    subprocess.run([docker, "image", "rm", images[name]], capture_output=True)
        path.unlink(missing_ok=True)
