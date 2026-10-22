"""The literal parser and number bindings (BUILD_SPEC section 6, "Prepare" and "Numbers keep
provenance").

The model never types a number. A number enters the platform from one of a few sources, and the
first is the person's own words: this module reads numbers with units, ranges, lists and number
words out of the person's typed messages (the current one and their last two, never a peer's, a
plugin's or a tool's) into handles ``n1..nk``. Each handle records its message id, its source
span and its left context. Spans that are pasted or quoted (code fences, ``>`` quotes, blocks
over 400 characters) are flagged ``from_quote``.

``resolve_number`` then turns a ``Binding`` the model wrote into a concrete value the host
writes: ``literal`` (a handle), ``result`` (a current, non-blocked result), ``default``,
``choice``, ``preset`` and ``derive`` (unit-aware scale or offset of a live lane value or a
current result). Anything uncertain comes back with ``reasons``, and the tool host turns those
into a confirm card that echoes the value, the unit and the source span.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, replace
from typing import Any

from consilith_contracts import Binding, BindingKind, FieldDef, ResultRecord
from consilith_contracts.results import ResultStatus, Stamp
from consilith_contracts.units import UnitError, check, convert, parse

MAX_MESSAGE_CHARS = 4000
QUOTE_BLOCK_CHARS = 400
LEFT_CONTEXT_WORDS = 8

# Reasons a handle makes a call uncertain (BUILD_SPEC step 3).
NO_UNIT = "no_unit"
DECIMAL_COMMA = "decimal_comma"
RANGE = "range"
PERCENT = "percent"
FROM_QUOTE = "from_quote"
LABEL_MISMATCH = "label_mismatch"

_WORD_VALUES: dict[str, float] = {
    "zero": 0, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
    "half": 0.5, "halve": 0.5, "halved": 0.5, "double": 2, "doubled": 2, "twice": 2,
    "triple": 3, "tripled": 3, "thrice": 3, "quadruple": 4, "quarter": 0.25, "dozen": 12,
}  # fmt: skip
_MULTIPLIERS = {"hundred": 100.0, "thousand": 1_000.0, "million": 1_000_000.0}
_TENS = {w for w, v in _WORD_VALUES.items() if v >= 20 and v % 10 == 0 and v < 100}
_ONES = {w for w, v in _WORD_VALUES.items() if 2 <= v <= 9}
_WORD_RE = re.compile(
    r"\b(" + "|".join(sorted([*_WORD_VALUES, *_MULTIPLIERS], key=len, reverse=True)) + r")\b",
    re.I,
)

# English words that are also units in pint. A number followed by one of them has no unit.
_NOT_UNITS = frozenset(
    "a an in at is as are to of or on by it if so do be and the per for from with".split()  # noqa: SIM905
)
_NUM = re.compile(
    r"(?<![\w.,])(?P<sign>[+-]?)"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?|\.\d+)"
    r"(?:[eE](?P<exp>[+-]?\d+))?"
)
_UNIT = re.compile(r"[ \t]?(?P<unit>%|[°µμΩA-Za-z][A-Za-z0-9°µμΩ/^*·.-]*)")
_RANGE_GAP = re.compile(r"^\s*(?:-|–|—|to|and|until|through)\s*$", re.I)
_LIST_GAP = re.compile(r"^\s*(?:,|;|and|or|,\s*and|,\s*or|&)\s*$", re.I)
_FENCE = re.compile(r"```.*?(?:```|\Z)", re.S)
_SENTENCE_BREAK = re.compile(r"[.;!?\n]")


@dataclass(frozen=True)
class Handle:
    """One number the person wrote, as a handle the model may cite and never type."""

    id: str  # "n1".."nk"
    value: float
    unit: str  # a pint unit string, "" for none, "percent" for a percent
    text: str  # the source span, as written
    start: int
    end: int
    msg_id: str
    left: str  # the words before the span, in the same sentence
    kind: str = "number"  # "number" or "word" (half, double, ...)
    from_quote: bool = False
    reasons: tuple[str, ...] = ()
    group: str | None = None  # "range:<k>" or "list:<k>" when written together
    current: bool = False  # in the message this turn answers

    def echo(self) -> str:
        """The span for a card or a reason: the left context and the span as written."""
        return f"{self.left} {self.text}".strip()


class LiteralSet:
    """The handles of one turn, in the order the person wrote them."""

    def __init__(self, handles: Iterable[Handle] = (), *, quoted: bool = False) -> None:
        self._handles = list(handles)
        self._by_id = {h.id: h for h in self._handles}
        self.quoted = quoted  # the current message holds a pasted or quoted region

    def get(self, handle_id: str) -> Handle | None:
        return self._by_id.get(handle_id)

    def __iter__(self) -> Iterator[Handle]:
        return iter(self._handles)

    def __len__(self) -> int:
        return len(self._handles)

    def ids(self) -> frozenset[str]:
        return frozenset(self._by_id)

    def display(self) -> dict[str, str]:
        """``n1 -> "350 K"``: what ``{{lit:N}}`` expands to."""
        return {h.id: h.text for h in self._handles}

    def for_prompt(self) -> list[dict[str, Any]]:
        """What the model may read about the handles: the span and the words before it."""
        return [
            {
                "id": h.id,
                "text": h.text,
                "unit": h.unit,
                "after": h.left,
                "quoted": h.from_quote,
                "current": h.current,
            }
            for h in self._handles
        ]


# --- parsing -------------------------------------------------------------------------------


def _quote_spans(text: str) -> list[tuple[int, int]]:
    spans = [m.span() for m in _FENCE.finditer(text)]
    pos = 0
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith(">"):
            spans.append((pos, pos + len(line)))
        pos += len(line)
    pos = 0
    for block in re.split(r"(\n\s*\n)", text):
        if len(block) > QUOTE_BLOCK_CHARS and not re.fullmatch(r"\n\s*\n", block):
            spans.append((pos, pos + len(block)))
        pos += len(block)
    return spans


def _in_spans(index: int, spans: Sequence[tuple[int, int]]) -> bool:
    return any(a <= index < b for a, b in spans)


def _unit_at(text: str, pos: int) -> tuple[str, int] | None:
    """The unit written right after a number: its pint string and the end of its span."""
    m = _UNIT.match(text, pos)
    if m is None:
        return None
    raw = m.group("unit").rstrip(".-")
    if raw == "%":
        return "percent", m.start("unit") + 1
    if not raw:
        return None
    end = m.start("unit") + len(raw)
    low = raw.lower()
    if low in _NOT_UNITS:
        return None
    if low == "percent":
        return "percent", end
    norm = {"°c": "degC", "°f": "degF", "°": "degree"}.get(low, raw)
    if norm in ("degree",):
        return None
    try:
        parse(norm)
    except UnitError:
        return None
    return norm, end


def _left_context(text: str, start: int, floor: int) -> str:
    """The words before a span in its sentence, stopping at the previous number's end."""
    window = text[floor:start]
    cut = max((m.end() for m in _SENTENCE_BREAK.finditer(window)), default=0)
    words = window[cut:].split()
    return " ".join(words[-LEFT_CONTEXT_WORDS:]).strip(" ,:=(")


def _number_value(num: str, exp: str | None, sign: str) -> tuple[float, tuple[str, ...]]:
    reasons: tuple[str, ...] = ()
    if "," in num:
        reasons = (DECIMAL_COMMA,)
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", num):
            num = num.replace(",", "")  # 1,000: thousands, still flagged
        else:
            num = num.replace(",", ".")  # 3,5: a decimal comma
    value = float(num)
    if exp:
        value *= 10 ** int(exp)
    if sign == "-":
        value = -value
    return value, reasons


def _word_runs(text: str) -> list[tuple[int, int, float, str]]:
    """Number words, composed where they sit together (twenty five, two hundred)."""
    found = [(m.start(), m.end(), m.group(1).lower()) for m in _WORD_RE.finditer(text)]
    runs: list[tuple[int, int, float, str]] = []
    i = 0
    while i < len(found):
        start, end, word = found[i]
        value = _WORD_VALUES.get(word, _MULTIPLIERS.get(word, 0.0))
        j = i + 1
        while j < len(found):
            nstart, nend, nword = found[j]
            if not re.fullmatch(r"[ \t-]+", text[end:nstart]):
                break
            if word in _TENS and nword in _ONES:
                value += _WORD_VALUES[nword]
            elif nword in _MULTIPLIERS and (word in _ONES or word in _MULTIPLIERS or value >= 1):
                value *= _MULTIPLIERS[nword]
            else:
                break
            end, word = nend, nword
            j += 1
        if word in _MULTIPLIERS and j == i + 1:
            value = _MULTIPLIERS[word]  # "a hundred": the word alone
        runs.append((start, end, value, text[start:end]))
        i = j
    return runs


def parse_message(msg_id: str, text: str, *, current: bool = False) -> tuple[list[Handle], bool]:
    """The handles of one message (ids are assigned later) and whether it holds a quoted region.
    ``text`` is cut at ``MAX_MESSAGE_CHARS`` first."""
    text = text[:MAX_MESSAGE_CHARS]
    quotes = _quote_spans(text)
    items: list[dict[str, Any]] = []
    for m in _NUM.finditer(text):
        value, reasons = _number_value(m.group("num"), m.group("exp"), m.group("sign"))
        unit, end = "", m.end()
        found = _unit_at(text, m.end())
        if found is not None:
            unit, end = found
        items.append(
            {
                "start": m.start(),
                "end": end,
                "value": value,
                "unit": unit,
                "reasons": reasons,
                "kind": "number",
            }
        )
    taken = [(i["start"], i["end"]) for i in items]
    for start, end, value, _span in _word_runs(text):
        if any(a < end and start < b for a, b in taken):
            continue
        items.append(
            {"start": start, "end": end, "value": value, "unit": "", "reasons": (), "kind": "word"}
        )
    items.sort(key=lambda i: i["start"])
    _group(text, items)
    handles: list[Handle] = []
    prev_end = 0
    for item in items:
        start, end = item["start"], item["end"]
        reasons = list(item["reasons"])
        if item.get("range"):
            reasons.append(RANGE)
        if item["unit"] == "percent":
            reasons.append(PERCENT)
        handles.append(
            Handle(
                id="",
                value=item["value"],
                unit=item["unit"],
                text=text[start:end].strip(),
                start=start,
                end=end,
                msg_id=msg_id,
                left=_left_context(text, start, prev_end),
                kind=item["kind"],
                from_quote=_in_spans(start, quotes),
                reasons=tuple(dict.fromkeys(reasons)),
                group=item.get("group"),
                current=current,
            )
        )
        prev_end = end
    return handles, bool(quotes)


def _group(text: str, items: list[dict[str, Any]]) -> None:
    """Mark ranges and lists, and let the last member's unit stand for the unit-less ones."""
    k = 0
    i = 0
    while i < len(items) - 1:
        run = [i]
        kind = ""
        while run[-1] + 1 < len(items):
            a, b = items[run[-1]], items[run[-1] + 1]
            gap = text[a["end"] : b["start"]]
            if a["kind"] != "number" or b["kind"] != "number":
                break
            if _RANGE_GAP.match(gap) and kind in ("", "range") and len(run) == 1:
                kind = "range"
            elif _LIST_GAP.match(gap) and kind in ("", "list"):
                kind = "list"
            else:
                break
            run.append(run[-1] + 1)
        if len(run) > 1:
            k += 1
            unit = items[run[-1]]["unit"]
            for idx in run:
                items[idx]["group"] = f"{kind}:{k}"
                if kind == "range":
                    items[idx]["range"] = True
                if not items[idx]["unit"] and unit:
                    items[idx]["unit"] = unit
            i = run[-1] + 1
        else:
            i += 1


def parse_literals(
    messages: Sequence[tuple[str, str]], *, current_id: str | None = None
) -> LiteralSet:
    """Handles ``n1..nk`` for the person's own messages, oldest first. The caller passes the
    current message and their last two; nobody else's text is ever parsed."""
    handles: list[Handle] = []
    quoted = False
    for msg_id, text in messages:
        is_current = msg_id == current_id
        found, has_quote = parse_message(msg_id, text, current=is_current)
        quoted = quoted or (has_quote and is_current)
        handles.extend(found)
    numbered = [
        replace(h, id=f"n{i}", group=None if h.group is None else f"{h.msg_id}/{h.group}")
        for i, h in enumerate(handles, start=1)
    ]
    return LiteralSet(numbered, quoted=quoted)


# --- bindings ------------------------------------------------------------------------------


class BindingError(Exception):
    """A binding the host cannot resolve; ``code`` is the typed error the model sees."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Resolved:
    """A binding turned into the value the host writes."""

    value: Any  # {"v": float, "unit": str} for numbers, else the declared choice
    source: str  # "literal n1", "result res-...", "derived ..."
    echo: str  # the words for a card or a log reason
    reasons: tuple[str, ...] = ()  # why this needs a confirm card
    handle: str | None = None
    result_id: str | None = None


ResultLookup = Callable[[str], ResultRecord | None]
LiveLookup = Callable[[str], dict[str, Any] | None]
PresetLookup = Callable[[str], dict[str, Any] | None]


def _label_words(field_def: FieldDef, aliases: Sequence[str]) -> set[str]:
    words: set[str] = set()
    for source in (field_def.name, field_def.label, *aliases):
        for w in re.split(r"[^A-Za-z0-9]+", source.lower()):
            if len(w) >= 3:
                words.add(w)
    return words


def _label_matches(field_def: FieldDef, aliases: Sequence[str], left: str) -> bool:
    context = {w for w in re.split(r"[^A-Za-z0-9]+", left.lower()) if w}
    wanted = _label_words(field_def, aliases)
    return any(
        w in context or any(c.startswith(w) or w.startswith(c) for c in context if len(c) >= 4)
        for w in wanted
    )


def _number_unit(unit: str, field_def: FieldDef) -> tuple[str, tuple[str, ...]]:
    """The unit to store, and a reason when the person gave none."""
    if unit == "":
        return field_def.unit, (NO_UNIT,) if field_def.unit else ()
    if field_def.unit:
        try:
            check(unit, field_def.unit)
        except UnitError as exc:
            raise BindingError(
                "unit_mismatch", f"{field_def.name} takes {field_def.unit}: {exc}"
            ) from exc
    return unit, ()


def _usable_result(rec: ResultRecord | None, ref: str) -> ResultRecord:
    if rec is None:
        raise BindingError("unknown_result", f"there is no result {ref}")
    if rec.status != ResultStatus.CURRENT:
        raise BindingError(
            "result_stale", f"result {ref} is {rec.status.value}: it cannot feed an edit"
        )
    if rec.stamp == Stamp.BLOCKED:
        raise BindingError("result_blocked", f"result {ref} is blocked: it cannot feed an edit")
    if (
        rec.kind != "number"
        or isinstance(rec.value, bool)
        or not isinstance(rec.value, int | float)
    ):
        raise BindingError("result_not_a_number", f"result {ref} is not a number")
    return rec


def _fmt(value: float) -> str:
    return f"{value:g}"


def resolve_number(
    binding: Binding,
    field_def: FieldDef,
    *,
    literals: LiteralSet,
    results: ResultLookup,
    live: LiveLookup,
    aliases: Sequence[str] = (),
    presets: PresetLookup | None = None,
) -> Resolved:
    """The concrete ``{"v", "unit"}`` a number binding stands for.

    Raises ``BindingError`` for a binding the host refuses (unknown handle, stale or blocked
    result, mismatched unit). The ``reasons`` of the returned value are the reasons for a card.
    """
    kind = binding.kind
    if kind is BindingKind.LITERAL:
        h = _handle(literals, binding.ref)
        unit, reasons = _number_unit(h.unit, field_def)
        reasons = (*reasons, *(r for r in h.reasons if r not in reasons))
        if h.from_quote:
            reasons = (*reasons, FROM_QUOTE)
        if h.kind == "number" and not _label_matches(field_def, aliases, h.left):
            reasons = (*reasons, LABEL_MISMATCH)
        return Resolved(
            value=_stored(h.value, unit, field_def),
            source=f"literal {h.id}",
            echo=h.echo(),
            reasons=tuple(dict.fromkeys(reasons)),
            handle=h.id,
        )
    if kind is BindingKind.RESULT:
        rec = _usable_result(results(binding.ref or ""), binding.ref or "")
        unit, _ = _number_unit(rec.unit, field_def)
        return Resolved(
            value=_stored(float(rec.value), unit, field_def),  # type: ignore[arg-type]
            source=f"result {rec.id}",
            echo=f"result {rec.id}",
            result_id=rec.id,
        )
    if kind is BindingKind.DERIVE:
        return _derive(binding, field_def, literals, results, live)
    if kind is BindingKind.PRESET:
        found = presets(binding.ref or "") if presets is not None else None
        if found is None:
            raise BindingError("unknown_preset", f"no preset {binding.ref!r} for this field")
        return Resolved(value=found, source=f"preset {binding.ref}", echo=f"preset {binding.ref}")
    if kind is BindingKind.DEFAULT:
        raise BindingError("no_default", f"{field_def.name} declares no default value")
    raise BindingError("binding_kind", f"a {kind.value} binding does not set a number")


def _handle(literals: LiteralSet, ref: str | None) -> Handle:
    h = literals.get(ref or "")
    if h is None:
        known = ", ".join(sorted(literals.ids())) or "none this turn"
        raise BindingError(
            "unknown_handle", f"{ref!r} is not a number from your words (handles: {known})"
        )
    return h


def _stored(value: float, unit: str, field_def: FieldDef) -> dict[str, Any]:
    if field_def.kind == "integer" and float(value) != int(value):
        raise BindingError("not_an_integer", f"{field_def.name} must be a whole number")
    return {"v": int(value) if field_def.kind == "integer" else float(value), "unit": unit}


def _derive(
    binding: Binding,
    field_def: FieldDef,
    literals: LiteralSet,
    results: ResultLookup,
    live: LiveLookup,
) -> Resolved:
    ref = binding.ref or ""
    by = _handle(literals, binding.by)
    source: dict[str, Any] | None
    source_id: str | None = None
    rec = results(ref)
    if rec is not None:
        rec = _usable_result(rec, ref)
        source = {"v": float(rec.value), "unit": rec.unit}  # type: ignore[arg-type]
        source_id = rec.id
        label = f"result {rec.id}"
    else:
        source = live(ref)
        label = "current value"
        if source is None or not isinstance(source.get("v"), int | float):
            raise BindingError(
                "unknown_source", f"{ref!r} is neither a current result nor a number in a lane"
            )
    base, base_unit = float(source["v"]), str(source.get("unit", ""))
    reasons: list[str] = []
    if by.from_quote:
        reasons.append(FROM_QUOTE)
    if binding.op == "scale":
        if by.unit == "percent":
            factor = by.value / 100.0
            reasons.append(PERCENT)
        elif by.unit:
            try:
                check(by.unit, "")
            except UnitError as exc:
                raise BindingError(
                    "unit_mismatch", f"a scale factor has no unit, got {by.unit}"
                ) from exc
            factor = convert(by.value, by.unit, "")
        else:
            factor = by.value
        value = base * factor
        what = f"{_fmt(factor)} x {label}"
    else:  # offset
        if by.unit == "percent":
            raise BindingError("unit_mismatch", "an offset takes a quantity, not a percent")
        if by.unit:
            try:
                value = base + convert(by.value, by.unit, base_unit)
            except UnitError as exc:
                raise BindingError("unit_mismatch", str(exc)) from exc
        else:
            value = base + by.value
            reasons.append(NO_UNIT)
        what = f"{label} + {by.text}"
    if RANGE in by.reasons or DECIMAL_COMMA in by.reasons:
        reasons.extend(r for r in by.reasons if r in (RANGE, DECIMAL_COMMA))
    unit, extra = _number_unit(base_unit, field_def)
    return Resolved(
        value=_stored(value, unit, field_def),
        source=f"derived from {ref}",
        echo=f"derived: {what}, host-computed",
        reasons=tuple(dict.fromkeys([*reasons, *extra])),
        handle=by.id,
        result_id=source_id,
    )


def card_reason_words(reasons: Iterable[str]) -> str:
    """Plain words for why a card is needed."""
    said = {
        NO_UNIT: "no unit was given",
        DECIMAL_COMMA: "the number uses a comma",
        RANGE: "it was written as a range",
        PERCENT: "it is a percent",
        FROM_QUOTE: "it comes from pasted or quoted text",
        LABEL_MISMATCH: "the field is not named next to the number",
    }
    return "; ".join(said.get(r, r) for r in reasons)
