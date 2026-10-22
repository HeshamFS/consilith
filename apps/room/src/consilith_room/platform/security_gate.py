"""The security gate, enforced in code (BUILD_SPEC section 10).

``CONSILITH_AGENT_LOOP`` (stage 1) and ``CONSILITH_PEERS`` (stage 2) may be turned on only when
every fix the stage requires is recorded as passed in ``security_gate.json`` and the checks that
can run inside this process pass too. ``tools/security_gate.py`` writes the file from a pytest
JUnit report at build time. The gate guards against forgetting, not against an operator who
controls the build.

This module is the one list of required fixes. It reads the file, runs the self-tests and
answers ``stage_passed``. The feature flags call it later; nothing here reads a flag.
"""

from __future__ import annotations

import importlib
import inspect
import json
import os
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .settings import Settings

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Fix:
    id: str
    stage: int  # the first stage that needs it; stage 2 needs every stage 1 fix as well
    owner: str
    title: str


FIXES: tuple[Fix, ...] = (
    Fix("gateway_open_proxy", 1, "WS-X", "The gateway cannot be used as an open proxy"),
    Fix(
        "operator_catalog_binding",
        1,
        "WS-X",
        "MCP servers, tools and policy ports come from the operator catalog",
    ),
    Fix("replay_denial", 1, "WS-X", "A recorded session replays only for its own person"),
    Fix("artifact_size_stream", 1, "WS-X", "An upload is limited while it streams in"),
    Fix("journal_viewer", 1, "WS-X", "Personal chat text is journaled to its owner only"),
    Fix("catalog_file_mode", 1, "WS-X", "The catalog that holds tokens is written with mode 600"),
    Fix("hash_cache_private", 1, "WS-X", "The worker hash cache lives in a private directory"),
    Fix("capabilities_trim", 1, "WS-X", "/capabilities shows an anonymous caller only the catalog"),
    Fix("topology_check", 1, "WS-X", "Plugin networks are internal, production has no dev plugins"),
    Fix("keys_fix", 1, "WS-M", "The key store fix"),
    Fix("require_pins_every_step", 1, "WS-L", "Package pins are checked on every agent step"),
    Fix("run_quotas", 1, "WS-L", "The run supervisor enforces minimal quotas"),
    Fix("agent_card_trim", 2, "WS-P", "Agent cards show detail only behind a token"),
    Fix("journal_payload_false", 2, "WS-P", "Peer traffic is journaled without its payload"),
    Fix("peer_budgets_optin", 2, "WS-P", "Peer budgets and recipient opt-in"),
    Fix("restricted_a2a_profile", 2, "WS-P", "A peer-origin turn is restricted by construction"),
    Fix("peer_messages_retention", 2, "WS-P", "Peer message bodies are retained 30 days"),
)
STAGES = (1, 2)


def required(stage: int) -> tuple[str, ...]:
    """The fix ids a stage needs. Stage 2 needs everything stage 1 does."""
    if stage not in STAGES:
        raise ValueError(f"unknown security gate stage {stage!r}")
    return tuple(fix.id for fix in FIXES if fix.stage <= stage)


def replay_allowed(caller: str, viewer: str) -> bool:
    """A recorded session, feed or agent, is private to the person it was recorded for."""
    return bool(caller) and viewer == caller


@dataclass(frozen=True)
class SelfTest:
    name: str
    ok: bool
    detail: str = ""


def _replay_denial() -> SelfTest:
    allowed = replay_allowed("alice", "alice") and not replay_allowed("alice", "bob")
    return SelfTest("replay_denial", allowed, "" if allowed else "another viewer can be replayed")


def _manifest_source() -> SelfTest:
    """A worker manifest that asks for more than the operator catalog grants must be refused."""
    client: Any = importlib.import_module("consilith_plugin_runtime.client")
    grant: dict[str, Any] = {
        "mcpServers": [{"id": "probe-server", "tools": ["probe.run"]}],
        "tools": ["probe.run"],
        "policyPorts": ["snapshot"],
    }
    base: dict[str, Any] = {
        "id": "gate-probe",
        "version": "1",
        "title": "Gate probe",
        "mcpServers": [{"id": "probe-server", "tools": ["probe.run"]}],
        "tools": ["probe.run"],
        "policyPorts": ["snapshot"],
    }
    wider: tuple[dict[str, Any], ...] = (
        {"policyPorts": ["snapshot", "propose"]},
        {"tools": ["probe.run", "probe.other"]},
        {"mcpServers": [{"id": "other-server", "tools": []}]},
        {"mcpServers": [{"id": "probe-server", "tools": ["probe.other"]}]},
    )
    try:
        client.check_manifest(base, grant)
        for change in wider:
            try:
                client.check_manifest({**base, **change}, grant)
            except client.CatalogMismatch:
                continue
            return SelfTest("operator_catalog_manifest_source", False, f"accepted {sorted(change)}")
    except Exception as exc:
        return SelfTest("operator_catalog_manifest_source", False, type(exc).__name__)
    if "binding" not in inspect.signature(client.load).parameters:
        return SelfTest("operator_catalog_manifest_source", False, "load ignores the catalog")
    return SelfTest("operator_catalog_manifest_source", True)


def _hash_cache_mode() -> SelfTest:
    client: Any = importlib.import_module("consilith_plugin_runtime.client")
    worker: Any = importlib.import_module("consilith_plugin_runtime.worker")
    name = "hash_cache_directory_mode"
    directory = client.plugin_hash_dir()
    if directory is None or not worker.private_directory(Path(directory)):
        return SelfTest(name, False, "the worker hash directory is not private")
    if os.name != "nt":
        shared = Path(tempfile.mkdtemp(prefix="consilith-gate-"))
        try:
            shared.chmod(0o755)
            if worker.private_directory(shared):
                return SelfTest(name, False, "a world-readable directory passes as private")
        finally:
            shutil.rmtree(shared, ignore_errors=True)
    return SelfTest(name, True)


def topology_problems(extension_modules: Sequence[str], plugin_catalog: Path | None) -> list[str]:
    """What a production room must not run: plugin code in its own process tree, or plugins
    that the operator catalog does not fully bind. Network isolation is checked at render time
    by ``tools/deployment.py``; from inside a container only these facts are visible."""
    problems: list[str] = []
    if extension_modules:
        problems.append("dev-subprocess plugins are configured (CONSILITH_EXTENSION_MODULES)")
    if plugin_catalog is None:
        return problems
    try:
        document = cast(dict[str, Any], json.loads(plugin_catalog.read_bytes()))
        plugins = cast(list[dict[str, Any]], document.get("plugins", []))
    except (OSError, ValueError, AttributeError):
        return [*problems, "the operator plugin catalog cannot be read"]
    for entry in plugins:
        reference = str(entry.get("reference", ""))
        if not reference.startswith("tcp://"):
            problems.append("a catalog plugin is not a network service")
        binding = entry.get("binding")
        if not isinstance(binding, dict):
            problems.append("a catalog plugin has no binding")
            continue
        grant = cast(dict[str, Any], binding)
        for surface in ("mcpServers", "tools", "policyPorts"):
            if not isinstance(grant.get(surface), list):
                problems.append(f"a catalog plugin does not bind {surface}")
        servers = grant.get("mcpServers")
        if isinstance(servers, list) and any(
            not isinstance(server, dict) or "tools" not in server
            for server in cast(list[Any], servers)
        ):
            problems.append("a catalog plugin does not bind the tools of an MCP server")
    return problems


def _topology(extension_modules: Sequence[str], plugin_catalog: Path | None) -> SelfTest:
    problems = topology_problems(extension_modules, plugin_catalog)
    return SelfTest("production_topology", not problems, "; ".join(problems))


def run_self_tests(
    *,
    env: str = "development",
    extension_modules: Sequence[str] = (),
    plugin_catalog: Path | None = None,
) -> tuple[SelfTest, ...]:
    """The checks that can run in this process. The topology check runs in production only."""
    results = [_replay_denial(), _manifest_source(), _hash_cache_mode()]
    if env == "production":
        results.append(_topology(extension_modules, plugin_catalog))
    return tuple(results)


def read_gate_file(path: str | Path) -> tuple[dict[str, bool], list[str]]:
    """Which fix ids the file records as passed. Anything unreadable or malformed is no pass."""
    try:
        document = cast(dict[str, Any], json.loads(Path(path).read_bytes()))
    except (OSError, ValueError) as exc:
        return {}, [f"security gate file unreadable ({type(exc).__name__})"]
    if document.get("schemaVersion") != SCHEMA_VERSION:
        return {}, ["security gate file has an unsupported schema version"]
    fixes = document.get("fixes")
    if not isinstance(fixes, dict):
        return {}, ["security gate file lists no fixes"]
    passed: dict[str, bool] = {}
    for fix_id, raw in cast(dict[str, Any], fixes).items():
        passed[fix_id] = False
        if not isinstance(raw, dict):
            continue
        entry = cast(dict[str, Any], raw)
        tests = entry.get("tests")
        passed[fix_id] = (
            entry.get("passed") is True
            and isinstance(tests, list)
            and len(cast(list[Any], tests)) > 0
            and all(isinstance(test, str) and test for test in cast(list[Any], tests))
        )
    return passed, []


def stage_passed(stage: int, path: str | Path | None = None, **options: Any) -> bool:
    """Whether the gate for ``stage`` is green: read the file, run the self-tests, answer."""
    return SecurityGate.load(path, **options).stage_passed(stage)


class GateClosed(RuntimeError):
    """A feature flag was asked for before its security gate is green."""


@dataclass(frozen=True)
class SecurityGate:
    passed: dict[str, bool]
    problems: tuple[str, ...]
    self_tests: tuple[SelfTest, ...]

    @classmethod
    def load(
        cls,
        path: str | Path | None,
        *,
        env: str | None = None,
        extension_modules: Sequence[str] = (),
        plugin_catalog: Path | None = None,
        self_tests: bool = True,
    ) -> SecurityGate:
        """Read the gate file (``None`` or a missing file means nothing has passed) and run the
        in-process self-tests. ``env`` defaults to ``CONSILITH_ENV``; ``production`` adds the
        topology check."""
        env = os.environ.get("CONSILITH_ENV", "development") if env is None else env
        if path is None:
            passed, problems = {}, ["no security gate file is configured"]
        else:
            passed, problems = read_gate_file(path)
        tests = (
            run_self_tests(
                env=env, extension_modules=extension_modules, plugin_catalog=plugin_catalog
            )
            if self_tests
            else ()
        )
        return cls(passed, tuple(problems), tests)

    @classmethod
    def from_settings(cls, settings: Settings, path: str | Path | None = None) -> SecurityGate:
        """The path comes from the argument, else from ``settings.security_gate_path`` (or the
        older ``security_gate``) when the settings carry one."""
        named = getattr(settings, "security_gate_path", None) or getattr(
            settings, "security_gate", None
        )
        configured = path or cast("str | Path | None", named)
        return cls.load(
            configured,
            env=settings.env,
            extension_modules=settings.extension_modules,
            plugin_catalog=settings.plugin_catalog,
        )

    def missing(self, stage: int) -> list[str]:
        """Required fix ids that the file does not record as passed."""
        return [fix_id for fix_id in required(stage) if not self.passed.get(fix_id, False)]

    def failed_self_tests(self) -> list[SelfTest]:
        return [test for test in self.self_tests if not test.ok]

    def stage_passed(self, stage: int) -> bool:
        return not self.missing(stage) and not self.failed_self_tests()

    def require_stage(self, stage: int, flag: str) -> None:
        """Raise ``GateClosed`` naming what is still open. Reasons never contain secrets."""
        if self.stage_passed(stage):
            return
        reasons = [*self.problems]
        if self.missing(stage):
            reasons.append("not passed: " + ", ".join(self.missing(stage)))
        reasons += [f"self-test {t.name} failed: {t.detail}" for t in self.failed_self_tests()]
        raise GateClosed(f"{flag} needs security gate stage {stage}: " + "; ".join(reasons))
