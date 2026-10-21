"""Manage only this copy's isolated Docker project; never remove volumes automatically.

python tools/docker_stack.py up              # platform and domain-neutral starter worker/MCP
python tools/docker_stack.py up --core-only  # platform with an empty plugin catalog
python tools/docker_stack.py check           # health and authenticated API smoke
python tools/docker_stack.py stop            # stop, preserving all state
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from deployment import state_dir

ROOT = Path(__file__).resolve().parent.parent


def docker_binary() -> str:
    binary = shutil.which("docker")
    if binary:
        return binary
    desktop = Path("C:/Program Files/Docker/Docker/resources/bin/docker.exe")
    if desktop.exists():
        return str(desktop)
    raise SystemExit("Docker CLI is required. Install Docker Desktop and start its Linux engine.")


def prepare() -> None:
    private = state_dir()
    private.mkdir(mode=0o700, exist_ok=True)
    path = private / "stack.env"
    if not path.exists():
        with path.open("x", encoding="utf-8") as file:
            file.write(
                f"CONSILITH_TOKEN_SECRET={secrets.token_urlsafe(48)}\n"
                f"CONSILITH_REFEREE_TOKEN={secrets.token_urlsafe(32)}\n"
            )
        path.chmod(0o600)
    values = dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)
    if "CONSILITH_SIWC_SECRET" not in values:
        values["CONSILITH_SIWC_SECRET"] = secrets.token_urlsafe(48)
        with path.open("a", encoding="utf-8") as file:
            file.write(f"CONSILITH_SIWC_SECRET={values['CONSILITH_SIWC_SECRET']}\n")
    values["CONSILITH_SIWC_URL"] = os.environ.get("CONSILITH_SIWC_URL", "http://siwc:8730")
    shared = private / "siwc-shared-secret"
    shared.write_text(values["CONSILITH_SIWC_SECRET"], encoding="utf-8")
    shared.chmod(0o600)
    keyring = private / "siwc-keyring-password"
    if not keyring.exists():
        keyring.write_text(secrets.token_urlsafe(48), encoding="utf-8")
        keyring.chmod(0o600)
    for name, keys in {
        "room": [
            "CONSILITH_TOKEN_SECRET",
            "CONSILITH_REFEREE_TOKEN",
            "CONSILITH_SIWC_SECRET",
            "CONSILITH_SIWC_URL",
        ],
        "referee": ["CONSILITH_REFEREE_TOKEN"],
    }.items():
        scoped = private / f"{name}.env"
        scoped.write_text("".join(f"{key}={values[key]}\n" for key in keys), encoding="utf-8")
        scoped.chmod(0o600)


def smoke() -> None:
    base = f"http://127.0.0.1:{os.environ.get('CONSILITH_HTTP_PORT', '8800')}"

    def call(path: str, body: dict | None = None, token: str = ""):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    assert call("/api/health")["status"] == "ok"
    email = f"container-check-{secrets.token_hex(4)}@example.test"
    call("/api/auth/email", {"email": email})
    mailbox = call("/api/dev/mailbox")
    link = next(message["url"] for message in mailbox if message["to"] == email)
    ticket = parse_qs(urlsplit(link).query)["token"][0]
    session = call("/api/auth/email/redeem", {"token": ticket})
    assert call("/api/me", token=session["token"])
    print("Docker gateway, room health, email redemption, and authenticated account: passed")
    status = call("/api/me/agent/siwc", token=session["token"])
    # A connected plan belongs to its real owner, not this disposable smoke-test account.
    if not status.get("available") and status.get("reason") != "connected by another person":
        raise SystemExit("Docker room cannot reach the configured ChatGPT helper")
    print("Authenticated Docker route to ChatGPT helper: passed (account sign-in is separate)")


def health(compose: list[str], expected: list[str]) -> None:
    result = subprocess.run(
        [*compose, "ps", "--all", "--format", "json"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    raw = result.stdout.strip()
    rows = (
        json.loads(raw) if raw.startswith("[") else [json.loads(line) for line in raw.splitlines()]
    )
    states = {row["Service"]: row for row in rows}
    for name in expected:
        row = states.get(name, {})
        if row.get("State") != "running" or row.get("Health") != "healthy":
            raise SystemExit(f"{name}: not running and healthy")
    print(f"Docker service health: {len(expected)}/{len(expected)} passed")


def _main() -> int:
    from deployment import bundle, manifest, publish_catalog, release_entries, render

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["up", "build", "plugin-update", "check", "stop", "logs", "config"]
    )
    parser.add_argument("--core-only", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--plugin", help="Update exactly one plugin ID; omitted updates all manifest plugins"
    )
    args = parser.parse_args()
    document = manifest(args.manifest)
    prepare()
    if args.action == "plugin-update":
        from staged_release import update

        update(document, args.plugin, docker_binary(), health)
        return 0
    path, catalog = render(document, args.core_only)
    compose = [docker_binary(), "compose", "--project-name", document["project"], "-f", str(path)]
    entries = release_entries(document, args.core_only)
    rendered = json.loads(path.read_text())
    expected = list(rendered["services"])
    if args.action in {"up", "build", "plugin-update"}:
        selected = entries
        for name, entry in selected.items():
            bundle(name, entry)
        from deployment import active_services

        aliases = active_services()
        build_targets = [
            aliases.get(name, name) for name, entry in selected.items() if entry.get("build")
        ]
        known_images = {rendered["services"][name]["image"] for name in build_targets}
        for name, service in rendered["services"].items():
            if service.get("build") and service["image"] not in known_images:
                build_targets.append(name)
                known_images.add(service["image"])
        if build_targets:
            subprocess.run([*compose, "build", *build_targets], cwd=ROOT, check=True)
        if args.action == "build":
            return 0
        workers = [name for name in entries if name != "room"]
        workers = [aliases.get(name, name) for name in workers]
        if workers:
            subprocess.run(
                [*compose, "up", "--no-build", "--detach", "--wait", *workers],
                cwd=ROOT,
                check=True,
            )
        publish_catalog(catalog)
        subprocess.run([*compose, "up", "--no-build", "--detach", "--wait"], cwd=ROOT, check=True)
        health(compose, expected)
        smoke()
    elif args.action == "check":
        health(compose, expected)
        smoke()
    else:
        if args.action == "config":
            (state_dir() / "catalog").mkdir(exist_ok=True)
        command = ["config", "--quiet"] if args.action == "config" else [args.action]
        subprocess.run([*compose, *command], cwd=ROOT, check=True)
    return 0


def main() -> int:
    from deployment import deployment_guard

    with deployment_guard():
        return _main()


if __name__ == "__main__":
    raise SystemExit(main())
