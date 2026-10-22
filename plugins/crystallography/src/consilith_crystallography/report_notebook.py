# /// script
# [tool.marimo.display]
# theme = "system"
# ///
"""Crystal structure and coordination report notebook, run headless by ``marimo export html``.

The input is one JSON file named by CONSILITH_REPORT_INPUT: the polyhedra report that
``analyze_polyhedra`` returns. Nothing else is read and nothing is fetched. The pieces live in
``report_view`` so they can be tested without marimo.
"""

import marimo

__generated_with = "0.25.2"
app = marimo.App(width="full", html_head_file="report_head.html")


@app.cell
def _():
    import json
    import os

    import marimo as mo

    from consilith_crystallography import report_view

    return json, mo, os, report_view


@app.cell
def _(json, os):
    with open(os.environ["CONSILITH_REPORT_INPUT"], encoding="utf-8") as _stream:
        report = json.load(_stream)
    return (report,)


@app.cell
def _(mo, report, report_view):
    mo.Html(report_view.page(report))
    return


if __name__ == "__main__":
    app.run()
