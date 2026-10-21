"""Helpers for solver servers: hash the inputs, time the call, build the envelope."""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import io
import json
import os
import re
import time
from collections.abc import Generator
from contextlib import contextmanager
from email.parser import Parser
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from packaging.requirements import Requirement

from .base import digest
from .results import Provenance, QuantityOut, ResultEnvelope, Tier, Validity


class NotInstalledError(RuntimeError):
    """The solver behind a tool is not installed here. Tools raise it instead of faking numbers."""


def inputs_hash(arguments: dict[str, Any]) -> str:
    return digest(arguments)[:16]


class Timer:
    def __init__(self) -> None:
        self.start = time.perf_counter()
        self.elapsed = 0.0


@contextmanager
def timed() -> Generator[Timer]:
    t = Timer()
    try:
        yield t
    finally:
        t.elapsed = time.perf_counter() - t.start


def envelope(
    *,
    tool: str,
    version: str,
    arguments: dict[str, Any],
    quantities: list[tuple[str, float, str]],
    wall_time_s: float,
    validity: Validity,
    tier: Tier,
    seed: int | None = None,
    size: dict[str, int | float | str] | None = None,
    model: str | None = None,
    illustrative: bool = False,
    fields: dict[str, Any] | None = None,
    notes: list[str] | None = None,
) -> ResultEnvelope:
    return ResultEnvelope(
        quantities=[QuantityOut(name=n, value=float(v), unit=u) for n, v, u in quantities],
        provenance=Provenance(
            tool=tool,
            solver_version=version,
            inputs_hash=inputs_hash(arguments),
            seed=seed,
            wall_time_s=round(wall_time_s, 6),
            size=size or {},
            model=model,
        ),
        validity=validity,
        tier=tier,
        illustrative=illustrative,
        fields=fields or {},
        notes=notes or [],
    )


@lru_cache(maxsize=8192)
def _content_hash(path: str, size: int, modified: int, changed: int) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _file_hash(path: Path) -> str:
    stat = path.lstat()
    if path.is_symlink():
        raise ValueError("solver release contains a symbolic link")
    return _content_hash(str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


@lru_cache(maxsize=256)
def _metadata_inventory(metadata: str, record: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Cache parsing only; callers still read metadata and inspect every packaged asset."""
    message = Parser().parsestr(metadata)
    return (
        str(message.get("Version", "")),
        tuple(str(item) for item in message.get_all("Requires-Dist", [])),
        tuple(row[0] for row in csv.reader(io.StringIO(record)) if row),
    )


def _package_files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_file():
            result[path.relative_to(root).as_posix()] = _file_hash(path)
    return result


def _dependency_identity(name: str) -> dict[str, Any]:
    """Installed declared closure, binding numerical assets as well as version labels."""
    pending = [name]
    seen: dict[str, Any] = {}
    selected_extras: dict[str, set[str]] = {}
    while pending:
        requirement = Requirement(pending.pop())
        if requirement.marker and not requirement.marker.evaluate():
            continue
        selected = requirement.name.lower().replace("_", "-")
        extras = set(requirement.extras) | {""}
        if selected in seen and extras <= selected_extras[selected]:
            continue
        selected_extras[selected] = extras | selected_extras.get(selected, set())
        try:
            distribution = importlib.metadata.distribution(selected)
        except importlib.metadata.PackageNotFoundError:
            continue
        metadata = distribution.read_text("METADATA")
        record = distribution.read_text("RECORD")
        if metadata is not None and record is not None:
            version, requires, inventory = _metadata_inventory(metadata, record)
            packaged = [Path(item) for item in inventory]
        else:
            version = distribution.version
            requires = tuple(distribution.requires or [])
            packaged = list(distribution.files or [])
        files: dict[str, str] = {}
        for item in packaged:
            if "__pycache__" in item.parts or item.suffix in {".pyc", ".pyo"}:
                continue
            path = Path(str(distribution.locate_file(item)))
            if path.is_file() and not any(part.endswith(".dist-info") for part in item.parts):
                files[str(item)] = _file_hash(path)
        direct = distribution.read_text("direct_url.json")
        if direct:
            url = urlsplit(str(json.loads(direct).get("url", "")))
            if url.scheme == "file":
                decoded_path = unquote(url.path)
                if os.name == "nt" and re.match(r"^/[A-Za-z]:", decoded_path):
                    decoded_path = decoded_path[1:]
                project = Path(decoded_path)
                source = project / "src"
                if source.is_dir():
                    files.update(
                        {"editable/" + key: value for key, value in _package_files(source).items()}
                    )
        seen[selected] = {"version": version, "files": files}
        for declared in requires:
            dependency = Requirement(declared)
            if dependency.marker and not any(
                dependency.marker.evaluate({"extra": extra}) for extra in extras
            ):
                continue
            dependency.marker = None
            pending.append(str(dependency))
    return seen


def implementation_identity(module_file: str, version: str) -> str:
    """Bind all packaged assets and installed dependency content; not a science claim."""
    root = Path(module_file).resolve().parent
    source = _package_files(root)
    dependencies = _dependency_identity(root.name.replace("_", "-"))
    return version + "+" + digest({"source": source, "dependencies": dependencies})
