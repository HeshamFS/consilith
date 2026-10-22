"""Packaged viewer access without importing the optional MCP server."""

from importlib import resources


def viewer_html() -> str:
    return resources.files("consilith_crystallography").joinpath("viewer.html").read_text(encoding="utf-8")
