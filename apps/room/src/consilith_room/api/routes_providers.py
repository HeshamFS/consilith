"""The model provider registry, as the connect panel and the drawer read it."""

from typing import Any

from consilith_contracts.providers import PROVIDERS
from fastapi import APIRouter


def providers_router() -> APIRouter:
    router = APIRouter()

    @router.get("/providers")
    def providers() -> dict[str, Any]:
        """Id, label, auth, needsModel, defaultModel and dataTerms of every provider. Static
        registry data: no key, no account and nothing about a person."""
        return {"providers": [p.wire() for p in PROVIDERS.values()]}

    return router
