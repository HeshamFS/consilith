"""Run the report notebook headless and return the exported HTML.

``marimo export html`` executes the notebook and writes a static page. The page needs no
server. It loads the marimo front end from ``cdn.jsdelivr.net`` when it is opened, and
nothing else (see ``ORIGINS``); the export itself needs no network.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .run_tables import RunTables

MARIMO_VERSION = "0.25.2"
NOTEBOOK = Path(__file__).with_name("report_notebook.py")
EXPORT_TIMEOUT_S = 300
MAX_REPORT_BYTES = 8 * 1024 * 1024
# Origins the exported page contacts when opened, by what they serve. Checked by a test
# against a real export; ``origins`` below reads them from any page.
ORIGINS = {"cdn.jsdelivr.net": ["script", "style", "font", "image", "manifest"]}


def origins(html: str) -> list[str]:
    """Every host an absolute http(s) URL in the page names, sorted."""
    return sorted({m.group(1) for m in re.finditer(r"https?://([A-Za-z0-9.-]+)", html)})


MAX_REFERENCES = 8
MAX_REFERENCE_CHARS = 400


def clean_references(references: object) -> list[str]:
    """At most eight non-empty lines of plain text, each at most 400 characters."""
    if not isinstance(references, list):
        return []
    lines = [" ".join(str(r).split()) for r in references if isinstance(r, str)]
    return [r[:MAX_REFERENCE_CHARS] for r in lines if r][:MAX_REFERENCES]


def export_report(trajectory: object, parameters: object = None, references: object = None) -> bytes:
    """Validated run tables to a marimo HTML report (bytes).

    Raises ``ValueError`` for a bad table and ``RuntimeError`` if the export fails or the page
    is larger than the report budget. Error text never includes the input values.
    """
    tables = RunTables.from_trajectory(trajectory, parameters)
    with tempfile.TemporaryDirectory(prefix="report-") as directory:
        work = Path(directory)
        source = work / "input.json"
        source.write_text(
            json.dumps(
                {
                    "trajectory": {
                        "times": tables.times,
                        "energy": tables.energy,
                        "visibleGrainCounts": tables.counts,
                        "grainIds": tables.grain_ids,
                        "diffuseAreas": tables.areas,
                    },
                    "parameters": tables.parameters,
                    "references": clean_references(references),
                }
            ),
            encoding="utf-8",
        )
        output = work / "report.html"
        # Run a copy: marimo writes a cache beside the notebook, and the installed package
        # directory is read-only in the container.
        notebook = work / NOTEBOOK.name
        shutil.copyfile(NOTEBOOK, notebook)
        # The head (theme and size over the MCP Apps bridge) is read next to the notebook.
        shutil.copyfile(NOTEBOOK.parent / "report_head.html", work / "report_head.html")
        # A read-only container image has no home directory: keep marimo's caches in the
        # work directory, and keep its telemetry and update checks off.
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
        }
        command = [
            sys.executable,
            "-m",
            "marimo",
            "export",
            "html",
            str(notebook),
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
