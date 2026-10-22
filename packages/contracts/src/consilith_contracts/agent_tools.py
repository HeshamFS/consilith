"""Tools, the catalog entry that shows them, number bindings, and the JSON-action envelope.

The model never types a number. Everything the model fills in (a ``Binding``, the envelopes) is
therefore built from strings, booleans and enums only, and ``assert_model_safe_schema`` proves
it on the JSON schema. Numbers reach the platform through bindings that name where the number
comes from (the person's own words, a result, a default, a choice, a preset, or a host-computed
derivation), never through a value the model wrote.

Tool names are provider-neutral aliases, so every provider sees the same tool block. The
canonical ``ref`` is what the platform records; the ``alias`` is what the model says.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator, Mapping
from enum import StrEnum
from typing import Annotated, Any, Literal, cast

from pydantic import Field, field_validator, model_validator

from .agents import Autonomy
from .base import Contract


class ToolKind(StrEnum):
    """The family a tool belongs to. Families keep separate namespaces and are never mixed."""

    PLATFORM = "platform"  # an op of the ops registry, for example ``input.set``
    PLUGIN = "plugin"  # ``plugin/<pluginId>/<server>/<tool>``: listed, run only via workflows
    SKILL = "skill"  # ``skill/<pluginId>/<name>``: read by id
    KB = "kb"  # ``kb/<pluginId>/<id>``: read by id
    UI = "ui"  # ``ui.*``: surfaces for the person's own chat
    PEER = "peer"  # ``peer.*``: agent-to-agent messages


class RiskClass(StrEnum):
    """How much a call can change. The autonomy matrix is generated from this."""

    R0 = "R0"  # read
    R1 = "R1"  # the person's own lane, proposals, private surfaces
    R2 = "R2"  # execution (starts a run)
    R3 = "R3"  # the owner confirms every time
    R4 = "R4"  # never offered to an agent


class Availability(StrEnum):
    """Whether this agent can use a catalog entry right now. Blocked entries stay visible."""

    AVAILABLE = "available"
    BLOCKED_BY_DRAWER = "blocked_by_drawer"
    BLOCKED_BY_ACTIVATION = "blocked_by_activation"
    BLOCKED_BY_AUTONOMY = "blocked_by_autonomy"


# Refs of the namespaced families start with their family. Platform, ui and peer refs are
# dotted op names with no slash.
_REF_PREFIX = {ToolKind.PLUGIN: "plugin/", ToolKind.SKILL: "skill/", ToolKind.KB: "kb/"}


class ToolSpec(Contract):
    """One tool as the catalog knows it.

    ``input_schema`` is the full JSON schema, held as data. It is exposed to the model only for
    callable tools (platform ops), and those must pass ``assert_model_safe_schema``. A plugin
    tool's schema may be numeric: the tool is described to the model, never called by it.
    """

    ref: str = Field(min_length=1, max_length=300)
    # Provider-safe name the model uses: ``<kind>_<slug>_<6-char hash>``, hash from ``ref`` alone.
    alias: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    kind: ToolKind
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict[str, Any])
    risk: RiskClass
    min_autonomy: Autonomy

    @model_validator(mode="after")
    def check_ref(self) -> ToolSpec:
        prefix = _REF_PREFIX.get(self.kind)
        if prefix is not None and not self.ref.startswith(prefix):
            raise ValueError(f"a {self.kind.value} tool ref starts with {prefix!r}")
        if prefix is None and "/" in self.ref:
            raise ValueError(f"a {self.kind.value} tool ref is a dotted op name with no slash")
        return self


class ReachableVia(Contract):
    """The capability and mode whose governed workflow runs a plugin tool."""

    capability: str
    mode: str


class CatalogEntry(Contract):
    """A tool plus whether this agent can use it, and why not when it cannot."""

    spec: ToolSpec
    availability: Availability = Availability.AVAILABLE
    # Plain words for the person, for example "ask the owner to enable skill X in Your agent".
    reason: str = ""
    # Plugin tools only: the (capability, mode) pairs that reach this tool. Empty means none.
    reachable_via: list[ReachableVia] = Field(default_factory=list[ReachableVia])

    @model_validator(mode="after")
    def blocked_entries_say_why(self) -> CatalogEntry:
        if self.availability is not Availability.AVAILABLE and not self.reason.strip():
            raise ValueError("a blocked catalog entry names the reason")
        return self


# --- number bindings -----------------------------------------------------------------------

_NUMBER_STRING = re.compile(r"^\s*[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][+-]?\d+)?\s*$")


def looks_numeric(text: str) -> bool:
    """True for a string that is a number ("350", "-1.5", "1e3", "3,5", Unicode digits)."""
    return _NUMBER_STRING.match(text) is not None


class BindingKind(StrEnum):
    LITERAL = "literal"  # ref: a handle (n1..nk) the server parsed from the person's own words
    RESULT = "result"  # ref: the id of a result
    DEFAULT = "default"  # no ref: the field's declared default
    CHOICE = "choice"  # ref: one of the field's declared choices
    PRESET = "preset"  # ref: a plugin-declared preset
    DERIVE = "derive"  # ref: the source (a live lane value or a current result id), op, by


BindingOp = Literal["scale", "offset"]


class Binding(Contract):
    """Where a number comes from. The model names the source; the host writes the value.

    A flat shape on purpose, so every provider can emit it:

    ===========  ====================================  =========  =========
    kind         ref                                   op         by
    ===========  ====================================  =========  =========
    literal      handle of the person's own words      -          -
    result       result id                             -          -
    default      -                                     -          -
    choice       a declared choice                     -          -
    preset       a plugin-declared preset              -          -
    derive       the value to start from ("from")      required   required
    ===========  ====================================  =========  =========

    For ``derive`` the host evaluates ``op`` (scale or offset) of the source by ``by``, a literal
    handle, with unit-aware arithmetic: "halve the pressure" without the model typing a number.

    A null, a missing key and ``""`` mean the same for ``ref``, ``op`` and ``by``. Nothing here
    is numeric, and a ``ref`` or ``by`` that is itself a number ("350") is refused (a choice may
    be any declared text).
    """

    kind: BindingKind
    ref: str | None = Field(default=None, max_length=200)
    op: BindingOp | None = None
    by: str | None = Field(default=None, max_length=200)

    @model_validator(mode="before")
    @classmethod
    def blank_is_missing(cls, data: Any) -> Any:
        if isinstance(data, dict):
            raw = cast(dict[str, Any], data)
            return {
                k: v for k, v in raw.items() if not (k in ("ref", "op", "by") and v in (None, ""))
            }
        return data

    @model_validator(mode="after")
    def check_combination(self) -> Binding:
        kind = self.kind
        if kind is BindingKind.DEFAULT:
            if self.ref is not None or self.op is not None or self.by is not None:
                raise ValueError("a default binding takes no ref, op or by")
            return self
        if self.ref is None:
            raise ValueError(f"a {kind.value} binding needs a ref")
        if kind is BindingKind.DERIVE:
            if self.op is None or self.by is None:
                raise ValueError("a derive binding needs ref, op and by")
        elif self.op is not None or self.by is not None:
            raise ValueError(f"a {kind.value} binding takes no op or by")
        if kind is not BindingKind.CHOICE and looks_numeric(self.ref):
            raise ValueError("a binding ref names a source and is never a number")
        if self.by is not None and looks_numeric(self.by):
            raise ValueError("a derive binding's by names a handle and is never a number")
        return self


# --- the JSON-action envelope --------------------------------------------------------------

MAX_CALLS_PER_REPLY = 4


def find_json_number(value: object, path: str = "$") -> str | None:
    """The path of the first JSON number in a parsed value, or None.

    Booleans and null are not numbers. This is the post-parse check on tool arguments: the model
    never types a number, so any number token in them is refused.
    """
    if value is None or isinstance(value, (bool, str)):
        return None
    if isinstance(value, (int, float)):
        return path
    if isinstance(value, Mapping):
        for key, item in cast(Mapping[object, object], value).items():
            hit = find_json_number(item, f"{path}.{key}")
            if hit is not None:
                return hit
        return None
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(cast(list[object], value)):
            hit = find_json_number(item, f"{path}[{index}]")
            if hit is not None:
                return hit
    return None


class EnvelopeCall(Contract):
    """One requested call: a tool alias and its arguments (strings, booleans, lists, objects)."""

    tool: str = Field(min_length=1, max_length=200)
    args: dict[str, Any] = Field(default_factory=dict[str, Any])

    @field_validator("args")
    @classmethod
    def args_hold_no_number(cls, args: dict[str, Any]) -> dict[str, Any]:
        where = find_json_number(args, "args")
        if where is not None:
            raise ValueError(f"{where} is a number; the model never types one, use a binding")
        return args


class CallEnvelope(Contract):
    """``{"do": "call", "calls": [{"tool", "args"}]}``: at most four calls in one reply."""

    do: Literal["call"]
    calls: list[EnvelopeCall] = Field(min_length=1, max_length=MAX_CALLS_PER_REPLY)


class SayEnvelope(Contract):
    """``{"do": "say", "text"}``: prose for the person. The host caps and guards the text."""

    do: Literal["say"]
    text: str


ActionEnvelope = Annotated[CallEnvelope | SayEnvelope, Field(discriminator="do")]


# --- the schema guard ----------------------------------------------------------------------


class UnsafeSchemaError(TypeError):
    """A JSON schema would let a model type a number."""


# Sub-schema positions, by how the keyword holds them.
_SCHEMA_KEYWORDS = (
    "items",
    "additionalItems",
    "additionalProperties",
    "contains",
    "not",
    "if",
    "then",
    "else",
    "propertyNames",
    "unevaluatedItems",
    "unevaluatedProperties",
    "contentSchema",
)
_SCHEMA_LIST_KEYWORDS = ("allOf", "anyOf", "oneOf", "prefixItems")
_SCHEMA_MAP_KEYWORDS = (
    "properties",
    "patternProperties",
    "$defs",
    "definitions",
    "dependentSchemas",
)
# Keywords whose value is data that must hold no number.
_LITERAL_KEYWORDS = ("enum", "const", "default", "examples", "example")
# Bounds on a value. Size bounds (minLength, maxItems, ...) stay allowed: they do not let the
# model write a number, and the envelope's "at most four calls" needs ``maxItems``.
_BOUND_KEYWORDS = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf")


def _schema_nodes(node: object, path: str) -> Iterator[tuple[str, dict[str, Any]]]:
    """Every sub-schema of ``node`` with a readable path. ``$ref`` is not followed: the targets
    sit in ``$defs`` and are visited there."""
    if not isinstance(node, dict):
        return
    schema = cast(dict[str, Any], node)
    yield path, schema
    for key in _SCHEMA_KEYWORDS:
        child = schema.get(key)
        if isinstance(child, list):  # the older tuple form of ``items``
            for index, item in enumerate(cast(list[object], child)):
                yield from _schema_nodes(item, f"{path}.{key}[{index}]")
        else:
            yield from _schema_nodes(child, f"{path}.{key}")
    for key in _SCHEMA_LIST_KEYWORDS:
        children = schema.get(key)
        if isinstance(children, list):
            for index, item in enumerate(cast(list[object], children)):
                yield from _schema_nodes(item, f"{path}.{key}[{index}]")
    for key in _SCHEMA_MAP_KEYWORDS:
        named = schema.get(key)
        if isinstance(named, dict):
            for name, item in cast(dict[str, object], named).items():
                yield from _schema_nodes(item, f"{path}.{key}.{name}")


def _numeric_types(schema: dict[str, Any]) -> bool:
    declared = schema.get("type")
    types: list[object] = cast(list[object], declared) if isinstance(declared, list) else [declared]
    return "number" in types or "integer" in types


def _holds_number(value: object) -> bool:
    """A number, or text that is one, anywhere inside a literal keyword's value."""
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        return looks_numeric(value)
    if isinstance(value, dict):
        return any(_holds_number(item) for item in cast(dict[str, object], value).values())
    if isinstance(value, list):
        return any(_holds_number(item) for item in cast(list[object], value))
    return False


def assert_structured_schema(schema: dict[str, Any]) -> None:
    """For ``structured()`` response schemas: reject only numeric ``type`` (number or integer).

    Those schemas legitimately use id patterns, recursion and optional fields
    (``WorkspacePlan``), so the stricter checks of ``assert_model_safe_schema`` do not apply.
    """
    for path, node in _schema_nodes(schema, "$"):
        if _numeric_types(node):
            raise UnsafeSchemaError(f"{path} is numeric: the model never produces numbers")


def assert_model_safe_schema(schema: dict[str, Any]) -> None:
    """For every schema a model fills in (tool arguments, the envelope, an ad-hoc plan).

    Rejects, at any depth of the schema:

    - numeric types (``number``, ``integer``);
    - numeric values in ``enum``, ``const``, ``default``, ``examples`` and ``example``, including
      text that is a number;
    - value bounds (``minimum``, ``maximum``, ``exclusiveMinimum``, ``exclusiveMaximum``,
      ``multipleOf``). Size bounds such as ``maxItems`` and ``maxLength`` are allowed;
    - a ``pattern`` that admits digits, in a property or in ``patternProperties`` keys. A pattern
      that is not anchored at both ends admits digits in the text around the match, so it counts;
    - any ``format``: every registered format (date, time, uuid, ipv4, ...) admits digits.
    """
    for path, node in _schema_nodes(schema, "$"):
        if _numeric_types(node):
            raise UnsafeSchemaError(f"{path} is numeric: the model never produces numbers")
        for key in _LITERAL_KEYWORDS:
            if key in node and _holds_number(node[key]):
                raise UnsafeSchemaError(f"{path}.{key} holds a number")
        for key in _BOUND_KEYWORDS:
            if key in node:
                raise UnsafeSchemaError(f"{path}.{key} bounds a number")
        if "format" in node:
            raise UnsafeSchemaError(f"{path}.format admits digits")
        pattern = node.get("pattern")
        if isinstance(pattern, str) and pattern_admits_digits(pattern):
            raise UnsafeSchemaError(f"{path}.pattern admits digits")
        names = node.get("patternProperties")
        if isinstance(names, dict):
            for name in cast(dict[str, object], names):
                if pattern_admits_digits(name):
                    raise UnsafeSchemaError(f"{path}.patternProperties admits digits: {name!r}")


# --- does a regular expression admit digits? ------------------------------------------------

_DIGIT_CATEGORIES = frozenset({"Nd", "No", "Nl"})
_QUANTIFIER = re.compile(r"\{(?:\d+(?:,\d*)?|,\d+)\}")
_WIDE_RANGE = 2048  # a class range wider than this is treated as admitting digits


def _is_digit(char: str) -> bool:
    return unicodedata.category(char) in _DIGIT_CATEGORIES


def _range_has_digit(low: str, high: str) -> bool:
    if ord(high) - ord(low) > _WIDE_RANGE:
        return True
    return any(_is_digit(chr(code)) for code in range(ord(low), ord(high) + 1))


def _escape(pattern: str, at: int) -> tuple[str | None, bool, int]:
    """Read one escape that starts after the backslash at ``at - 1``.

    Returns ``(char, wide, next_index)``. ``char`` is the single character it stands for, or None
    when it is a class or zero-width. ``wide`` is True when it can match a digit (``\\d``, ``\\w``,
    ``\\D``, ``\\W``, ``\\S``, a property or a named character).
    """
    if at >= len(pattern):
        return None, True, at
    c = pattern[at]
    if c in "dwDWSpPN":
        return None, True, at + 1
    if c in "sbBAZzG":
        return None, False, at + 1
    if c in "0123456789":
        text = pattern[at : at + 3]
        text = text[: len(text) - len(text.lstrip("0123456789"))]
        if text[0] == "0" or (
            len(text) == 3 and text[0] in "0123" and set(text) <= set("01234567")
        ):
            octal = re.match(r"[0-7]{1,3}", text)
            used = octal.group(0) if octal else text[0]
            return chr(int(used, 8)), False, at + len(used)
        return None, False, at + min(len(text), 2)  # a back-reference repeats text already scanned
    for letter, width in (("x", 2), ("u", 4), ("U", 8)):
        if c == letter:
            hexa = pattern[at + 1 : at + 1 + width]
            if len(hexa) == width and re.fullmatch(r"[0-9a-fA-F]+", hexa):
                code = int(hexa, 16)
                if code <= 0x10FFFF:
                    return chr(code), False, at + 1 + width
            return None, True, at + 1
    controls = {"n": "\n", "r": "\r", "t": "\t", "f": "\f", "v": "\v", "a": "\a", "e": "\x1b"}
    return controls.get(c, c), False, at + 1


def _scan_class(pattern: str, at: int) -> tuple[bool, int]:
    """Scan the character class whose ``[`` is at ``at``: (admits a digit, index after ``]``)."""
    i = at + 1
    if i < len(pattern) and pattern[i] == "^":
        negated, i = True, i + 1
    else:
        negated = False
    admits = negated  # a negated class admits whatever it does not list, digits included
    first = True
    previous: str | None = None  # the single character just read, a range may start here
    while i < len(pattern) and (pattern[i] != "]" or first):
        first = False
        c = pattern[i]
        item: str | None
        if c == "\\":
            item, wide, i = _escape(pattern, i + 1)
            admits = admits or wide
        else:
            item, i = c, i + 1
        if c == "-" and previous is not None and i < len(pattern) and pattern[i] != "]":
            if pattern[i] == "\\":
                end, wide, i = _escape(pattern, i + 1)
                admits = admits or wide
            else:
                end, i = pattern[i], i + 1
            if end is None or _range_has_digit(previous, end):
                admits = True
            previous = None
            continue
        if item is not None and _is_digit(item):
            admits = True
        previous = item
    return admits, i + 1


def pattern_admits_digits(pattern: str) -> bool:
    """True when some string the regular expression accepts can contain a digit.

    Conservative: it says True for anything it cannot reason about (invalid syntax, inline
    flags, conditionals, Unicode properties). A digit is any character of Unicode category Nd,
    No or Nl. Negated classes, ``.``, ``\\w``, ``\\d``, ``\\S``, ``\\D`` and ``\\W`` admit digits.
    Lookarounds consume nothing and are skipped. Every top-level alternative must start with
    ``^`` and end with ``$``: JSON Schema patterns are searched, not matched, so an unanchored
    pattern accepts digits in the text around the match.
    """
    try:
        re.compile(pattern)
    except re.error:
        return True
    branches: list[list[str]] = [[]]  # piece kinds per top-level alternative
    open_groups: list[bool] = []  # per open group: is it a lookaround
    skipping = 0
    i = 0
    while i < len(pattern):
        c = pattern[i]
        depth0 = not open_groups
        if c == "\\":
            char, wide, i = _escape(pattern, i + 1)
            kind = "other"
            letter = pattern[i - 1]
            if char is None and not wide and letter == "A":
                kind = "start"
            elif char is None and not wide and letter in ("Z", "z"):
                kind = "end"
            elif char is None and not wide and letter in ("b", "B", "G"):
                continue  # zero-width: not a piece
            if not skipping and (wide or (char is not None and _is_digit(char))):
                return True
            if depth0:
                branches[-1].append(kind)
            continue
        if c == "[":
            admits, i = _scan_class(pattern, i)
            if admits and not skipping:
                return True
            if depth0:
                branches[-1].append("other")
            continue
        if c == "(":
            if depth0:
                branches[-1].append("other")
            if pattern.startswith("(?", i):
                if pattern.startswith("(?:", i):
                    open_groups.append(False)
                    i += 3
                elif pattern.startswith(("(?=", "(?!"), i):
                    open_groups.append(True)
                    skipping += 1
                    i += 3
                elif pattern.startswith(("(?<=", "(?<!"), i):
                    open_groups.append(True)
                    skipping += 1
                    i += 4
                elif named := re.match(r"\(\?P?<[A-Za-z_][A-Za-z0-9_]*>", pattern[i:]):
                    open_groups.append(False)
                    i += named.end()
                elif backref := re.match(r"\(\?P=[A-Za-z_][A-Za-z0-9_]*\)", pattern[i:]):
                    i += backref.end()  # repeats text already scanned; opens no group
                else:
                    return True  # flags, conditionals, atomic groups, comments: not reasoned about
            else:
                open_groups.append(False)
                i += 1
            continue
        if c == ")":
            if open_groups and open_groups.pop():
                skipping -= 1
            i += 1
            continue
        if c == "|":
            if depth0:
                branches.append([])
            i += 1
            continue
        if c in "*+?":
            if depth0 and branches[-1] and branches[-1][-1] in ("start", "end"):
                branches[-1][-1] = "other"  # a quantified anchor can vanish
            i += 1
            continue
        if c == "{":
            quantifier = _QUANTIFIER.match(pattern, i)
            if quantifier:
                if depth0 and branches[-1] and branches[-1][-1] in ("start", "end"):
                    branches[-1][-1] = "other"
                i = quantifier.end()
                continue
        if c in "^$":
            if depth0:
                branches[-1].append("start" if c == "^" else "end")
            i += 1
            continue
        if c == ".":
            if not skipping:
                return True
        elif not skipping and _is_digit(c):
            return True
        if depth0:
            branches[-1].append("other")
        i += 1
    return any(not (b and b[0] == "start" and b[-1] == "end") for b in branches)
