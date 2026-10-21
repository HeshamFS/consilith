"""Seat colours and generic access to installed role defaults.

Nobody is seeded: people are real accounts (``domain/accounts.py``).
"""

from __future__ import annotations

from consilith_contracts import AgentConfig, Autonomy

Role = str

# Seat colours as paper / stage pairs. A person picks a name; the room document carries both.
COLORS: dict[str, tuple[str, str]] = {
    "blue": ("#2A50CC", "#7C9BFF"),
    "magenta": ("#C4296A", "#FF7FB5"),
    "teal": ("#0D8089", "#3EC1B4"),
    "violet": ("#5B2EA6", "#B497F0"),
    "amber": ("#B26A00", "#F2B04A"),
    "olive": ("#5E6B1F", "#B7C45E"),
}


def default_config(role: Role) -> AgentConfig:
    from consilith_contracts.extensions import optional_extension

    value = optional_extension("roles", role)
    if value is None:
        return AgentConfig(autonomy=Autonomy.SUGGEST)
    return AgentConfig.model_validate(value() if callable(value) else value).model_copy(deep=True)
