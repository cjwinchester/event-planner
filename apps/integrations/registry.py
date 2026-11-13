"""Connector registry (pattern ported from ire-data-dashboard's
connectors/registry.py, extended with capabilities + per-org enablement).

    @register
    class MyConnector(BaseConnector): ...

    get_connector("membersuite")
    connectors_with(Capability.REGISTRATION_SYNC)
"""

from __future__ import annotations

from .base import CAPABILITY_PROTOCOLS, BaseConnector, Capability

_REGISTRY: dict[str, BaseConnector] = {}


def register(cls: type[BaseConnector]) -> type[BaseConnector]:
    instance = cls()
    if not instance.slug:
        raise ValueError(f"{cls.__name__} has no slug")
    if instance.slug in _REGISTRY:
        raise ValueError(f"A connector named {instance.slug!r} is already registered")
    for cap in instance.capabilities:
        protocol = CAPABILITY_PROTOCOLS[cap]
        if not isinstance(instance, protocol):
            raise TypeError(f"{cls.__name__} declares {cap} but doesn't implement {protocol.__name__}")
    _REGISTRY[instance.slug] = instance
    return cls


def get_connector(slug: str) -> BaseConnector:
    try:
        return _REGISTRY[slug]
    except KeyError:
        available = ", ".join(sorted(_REGISTRY)) or "(none registered)"
        raise KeyError(f"No connector named {slug!r}. Available: {available}") from None


def all_connectors() -> dict[str, BaseConnector]:
    return dict(_REGISTRY)


def connectors_with(capability: Capability) -> list[BaseConnector]:
    return [c for c in _REGISTRY.values() if capability in c.capabilities]


def choices() -> list[tuple[str, str]]:
    return sorted((slug, c.display_name) for slug, c in _REGISTRY.items())
