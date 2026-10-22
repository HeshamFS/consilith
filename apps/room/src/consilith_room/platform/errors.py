"""One error family for every model call, whatever the provider.

An adapter turns an SDK or transport failure into a ``ModelError`` with a ``code`` from a small
fixed vocabulary, a ``user_message`` that is safe to show, and a ``fix_action`` the interface can
offer. Chat, the composer and dispatch then show the same words for the same failure.

Only fixed, known text may go in a message. Never put a key, a prompt, a response body or an SDK
exception message in one: the exception type and a status code are all that may cross.

This module sits in ``platform`` so that the domain, the agents and the API can all raise and
catch these errors (``platform`` cannot import ``agents``).
"""

from __future__ import annotations

from typing import ClassVar

# Every code a model failure can carry. ``NotConfigured`` may also carry a more specific code
# from the ChatGPT helper (``not_signed_in``, ``subscription_sharing_usage_limit_exceeded``).
MODEL_ERROR_CODES = (
    "auth",
    "rate_limit",
    "quota",
    "overloaded",
    "network",
    "timeout",
    "context_length",
    "bad_output",
    "refused",
    "truncated",
    "empty",
    "cancelled",
    "not_configured",
)


class ModelError(RuntimeError):
    """A model call failed. ``str(error)`` is the message, which is already safe to show.

    ``code`` is what callers branch on. ``user_message`` is what the person reads (it defaults to
    the message). ``fix_action`` names what the interface can offer, for example ``reconnect``,
    ``reenter_key``, ``choose_model`` or ``retry``; None when nothing helps. ``retry_after_s``
    is how long a provider asked us to wait, when it said.
    """

    # The code of a subclass; ``ModelError`` itself is unclassified.
    default_code: ClassVar[str] = "model_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        user_message: str | None = None,
        fix_action: str | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code: str = code or self.default_code
        self.user_message: str = user_message or message
        self.fix_action: str | None = fix_action
        self.retry_after_s: float | None = retry_after_s


class AuthError(ModelError):
    """The provider rejected the credential."""

    default_code = "auth"


class RateLimitError(ModelError):
    """Too many requests; the provider may say when to try again."""

    default_code = "rate_limit"


class QuotaError(ModelError):
    """The account or plan has no allowance left. Waiting a few seconds will not help."""

    default_code = "quota"


class OverloadedError(ModelError):
    """The provider is busy and asked us to try again."""

    default_code = "overloaded"


class NetworkError(ModelError):
    """The provider could not be reached."""

    default_code = "network"


class ModelTimeout(ModelError):
    """The call ran past its deadline."""

    default_code = "timeout"


class ContextLengthError(ModelError):
    """The request was larger than the model's window."""

    default_code = "context_length"


class BadOutputError(ModelError):
    """The reply could not be used (no valid envelope after the repairs)."""

    default_code = "bad_output"


class RefusedError(ModelError):
    """The model refused or the provider filtered the reply. Never shown as chat text."""

    default_code = "refused"


class TruncatedError(ModelError):
    """The reply was cut off at the output limit."""

    default_code = "truncated"


class EmptyReplyError(ModelError):
    """The reply was empty, also after the one retry."""

    default_code = "empty"


class ModelCancelled(ModelError):
    """The call was cancelled before it finished."""

    default_code = "cancelled"


class NotConfigured(ModelError):
    """The provider is declared but cannot run here (no key, not signed in, or not built yet).

    ``NotConfigured(message, *, code=None)`` keeps working: ``code`` is a more specific reason
    when the caller has one, and ``not_configured`` otherwise.
    """

    default_code = "not_configured"


_BY_CODE: dict[str, type[ModelError]] = {
    cls.default_code: cls
    for cls in (
        AuthError,
        RateLimitError,
        QuotaError,
        OverloadedError,
        NetworkError,
        ModelTimeout,
        ContextLengthError,
        BadOutputError,
        RefusedError,
        TruncatedError,
        EmptyReplyError,
        ModelCancelled,
        NotConfigured,
    )
}


def error_for_code(
    code: str,
    message: str,
    *,
    user_message: str | None = None,
    fix_action: str | None = None,
    retry_after_s: float | None = None,
) -> ModelError:
    """The ``ModelError`` subclass for one of ``MODEL_ERROR_CODES``. ``ValueError`` otherwise."""
    cls = _BY_CODE.get(code)
    if cls is None:
        raise ValueError(f"unknown model error code {code!r}; use one of: {', '.join(_BY_CODE)}")
    return cls(
        message, user_message=user_message, fix_action=fix_action, retry_after_s=retry_after_s
    )
