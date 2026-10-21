"""Check the public source snapshot; private regression suites run separately."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    uv = ["uv", "run", "--frozen", "--no-sync", "--no-default-groups", "--group", "platform"]
    paths = ["packages", "apps/room", "apps/referee", "tools", "examples/agentic-plugin-template"]
    npm = shutil.which("npm") or "npm"
    checks = [
        [*uv, "ruff", "check", *paths],
        [*uv, "ruff", "format", "--check", *paths],
        [*uv, "pyright"],
        [*uv, "lint-imports", "--config", "config/imports.ini"],
        [npm, "--prefix", "apps/web", "run", "typecheck"],
        [npm, "--prefix", "apps/web", "run", "lint"],
        [npm, "--prefix", "apps/web", "run", "build"],
        [npm, "--prefix", "apps/siwc", "run", "build"],
    ]
    failures = []
    for command in checks:
        print("==", " ".join(command), flush=True)
        if subprocess.run(command, cwd=ROOT, check=False).returncode:
            failures.append(command)
    if failures:
        print(f"FAILED: {len(failures)} checks")
        return 1
    print("Public source checks passed; no private regression results are implied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
