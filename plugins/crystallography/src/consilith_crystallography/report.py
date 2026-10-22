"""Run the report notebook headless and return the exported HTML.

``marimo export html`` executes the notebook and writes a static page that needs no server. The
page loads the marimo front end from ``cdn.jsdelivr.net`` when it is opened; the room allows
exactly that origin for a report (``REPORT_ORIGINS`` in the room's app route). The export itself
needs no network. The report needs the ``platform`` extra (marimo); without it
``available()`` is false and the analysis simply has no report.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

MARIMO_VERSION = "0.25.2"
NOTEBOOK = Path(__file__).with_name("report_notebook.py")
EXPORT_TIMEOUT_S = 240
MAX_REPORT_BYTES = 8 * 1024 * 1024


def available() -> bool:
    return importlib.util.find_spec("marimo") is not None


def export_report(report: dict[str, Any]) -> bytes:
    """The polyhedra report (plain JSON) to a marimo HTML page.

    Raises ``RuntimeError`` if marimo is missing or the export fails. Error text never
    includes the report.
    """
    if not available():
        raise RuntimeError("marimo is not installed")
    with tempfile.TemporaryDirectory(prefix="crystal-report-") as directory:
        work = Path(directory)
        source = work / "input.json"
        source.write_text(json.dumps(report), encoding="utf-8")
        output = work / "report.html"
        # A read-only container image has no home directory: keep marimo's caches in the work
        # directory, and keep its telemetry and update checks off.
        env = {
            **os.environ,
            "CONSILITH_REPORT_INPUT": str(source),
            "HOME": str(work),
            "USERPROFILE": str(work),
            "XDG_CONFIG_HOME": str(work / "config"),
            "XDG_CACHE_HOME": str(work / "cache"),
            "XDG_STATE_HOME": str(work / "state"),
            "MARIMO_NO_TELEMETRY": "1",
            "DO_NOT_TRACK": "1",
            "PYTHONHASHSEED": "0",
            # The notebook imports this package; make that work whatever the working directory.
            "PYTHONPATH": os.pathsep.join(
                [str(Path(__file__).resolve().parents[1]), env_path()]
            ).strip(os.pathsep),
        }
        command = [
            sys.executable,
            "-m",
            "marimo",
            "export",
            "html",
            str(NOTEBOOK),
            "-o",
            str(output),
            "--no-include-code",
            "-f",
        ]
        try:
            done = subprocess.run(
                command,
                cwd=work,
                env=env,
                capture_output=True,
                timeout=EXPORT_TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"the report export took longer than {EXPORT_TIMEOUT_S} s") from None
        if done.returncode != 0 or not output.exists():
            tail = done.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
            raise RuntimeError("the report export failed: " + " | ".join(tail)[:600])
        html = output.read_bytes()
    if len(html) > MAX_REPORT_BYTES:
        raise RuntimeError("the exported report is larger than 8 MiB")
    return html


def env_path() -> str:
    return os.environ.get("PYTHONPATH", "")
