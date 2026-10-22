# /// script
# [tool.marimo.display]
# theme = "system"
# ///
"""Grain-growth report notebook, run headless by ``marimo export html``.

The input is one JSON file named by CONSILITH_REPORT_INPUT: the trajectory block of an
evolution report and the run parameters. Nothing else is read, and nothing is fetched. The
pieces live in ``report_view`` so they can be tested without marimo.
"""

import marimo

__generated_with = "0.25.2"
app = marimo.App(width="full", html_head_file="report_head.html")


@app.cell
def _():
    import json
    import os

    import marimo as mo

    from consilith_microstructure import report_view
    from consilith_microstructure.run_tables import RunTables

    return RunTables, json, mo, os, report_view


@app.cell
def _(RunTables, json, os):
    with open(os.environ["CONSILITH_REPORT_INPUT"], encoding="utf-8") as _stream:
        _payload = json.load(_stream)
    tables = RunTables.from_trajectory(_payload["trajectory"], _payload.get("parameters"))
    references = list(_payload.get("references") or [])
    return references, tables


@app.cell
def _(references, report_view, tables):
    page = (
        report_view.stylesheet()
        + report_view.header(tables)
        + report_view.introduction(references)
        + report_view.kpis(tables)
        + report_view.charts(tables)
        + report_view.tables_section(tables)
        + report_view.summary_section(tables)
        + report_view.footer(tables)
    )
    return (page,)


@app.cell
def _(mo, page):
    mo.Html(page)
    return


if __name__ == "__main__":
    app.run()
