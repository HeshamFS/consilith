"""The Consilith colour tokens the crystal report uses, hard-coded.

The values are copied from ``apps/web/src/shared/theme/tokens.css`` (Paper for light, Stage for
dark). The report is a separate page that cannot import the web app, so a change to the tokens
there is a change here too; ``tests`` compare the two files.
"""

LIGHT = {
    "bg": "#fbfaf7",
    "surface": "#ffffff",
    "surface-2": "#f4f2ec",
    "line": "#e2ded5",
    "line-strong": "#cfcac0",
    "ink": "#1c1b18",
    "ink-2": "#5b5850",
    "ink-3": "#6b675e",
    "blue": "#2a50cc",
    "magenta": "#c4296a",
    "teal": "#0d8089",
    "violet": "#5b2ea6",
    "amber": "#b4530f",
    "olive": "#4b5a2a",
    "warn-bg": "#fbefd5",
    "warn-fg": "#7a4f00",
}

DARK = {
    "bg": "#1c1b18",
    "surface": "#2a2a26",
    "surface-2": "#34342f",
    "line": "#3b3b36",
    "line-strong": "#4a4943",
    "ink": "#f0ede6",
    "ink-2": "#b5b1a7",
    "ink-3": "#a29d91",
    "blue": "#7c9bff",
    "magenta": "#ff7fb5",
    "teal": "#5fd3d9",
    "violet": "#b79cff",
    "amber": "#f5a15c",
    "olive": "#a8c46a",
    "warn-bg": "#3a2e14",
    "warn-fg": "#f2c66b",
}

# The order series take their colours in, as in the web app's charts.
SERIES = ("blue", "amber", "teal", "magenta", "violet", "olive")

FONT_UI = '"Instrument Sans", "Instrument Sans Variable", system-ui, -apple-system, "Segoe UI", sans-serif'
FONT_DISPLAY = '"Instrument Serif", Georgia, "Times New Roman", serif'
FONT_MONO = '"JetBrains Mono", "JetBrains Mono Variable", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace'


def variables(tokens: dict[str, str]) -> str:
    return "".join(f"--{name}:{value};" for name, value in tokens.items())
