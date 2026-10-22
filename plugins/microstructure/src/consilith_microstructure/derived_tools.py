"""The report and training-set tools. Both read one run's trajectory block and nothing else.

The trajectory is the ``trajectory`` object of the ``evolution_report`` result, which carries
the same times, energies, grain counts and areas as the evolution tables and curves.
"""

import time
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from . import dataset
from .envelopes import derived_envelope, file_output, json_output
from .reporting import export_report
from .run_tables import DISCLAIMER, RunTables

REPORT_TOOL = "microstructure.report"
DATASET_TOOL = "microstructure.dataset"
NOTES = [
    DISCLAIMER,
    "Derived from the run's own computed tables; not a check of the physics.",
]


def report(
    trajectory: dict[str, Any],
    parameters: dict[str, Any] | None = None,
    references: list[str] | None = None,
) -> dict[str, Any]:
    """Export a marimo HTML report of one evolution run (headless, offline). ``references`` are
    lines the person or their agent chose (for example from Valency), shown as related work."""
    started = time.perf_counter()
    try:
        html = export_report(trajectory, parameters, references)
    except (ValueError, RuntimeError) as exc:  # messages never hold input values
        raise ToolError(str(exc)) from None
    return derived_envelope(
        REPORT_TOOL,
        {"trajectory": trajectory, "parameters": parameters, "references": references or []},
        [file_output("microstructure_report", html, "text/html", "report")],
        started,
        validity="Presentation of a hypothetical dimensionless 2D grain-growth run",
        notes=NOTES,
    )


def build_dataset(
    trajectory: dict[str, Any], parameters: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build 40 to 200 instruction rows from the run's tables (pure Python, no network)."""
    started = time.perf_counter()
    try:
        rows = dataset.build_rows(RunTables.from_trajectory(trajectory, parameters))
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    payload = dataset.to_jsonl(rows)
    return derived_envelope(
        DATASET_TOOL,
        {"trajectory": trajectory, "parameters": parameters},
        [
            file_output("training_set", payload, "application/jsonl", "dataset"),
            json_output("training_set_summary", dataset.summary(rows, payload)),
        ],
        started,
        validity="Illustrative question-and-answer text about a hypothetical dimensionless run",
        notes=NOTES,
    )
