"""Plug-and-play reports.

A report is a class with a slug, a title, the scope it runs on, and a
`compute()` returning JSON-serializable data (ready for a chart or map).
Register it with @register and it appears in the API at
/api/v1/reports/ and /api/v1/reports/<slug>/?<scope>=<id>.

Reports receive already-authorized objects (the API resolves and checks
`series`/`event`/`organization` before calling compute).
"""

from __future__ import annotations

from enum import StrEnum


class ReportScope(StrEnum):
    SERIES = "series"
    EVENT = "event"
    ORGANIZATION = "organization"


class Report:
    slug: str = ""
    title: str = ""
    description: str = ""
    scope: ReportScope = ReportScope.EVENT

    def compute(self, obj, **params) -> dict:
        raise NotImplementedError


_REGISTRY: dict[str, Report] = {}


def register(cls):
    instance = cls()
    if instance.slug in _REGISTRY:
        raise ValueError(f"Report {instance.slug!r} already registered")
    _REGISTRY[instance.slug] = instance
    return cls


def get_report(slug: str) -> Report:
    return _REGISTRY[slug]


def all_reports() -> dict[str, Report]:
    return dict(_REGISTRY)
