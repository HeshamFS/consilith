"""Optional data/callable illustration providers, installed outside the room core."""

from typing import Any

from consilith_contracts.extensions import all_extensions


def _providers() -> dict[str, dict[str, Any]]:
    return {
        key: value
        for key, value in all_extensions("illustrative").items()
        if isinstance(value, dict)
    }


def _entries() -> dict[str, dict[str, Any]]:
    return {
        key: entry
        for provider in _providers().values()
        for key, entry in zip(
            provider.get("keys", []), provider.get("data", {}).get("entries", []), strict=True
        )
    }


def for_case(case: str) -> dict[str, Any]:
    return {
        key.split(".", 1)[-1]: quantity(key)
        for key, entry in _entries().items()
        if entry.get("case") == case
    }


def resolves(pid: str) -> bool:
    return any(entry.get("id") == pid for entry in _entries().values())


def wire() -> dict[str, Any]:
    documents = [provider["data"] for provider in _providers().values() if "data" in provider]
    versions = list(dict.fromkeys(document["version"] for document in documents))
    entries = [entry for document in documents for entry in document["entries"]]
    return {
        "version": versions[0] if len(versions) == 1 else "composite" if versions else "none",
        "entries": entries,
    }


def quantity(key: str) -> dict[str, Any]:
    entry = _entries().get(key)
    if entry is None:
        raise LookupError(f"No installed illustration provider declares {key!r}")
    return {name: value for name, value in entry.items() if name not in {"id", "case"}}


def provenance_id(key: str) -> str:
    return str(quantity(key)["provenanceId"])


def __getattr__(name: str) -> Any:
    if name == "ENTRIES":
        return _entries()
    raise AttributeError(name)
