"""The prose guard: no number leaves the owner's context unless the server wrote it.

One ``prose_guard()`` covers every model-authored string that leaves the owner's context: chat
text, ``reason`` fields, warning messages, proposal reasons, lesson text, thread titles, block
titles, Note text, peer text and card labels (BUILD_SPEC section 6, "Numbers keep provenance").

It NFKC-normalises the string and rejects Unicode categories Nd, No and Nl (fullwidth,
Arabic-Indic, superscripts, vulgar fractions), CJK numerals and English number words. It allows
only the placeholders ``{{result:ID}}``, ``{{lit:N}}``, ``{{formula:id}}`` and ``{{paper:ID}}``
(a paper this turn's research search returned), identifier tokens
that match known ids, and the platform terms ``tier 0``, ``tier 1`` and ``tier 2``. There is no
free allowance for years or formulas. A numbered-list marker at the start of a line is allowed
in the owner's own chat text only.

On failure the caller retries once with feedback. For chat text it then calls ``sanitize_chat``,
which replaces each offending sentence with a deterministic summary built from the ids it cited.
After the guard, ``substitute`` writes the values of ``{{result:ID}}`` and ``{{lit:N}}``, and the
citation line of each ``{{paper:ID}}``.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field

_DIGIT_CATEGORIES = frozenset({"Nd", "No", "Nl"})

# CJK numerals (they are category Lo, so the category test alone would miss them).
_CJK_NUMERALS = frozenset("〇零一二三四五六七八九十百千万億兆壱弐参拾壹贰叁肆伍陆柒捌玖貳參陸")

_WORDS = (  # noqa: SIM905
    "zero two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety "
    "hundred hundreds thousand thousands million millions billion billions half halves halve "
    "halved halving double doubles doubled doubling triple tripled quadruple quadrupled twice "
    "thrice dozen dozens quarter quarters"
).split()
# "one" and "once" are excluded on purpose: a documented gap (they are ordinary English).
_WORD_RE = re.compile(r"\b(?:" + "|".join(sorted(_WORDS, key=len, reverse=True)) + r")\b", re.I)

_PLACEHOLDER = re.compile(r"\{\{\s*(result|lit|formula|paper)\s*:\s*([^{}\s]{1,200})\s*\}\}")
_ANY_PLACEHOLDER = re.compile(r"\{\{[^{}]*\}\}")
_TIER = re.compile(r"\btier [012]\b", re.I)
_TOKEN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:/#@-]*")
_LIST_MARKER = re.compile(r"(?m)^[ \t]*\d{1,3}[.)][ \t]+")
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]*|\n+")

_MARK = "\x00"  # stands for a removed, allowed span; it is neither a digit nor a word


@dataclass(frozen=True)
class GuardContext:
    """What the guard may allow. All ids come from the server, never from the model."""

    # Identifier tokens that may appear verbatim: result, lane, field, tool, alias, plugin and
    # case ids. A token that matches one is removed before the number scan.
    ids: frozenset[str] = frozenset()
    result_ids: frozenset[str] = frozenset()
    lit_ids: frozenset[str] = frozenset()  # "n1".."nk", or their numbers "1".."k"
    formula_ids: frozenset[str] = frozenset()
    paper_ids: frozenset[str] = frozenset()  # papers this turn's research search returned
    owner_chat: bool = False  # numbered-list markers are allowed at line starts

    def with_(self, **changes: object) -> GuardContext:
        data = {
            "ids": self.ids,
            "result_ids": self.result_ids,
            "lit_ids": self.lit_ids,
            "formula_ids": self.formula_ids,
            "paper_ids": self.paper_ids,
            "owner_chat": self.owner_chat,
        }
        data.update(changes)
        return GuardContext(**data)  # type: ignore[arg-type]


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    text: str  # the NFKC-normalised text
    problems: tuple[str, ...] = field(default_factory=tuple[str, ...])

    def feedback(self) -> str:
        """Plain words the model gets for its one retry."""
        return (
            "Your text was rejected: " + "; ".join(self.problems) + ". Write no digits, number "
            "words or numeric symbols. Cite a number with {{result:ID}} or {{lit:N}}, and a paper "
            "from your research search with {{paper:ID}}."
        )


def normalise(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def _is_digit(ch: str) -> bool:
    return unicodedata.category(ch) in _DIGIT_CATEGORIES or ch in _CJK_NUMERALS


def _lit_known(raw: str, ctx: GuardContext) -> bool:
    raw = raw.strip()
    return raw in ctx.lit_ids or (raw.isdigit() and f"n{raw}" in ctx.lit_ids)


def check_prose(text: str, ctx: GuardContext) -> list[str]:
    """Every reason the text may not leave the owner's context (empty when it may)."""
    problems: list[str] = []
    # NFKC turns a Roman numeral or a circled digit into Latin letters or ASCII, so the Unicode
    # number categories are also checked on the text as it was written.
    if any(ord(ch) > 127 and unicodedata.category(ch) in _DIGIT_CATEGORIES for ch in text):
        problems.append("it contains a digit or numeral")
    work = normalise(text)

    def take(m: re.Match[str]) -> str:
        kind, ident = m.group(1), m.group(2)
        known = {
            "result": ident in ctx.result_ids,
            "lit": _lit_known(ident, ctx),
            "formula": ident in ctx.formula_ids,
            "paper": ident in ctx.paper_ids,
        }[kind]
        if not known:
            problems.append(f"{{{{{kind}:{ident}}}}} is not a known placeholder")
        return _MARK

    work = _PLACEHOLDER.sub(take, work)
    if _ANY_PLACEHOLDER.search(work):
        problems.append(
            "only {{result:ID}}, {{lit:N}}, {{formula:id}} and {{paper:ID}} placeholders are "
            "allowed"
        )
        work = _ANY_PLACEHOLDER.sub(_MARK, work)
    work = _TIER.sub(_MARK, work)
    if ctx.owner_chat:
        work = _LIST_MARKER.sub("", work)

    def token(m: re.Match[str]) -> str:
        tok = m.group(0)
        return _MARK if tok.rstrip(".:/#@-") in ctx.ids or tok in ctx.ids else tok

    work = _TOKEN.sub(token, work)
    if any(_is_digit(ch) for ch in work):
        problems.append("it contains a digit or numeral")
    hit = _WORD_RE.search(work)
    if hit is not None:
        problems.append(f"it contains the number word {hit.group(0).lower()!r}")
    return problems


def prose_guard(text: str, ctx: GuardContext) -> GuardResult:
    """Check one model-authored string. ``ok`` text is the normalised text."""
    problems = check_prose(text, ctx)
    return GuardResult(ok=not problems, text=normalise(text), problems=tuple(problems))


def sanitize_chat(text: str, ctx: GuardContext) -> str:
    """The deterministic fallback for chat text after the retry also failed.

    Each sentence that fails the guard is replaced by a summary built from the result ids it
    cited (``Cited: {{result:a}}.``) or, when it cited none, by a fixed sentence. Sentences that
    pass are kept as they were.
    """
    out: list[str] = []
    for part in _SENTENCE.findall(text):  # checked as written: NFKC would hide a Roman numeral
        if part.strip() == "" or not check_prose(part, ctx):
            out.append(normalise(part))
            continue
        cited = [
            m.group(2)
            for m in _PLACEHOLDER.finditer(part)
            if m.group(1) == "result" and m.group(2) in ctx.result_ids
        ]
        if cited:
            refs = ", ".join(f"{{{{result:{rid}}}}}" for rid in dict.fromkeys(cited))
            out.append(f"Cited: {refs}.")
        else:
            out.append("(A sentence with a number was removed; ask me and I will cite the result.)")
    return "".join(out).strip()


def substitute(
    text: str,
    *,
    results: Callable[[str], str | None] | Mapping[str, str],
    literals: Mapping[str, str],
    papers: Mapping[str, str] | None = None,
) -> str:
    """Write the server's value for every ``{{result:ID}}`` and ``{{lit:N}}``, and the citation
    line of every ``{{paper:ID}}`` (``papers``: id to the line the research service gave).

    ``results`` maps a result id to its display text (value, unit and stamp), or is a callable
    that does. An id with no display is left in place, so the gap is visible. ``{{formula:id}}``
    is left for the surface renderer.
    """
    lookup = results if callable(results) else results.get

    def fill(m: re.Match[str]) -> str:
        kind, ident = m.group(1), m.group(2)
        if kind == "result":
            shown = lookup(ident)
            return shown if shown is not None else m.group(0)
        if kind == "lit":
            key = ident if ident in literals else f"n{ident}"
            return literals.get(key, m.group(0))
        if kind == "paper" and papers:
            return papers.get(ident, m.group(0))
        return m.group(0)

    return _PLACEHOLDER.sub(fill, text)


def split_chunks(text: str, size: int = 80) -> list[str]:
    """The deterministic chunker for releasing held text: breaks at spaces where it can."""
    out: list[str] = []
    rest = text
    while len(rest) > size:
        cut = rest.rfind(" ", 0, size)
        cut = cut + 1 if cut > 0 else size
        out.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        out.append(rest)
    return out


def ids_in(text: str) -> Collection[str]:
    """The ``{{result:ID}}`` ids a text cites, in order."""
    return [m.group(2) for m in _PLACEHOLDER.finditer(normalise(text)) if m.group(1) == "result"]
