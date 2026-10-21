"""Manifest-driven release bundles and discovery. No scientific family names in lifecycle code."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
PLATFORM_SERVICES = frozenset({"room", "referee", "web", "siwc"})


@contextlib.contextmanager
def deployment_guard():
    """Reject concurrent lifecycle mutations; the OS releases the lock after a crash."""
    directory = ROOT / ".docker"
    directory.mkdir(exist_ok=True)
    with (directory / "deployment.lock").open("a+b") as stream:
        stream.seek(0)
        stream.write(b"0")
        stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("another deployment or cleanup is active") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def manifest(path: Path | None = None) -> dict:
    document = json.loads((path or ROOT / "deployment.json").read_text())
    validate_manifest(document)
    return document


def validate_manifest(document: dict) -> None:
    if document.get("schemaVersion") != 1:
        raise ValueError("unsupported deployment manifest")
    names = set(PLATFORM_SERVICES)
    for item in [*document["plugins"], *document["mcpServices"]]:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,48}", item["id"]) or item["id"] in names:
            raise ValueError("deployment service IDs must be unique safe names")
        names.add(item["id"])
    generated = {"plugin-" + item["id"] for item in document["plugins"]}
    if generated & names or any(
        item["id"].startswith("release-") for item in document["plugins"] + document["mcpServices"]
    ):
        raise ValueError("deployment service IDs collide with generated or reserved names")
    servers = {item["id"] for item in document["mcpServices"]}
    claimed = set()
    for plugin in document["plugins"]:
        associated = set(plugin.get("mcpServices", []))
        if not associated <= servers or associated & claimed:
            raise ValueError("MCP services must exist and have one owning plugin")
        claimed.update(associated)
        if not plugin.get("entryPoint"):
            raise ValueError("plugin requires an entryPoint")
    for server in document["mcpServices"]:
        url = urlsplit(server.get("url", ""))
        if url.scheme not in {"http", "https"} or url.hostname != server["id"] or url.username:
            raise ValueError("MCP URL must address its declared Docker service")
        if not server.get("command"):
            raise ValueError("MCP service requires a command")
        # Source bundles inherit the checked runtime image health probe. External images
        # must supply their own explicit deployment probe, never implicit readiness.
        health = server.get("healthcheck")
        probe = health.get("test", []) if health else []
        if not server.get("build") and not (
            isinstance(probe, list)
            and len(probe) > 1
            and probe[0] in {"CMD", "CMD-SHELL"}
            and not health.get("disable")
        ):
            raise ValueError("external MCP image requires an explicit healthcheck")


def active_services() -> dict[str, str]:
    path = ROOT / ".docker/active-services.json"
    return json.loads(path.read_text()) if path.exists() else {}


def active_images() -> dict[str, str]:
    path = ROOT / ".docker/active-images.json"
    return json.loads(path.read_text()) if path.exists() else {}


def release_entries(document: dict, core_only: bool = False) -> dict:
    entries = {"room": document["platform"]}
    if not core_only:
        entries.update({"plugin-" + item["id"]: item for item in document["plugins"]})
        entries.update({item["id"]: item for item in document["mcpServices"]})
    return entries


def bundle(service: str, entry: dict) -> None:
    """Build independent wheels then compile a hash lock, preserving existing transitive pins."""
    config = entry.get("build")
    if not config:
        return
    directory = ROOT / ".docker" / "build" / service
    wheels = directory / "wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    local_names = []
    for old in wheels.glob("*.whl"):
        old.unlink()
    for package in config["packages"]:
        path = Path(package)
        source = path if path.is_absolute() else ROOT / path
        local_names.append(
            tomllib.loads((source / "pyproject.toml").read_text())["project"]["name"]
        )
        fingerprint = hashlib.sha256()
        excluded = {".git", ".venv", "__pycache__", "dist", ".pytest_cache", ".ruff_cache"}
        for file in sorted(source.rglob("*")):
            if set(file.relative_to(source).parts) & excluded or not file.is_file():
                continue
            if file.is_symlink():
                raise ValueError("wheel source cannot contain symbolic links")
            fingerprint.update(file.relative_to(source).as_posix().encode() + b"\0")
            fingerprint.update(file.read_bytes())
        cached = ROOT / ".docker/wheel-cache" / fingerprint.hexdigest()
        cached.mkdir(parents=True, exist_ok=True)
        if not list(cached.glob("*.whl")):
            subprocess.run(
                ["uv", "build", "--wheel", "--out-dir", str(cached), str(source)],
                cwd=ROOT,
                check=True,
            )
        for wheel in cached.glob("*.whl"):
            shutil.copyfile(wheel, wheels / wheel.name)
    requirements = directory / "requirements.in"
    requirements.write_text("\n".join(config["requirements"]) + "\n")
    lock = directory / "requirements.lock"
    subprocess.run(
        [
            "uv",
            "pip",
            "compile",
            str(requirements),
            "--find-links",
            str(wheels),
            "--generate-hashes",
            "--no-emit-find-links",
            "--python-version",
            "3.12",
            # Resolve architecture markers for both Intel and ARM Docker engines.
            "--universal",
            "--output-file",
            str(lock),
            *[
                flag
                for name in local_names
                for flag in ("--upgrade-package", name, "--refresh-package", name)
            ],
        ],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    shutil.copyfile(ROOT / "docker/runtime.Dockerfile", directory / "Dockerfile")
    shutil.copyfile(ROOT / "tools/container.py", directory / "container.py")
    receipt = {
        "image": entry["image"],
        "requirementsSha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
        "wheels": {
            wheel.name: hashlib.sha256(wheel.read_bytes()).hexdigest()
            for wheel in sorted(wheels.glob("*.whl"))
        },
    }
    (directory / "release.json").write_text(json.dumps(receipt, indent=2) + "\n")


def render(
    document: dict,
    core_only: bool = False,
    aliases: dict | None = None,
    output: str = "compose.generated.json",
) -> tuple[Path, dict]:
    validate_manifest(document)
    aliases = active_services() if aliases is None else aliases
    images = active_images()
    base = json.loads((ROOT / "docker/platform.compose.json").read_text())
    base["name"] = document["project"]
    services = base["services"]
    private = ROOT / ".docker"
    private.mkdir(exist_ok=True)
    secret_file = private / "plugin-tokens.json"
    tokens = json.loads(secret_file.read_text()) if secret_file.exists() else {}
    catalog = {"plugins": [], "solverUrls": {}}
    common = {
        "restart": "unless-stopped",
        "init": True,
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "tmpfs": ["/tmp:rw,noexec,nosuid,size=256m"],
        "mem_limit": "1g",
        "cpus": 2,
    }
    plugin_services = {"plugin-" + item["id"] for item in document["plugins"]}
    for logical, entry in release_entries(document, core_only).items():
        service = aliases.get(logical, logical)
        if logical == "room":
            for target in ("room", "referee"):
                services[target]["image"] = entry["image"]
                if entry.get("build"):
                    services[target]["build"] = {"context": str(private / "build" / logical)}
                else:
                    services[target].pop("build", None)
            continue
        spec = {**common, "image": images.get(logical, entry["image"])}
        if entry.get("build"):
            spec["build"] = {
                "context": str(private / "build" / logical),
                "args": {"SYSTEM_PACKAGES": " ".join(entry["build"].get("systemPackages", []))},
            }
        if logical in plugin_services:
            token = tokens.setdefault(service, secrets.token_urlsafe(48))
            env_file = private / (service + ".env")
            env_file.write_text(f"CONSILITH_PLUGIN_TOKEN={token}\n")
            env_file.chmod(0o600)
            spec.update(
                {
                    "command": [
                        "python",
                        "-m",
                        "consilith_plugin_runtime.server",
                        "--entry-point",
                        entry["entryPoint"],
                    ],
                    "env_file": [str(env_file)],
                    "healthcheck": {
                        "test": ["CMD", "python", "-m", "consilith_plugin_runtime.health"],
                        "interval": "10s",
                        "timeout": "5s",
                        "start_period": "15s",
                        "retries": 12,
                    },
                }
            )
            catalog["plugins"].append({"reference": f"tcp://{service}:8840", "token": token})
        else:
            spec.update(
                {
                    "command": entry["command"],
                    "environment": {
                        key: value.replace(logical + ":", service + ":")
                        if isinstance(value, str)
                        else value
                        for key, value in entry.get("environment", {}).items()
                    },
                    "mem_limit": entry.get("memory", "3g"),
                }
            )
            if entry.get("healthcheck"):
                spec["healthcheck"] = entry["healthcheck"]
            catalog["solverUrls"][entry["id"]] = (
                entry["url"]
                .replace("//" + logical + ":", "//" + service + ":")
                .replace("//" + logical + "/", "//" + service + "/")
            )
        services[service] = spec
        network = "capability-" + service
        base.setdefault("networks", {})[network] = {"internal": True}
        spec["networks"] = [network]
        for platform in ("room", "referee"):
            services[platform].setdefault("networks", ["default"]).append(network)
    secret_file.write_text(json.dumps(tokens))
    secret_file.chmod(0o600)
    for secret in base.get("secrets", {}).values():
        if "file" in secret and not Path(secret["file"]).is_absolute():
            secret["file"] = str(ROOT / secret["file"])
    for service_name, spec in services.items():
        spec["logging"] = {
            "driver": "json-file",
            "options": {"max-size": "10m", "max-file": "3"},
        }
        for key in ("env_file",):
            if key in spec:
                spec[key] = [
                    str(ROOT / path) if not Path(path).is_absolute() else path for path in spec[key]
                ]
        if "build" in spec and spec["build"].get("context") == ".":
            spec["build"]["context"] = str(ROOT)
        if "build" in spec:
            spec["build"].setdefault("labels", {}).update(
                {
                    "com.consilith.project": document["project"],
                    "com.consilith.role": "chatgpt-bridge" if service_name == "siwc" else "runtime",
                }
            )
        if "volumes" in spec:
            spec["volumes"] = [
                str(private / "catalog") + ":/catalog:ro" if volume == "CATALOG" else volume
                for volume in spec["volumes"]
            ]
    path = private / output
    path.write_text(json.dumps(base, indent=2) + "\n")
    return path, catalog


def publish_catalog(catalog: dict) -> None:
    directory = ROOT / ".docker" / "catalog"
    directory.mkdir(exist_ok=True)
    temporary = directory / "plugins.next.json"
    published = {**catalog, "generation": secrets.token_hex(16)}
    temporary.write_text(json.dumps(published, indent=2) + "\n")
    temporary.replace(directory / "plugins.json")
