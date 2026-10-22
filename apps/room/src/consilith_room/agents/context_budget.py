"""The context budget: how many tokens each part of a model call may use (section 9).

There is no tokenizer in the repo, so the estimate is characters divided by four. The same 24k
window applies to every provider, so the briefing and the history are the same size whichever
model a person picks.
"""

from __future__ import annotations

from dataclasses import dataclass

CHARS_PER_TOKEN = 4
WINDOW_TOKENS = 24_000


def estimate_tokens(text: str) -> int:
    """Tokens at chars/4, rounded up."""
    return -(-len(text) // CHARS_PER_TOKEN)


def cap_chars(text: str, tokens: int) -> str:
    """The text cut to at most ``tokens`` tokens, with a marker when it was cut."""
    limit = tokens * CHARS_PER_TOKEN
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)] + "..."


@dataclass(frozen=True)
class Allocation:
    """Default token allocation for a 24k window."""

    window: int = WINDOW_TOKENS
    rules: int = 1_500
    briefing: int = 2_500
    briefing_cap: int = 4_000
    primers: int = 6_000
    history: int = 4_000
    tool_result: int = 2_000
    message: int = 2_000
    # The visible reply. Raised from the old 800 for chat; the model table clamps it.
    output: int = 2_048


DEFAULT_ALLOCATION = Allocation()


class ContextBudget:
    """Estimates and fits the parts of one request. The drop order over budget is: old history,
    then primers, then twin detail, then results beyond the top rows (the last two are the
    briefing's job: ``briefing.fit_briefing``)."""

    def __init__(self, allocation: Allocation = DEFAULT_ALLOCATION) -> None:
        self.allocation = allocation

    def tokens(self, text: str) -> int:
        return estimate_tokens(text)

    def fits(self, text: str, tokens: int) -> bool:
        return estimate_tokens(text) <= tokens

    def cut(self, text: str, tokens: int) -> str:
        return cap_chars(text, tokens)

    def input_budget(self) -> int:
        """Tokens left for ``input`` once the rules and the reply are reserved."""
        a = self.allocation
        return a.window - a.rules - a.output

    def fit_history(self, lines: list[str]) -> list[str]:
        """Drop the oldest lines until the transcript fits the history allocation."""
        kept = list(lines)
        while kept and sum(estimate_tokens(x) + 1 for x in kept) > self.allocation.history:
            kept.pop(0)
        return kept
