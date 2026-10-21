"""Isolated scientific extension client; importing this package imports no science."""

from .client import load
from .codec import RemoteFailure

__all__ = ["RemoteFailure", "load"]
