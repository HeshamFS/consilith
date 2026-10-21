"""Compatibility facade: all scientific workflow implementations are extensions."""

from typing import Any

from .conductor import Conductor


class Studios:
    def __init__(self, conductor: Conductor) -> None:
        self.conductor = conductor

    def __getattr__(self, name: str) -> Any:
        return getattr(self.conductor, name)
