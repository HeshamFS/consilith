"""Install and verify immutable, operator-selected plugin files without platform changes."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


def tree_digest(root: Path) -> str:
    if root.is_symlink():
        raise ValueError("plugin releases may not contain symlinks")
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("plugin releases may not contain symlinks")
        if path.is_file():
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not files:
        raise ValueError("plugin release is empty")
    return hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def release_path(volume: Path) -> Path:
    manifest = json.loads((volume / "active.json").read_text(encoding="utf-8"))
    identity = manifest["sha256"]
    if (
        not isinstance(identity, str)
        or len(identity) != 64
        or any(c not in "0123456789abcdef" for c in identity)
    ):
        raise ValueError("invalid plugin release digest")
    release = volume / "releases" / identity
    if not release.resolve().is_relative_to(volume.resolve()):
        raise ValueError("plugin release escapes its volume")
    if release.is_symlink() or tree_digest(release) != identity:
        raise ValueError("plugin release content does not match its pinned digest")
    return release


def install(source: Path, volume: Path) -> str:
    identity = tree_digest(source)
    releases = volume / "releases"
    if releases.is_symlink():
        raise ValueError("plugin releases may not contain symlinks")
    releases.mkdir(parents=True, exist_ok=True)
    target = releases / identity
    if not target.exists():
        temporary = Path(tempfile.mkdtemp(prefix=".install-", dir=releases))
        try:
            shutil.copytree(source, temporary, dirs_exist_ok=True)
            if tree_digest(temporary) != identity:
                raise ValueError("plugin changed during installation")
            temporary.rename(target)
        except BaseException:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise
    elif tree_digest(target) != identity:
        raise ValueError("existing immutable plugin release was modified")
    active = volume / "active.tmp"
    active.write_text(json.dumps({"sha256": identity}) + "\n", encoding="utf-8")
    os.replace(active, volume / "active.json")
    return identity


if __name__ == "__main__":
    print(f"Activated plugin release: {install(Path(sys.argv[1]), Path(sys.argv[2]))}")
