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
import tempfile
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
PLATFORM_SERVICES = frozenset({"room", "referee", "web", "siwc"})
HOST_NAME = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
EGRESS_KEY = "x-consilith-egress"


CATALOG_MODE = 0o600


def write_private(path: Path, data: str | bytes, mode: int = CATALOG_MODE) -> None:
    """Replace ``path`` atomically with a file only its owner can read, from its first byte.

    The bytes go to a fresh file beside it (``mkstemp`` creates it owner-only on POSIX), the mode
    is set again explicitly, and ``replace`` swaps it in, so no reader sees a half-written file
    and no moment exists with a wider mode. Windows has no POSIX modes; its ACLs apply.
    """
    payload = data.encode() if isinstance(data, str) else data
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".next")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            temporary.chmod(mode)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def state_dir() -> Path:
    """Keep an existing deployment's credentials and catalog separate from source builds."""
    value = os.environ.get("CONSILITH_DEPLOYMENT_STATE_DIR")
    if value:
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("CONSILITH_DEPLOYMENT_STATE_DIR must be an absolute path")
        return path
    return ROOT / ".docker"


def runtime_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == ".docker":
        return state_dir().joinpath(*path.parts[1:])
    return ROOT / path


@contextlib.contextmanager
def deployment_guard():
    """Reject concurrent lifecycle mutations; the OS releases the lock after a crash."""
    directory = state_dir()
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


def name_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item for item in value)


def plugin_binding(plugin: dict, document: dict) -> dict:
    """What the operator grants one plugin, published in the catalog the runtime loads.

    The room checks the manifest its worker returns against this and refuses a worker that
    claims more: other MCP servers or tools, or host ports the operator did not list. A
    surface the deployment manifest does not declare is left out; production refuses that.
    """
    servers = {item["id"]: item for item in document["mcpServices"]}
    granted = []
    for identifier in plugin.get("mcpServices", []):
        entry: dict = {"id": identifier}
        if "tools" in servers[identifier]:
            entry["tools"] = list(servers[identifier]["tools"])
        granted.append(entry)
    binding: dict = {"mcpServers": granted}
    for field in ("tools", "policyPorts"):
        if field in plugin:
            binding[field] = list(plugin[field])
    return binding


MAX_SERVICE_CPUS = 8


def service_cpus(entry: dict) -> float:
    """CPUs for one MCP service: 2 unless its entry asks for more (a compute-heavy solver),
    never more than ``MAX_SERVICE_CPUS``."""
    value = entry.get("cpus", 2)
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not 0.5 <= value <= MAX_SERVICE_CPUS
    ):
        raise ValueError(f"{entry.get('id')}: cpus must be a number from 0.5 to {MAX_SERVICE_CPUS}")
    return value


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
        for field in ("tools", "policyPorts"):
            if field in plugin and not name_list(plugin[field]):
                raise ValueError(f"plugin {field} must be a list of names")
    for server in document["mcpServices"]:
        if "tools" in server and not name_list(server["tools"]):
            raise ValueError("MCP service tools must be a list of names")
        if "egress" in server:
            egress = server["egress"]
            hosts = egress.get("hosts") if isinstance(egress, dict) else None
            if (
                not isinstance(hosts, list)
                or not hosts
                or not all(isinstance(h, str) and HOST_NAME.match(h) for h in hosts)
            ):
                raise ValueError("egress needs a non-empty list of host names")
            if not isinstance(egress.get("reason"), str) or not egress["reason"].strip():
                raise ValueError("egress needs a reason")
        if "hostEnv" in server and not (
            isinstance(server["hostEnv"], list)
            and all(isinstance(n, str) and ENV_NAME.match(n) for n in server["hostEnv"])
        ):
            raise ValueError("hostEnv must be a list of environment variable names")
    for plugin in document["plugins"]:
        if "egress" in plugin or "hostEnv" in plugin:
            raise ValueError("a plugin worker can never have egress or host environment")
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
    path = state_dir() / "active-services.json"
    return json.loads(path.read_text()) if path.exists() else {}


def active_images() -> dict[str, str]:
    path = state_dir() / "active-images.json"
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
    directory = state_dir() / "build" / service
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
        cached = state_dir() / "wheel-cache" / fingerprint.hexdigest()
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
    production: bool | None = None,
) -> tuple[Path, dict]:
    validate_manifest(document)
    aliases = active_services() if aliases is None else aliases
    images = active_images()
    base = json.loads((ROOT / "docker/platform.compose.json").read_text())
    base["name"] = document["project"]
    services = base["services"]
    private = state_dir()
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
            write_private(env_file, f"CONSILITH_PLUGIN_TOKEN={token}\n")
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
            catalog["plugins"].append(
                {
                    "reference": f"tcp://{service}:8840",
                    "token": token,
                    "binding": plugin_binding(entry, document),
                }
            )
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
                    "cpus": service_cpus(entry),
                }
            )
            if entry.get("healthcheck"):
                spec["healthcheck"] = entry["healthcheck"]
            if entry.get("hostEnv"):
                # Host variables reach only this service, through an owner-only file the
                # lifecycle writes at start. Unset ones are left out: the service starts and
                # refuses its own calls.
                lines = []
                for name in entry["hostEnv"]:
                    value = os.environ.get(name, "")
                    if "\n" in value or "\r" in value:
                        raise ValueError(f"host variable {name} holds a line break")
                    if value:
                        lines.append(f"{name}={value}\n")
                env_file = private / (service + ".env")
                write_private(env_file, "".join(lines))
                spec["env_file"] = [str(env_file)]
            catalog["solverUrls"][entry["id"]] = (
                entry["url"]
                .replace("//" + logical + ":", "//" + service + ":")
                .replace("//" + logical + "/", "//" + service + "/")
            )
        services[service] = spec
        network = "capability-" + service
        base.setdefault("networks", {})[network] = {"internal": True}
        spec["networks"] = [network]
        if logical not in plugin_services and entry.get("egress"):
            # The one exception to "no route out": a declared MCP service gets a second,
            # non-internal network of its own. Hosts are enforced by the service's own code.
            route = "egress-" + service
            base["networks"][route] = {"driver": "bridge"}
            spec["networks"].append(route)
            spec.setdefault("labels", {})["com.consilith.egress"] = ",".join(
                entry["egress"]["hosts"]
            )
            base.setdefault(EGRESS_KEY, {})[service] = {
                "hosts": list(entry["egress"]["hosts"]),
                "reason": entry["egress"]["reason"],
            }
        for platform in ("room", "referee"):
            services[platform].setdefault("networks", ["default"]).append(network)
    write_private(secret_file, json.dumps(tokens))
    for secret in base.get("secrets", {}).values():
        if "file" in secret and not Path(secret["file"]).is_absolute():
            secret["file"] = str(runtime_path(secret["file"]))
    for service_name, spec in services.items():
        spec["logging"] = {
            "driver": "json-file",
            "options": {"max-size": "10m", "max-file": "3"},
        }
        for key in ("env_file",):
            if key in spec:
                spec[key] = [str(runtime_path(path)) for path in spec[key]]
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
    if production is None:
        production = os.environ.get("CONSILITH_ENV") == "production"
    check_topology(base, catalog, production=production)
    path = private / output
    path.write_text(json.dumps(base, indent=2) + "\n")
    return path, catalog


def publish_catalog(catalog: dict) -> None:
    """Publish the catalog the room and referee read. It holds the plugin tokens: mode 600,
    swapped in atomically."""
    directory = state_dir() / "catalog"
    directory.mkdir(parents=True, exist_ok=True)
    published = {**catalog, "generation": secrets.token_hex(16)}
    write_private(directory / "plugins.json", json.dumps(published, indent=2) + "\n")


def is_plugin_worker(name: str, spec: dict) -> bool:
    """Conservative: anything named or run like a plugin worker, staged aliases included."""
    command = " ".join(str(part) for part in spec.get("command") or [])
    return name.startswith("plugin-") or "-plugin-" in name or "consilith_plugin_runtime" in command


def _names(value: object) -> list[str]:
    if isinstance(value, dict):
        return [str(key) for key in value]
    return [str(item) for item in value] if isinstance(value, list) else []


def topology_problems(
    rendered: dict, catalog: dict | None = None, *, production: bool = False
) -> list[str]:
    """Every way a rendered deployment lets plugin code reach beyond its capability network.

    A plugin worker or MCP service sits alone on its own ``capability-<service>`` network, which
    must be ``internal`` (Docker gives it no route out) and shared only with the room and the
    referee. The web gateway and the ChatGPT bridge never join one. Production also refuses
    plugins that run as subprocesses of the room, and catalog plugins without a full binding.
    """
    problems: list[str] = []
    services: dict = rendered.get("services", {})
    networks: dict = rendered.get("networks", {})
    declared = rendered.get(EGRESS_KEY, {})
    declared = declared if isinstance(declared, dict) else {}
    for name, spec in services.items():
        attached = _names(spec.get("networks", ["default"]))
        capability = [n for n in attached if n.startswith("capability-")]
        routes = [n for n in attached if n.startswith("egress-")]
        if name in PLATFORM_SERVICES:
            if name in {"web", "siwc"} and (capability or routes):
                problems.append(f"{name} must not join a plugin capability or egress network")
            continue
        own = [n for n in attached if not n.startswith("egress-")]
        if own != ["capability-" + name]:
            problems.append(f"{name} must sit on its own capability network only, not {attached}")
        if routes:
            grant = declared.get(name)
            if routes != ["egress-" + name]:
                problems.append(f"{name} may join only its own egress network, not {routes}")
            elif not isinstance(grant, dict) or not grant.get("hosts"):
                problems.append(f"{name} joins an egress network without a declared host list")
            elif is_plugin_worker(name, spec):
                problems.append(f"{name} is a plugin worker and can never have egress")
        if spec.get("network_mode"):
            problems.append(f"{name} must not set network_mode")
        if spec.get("ports"):
            problems.append(f"{name} must not publish ports")
    for name, definition in networks.items():
        if not name.startswith("capability-"):
            continue
        if not isinstance(definition, dict) or definition.get("internal") is not True:
            problems.append(f"network {name} must be internal, with no egress")
        elif definition.get("external") or definition.get("driver") in {"host", "macvlan"}:
            problems.append(f"network {name} must be a private bridge")
    for name, definition in networks.items():
        if not name.startswith("egress-"):
            continue
        members = [s for s, spec in services.items() if name in _names(spec.get("networks", []))]
        if not isinstance(definition, dict) or definition.get("internal") is True:
            problems.append(f"network {name} is an egress network and must not be internal")
        if isinstance(definition, dict) and (
            definition.get("external") or definition.get("driver") in {"host", "macvlan"}
        ):
            problems.append(f"network {name} must be a private bridge")
        if members != [name.removeprefix("egress-")]:
            problems.append(f"network {name} must serve only the service it is named for")
    used = {n for spec in services.values() for n in _names(spec.get("networks", []))}
    for name in sorted(n for n in used if n.startswith("capability-") and n not in networks):
        problems.append(f"network {name} is used but not defined as internal")
    for name in sorted(n for n in used if n.startswith("egress-") and n not in networks):
        problems.append(f"network {name} is used but not defined")
    if production:
        for name in ("room", "referee"):
            env = services.get(name, {}).get("environment", {})
            try:
                modules = json.loads(env.get("CONSILITH_EXTENSION_MODULES", "[]") or "[]")
            except ValueError:
                modules = ["unreadable"]
            if modules:
                problems.append(f"{name} loads dev-subprocess plugins, which production refuses")
        for plugin in (catalog or {}).get("plugins", []):
            if not str(plugin.get("reference", "")).startswith("tcp://"):
                problems.append("a catalog plugin is not a network service")
            binding = plugin.get("binding")
            if not isinstance(binding, dict):
                problems.append("a catalog plugin has no binding")
                continue
            for field in ("mcpServers", "tools", "policyPorts"):
                if not isinstance(binding.get(field), list):
                    problems.append(f"a catalog plugin does not bind {field}")
            if any(
                not isinstance(server, dict) or "tools" not in server
                for server in binding.get("mcpServers", [])
            ):
                problems.append("a catalog plugin does not bind the tools of an MCP server")
    return problems


def check_topology(
    rendered: dict, catalog: dict | None = None, *, production: bool = False
) -> None:
    """Raise ``ValueError`` listing every topology problem; the security gate calls this."""
    problems = topology_problems(rendered, catalog, production=production)
    if problems:
        raise ValueError("deployment topology refused: " + "; ".join(problems))
