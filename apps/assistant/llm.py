"""LLM query service — natural-language questions over the speaker/session data.

    answer = ask(organization, "Find all academics who taught a Python workshop at NICAR")

Architecture
------------
  QueryService ── picks the configured provider ── provider runs its own
  tool-call loop, calling `apps.assistant.tools` (read-only, privacy-scoped
  per organization) through the `execute` callback it is handed.

Providers implement `LLMProvider.answer(question, tools, execute, system)`.
Configure with settings.EVENT_PLANNER["LLM_PROVIDER"]:
  ""          (default) NullProvider → raises LLMNotConfigured
  "anthropic" Claude via the Anthropic SDK (providers/anthropic_provider.py;
              `uv add anthropic` or `uv sync --extra ai`, set ANTHROPIC_API_KEY)
Add another provider by implementing the protocol and registering it in
PROVIDERS. Answers must cite only what the tools returned (system prompt).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from django.conf import settings

from . import tools as query_tools

SYSTEM_PROMPT = (
    "You answer questions for staff of a nonprofit that runs journalism conferences, using only the "
    "search tools provided. They cover the organization's own sessions, other organizations' published "
    "sessions, and public speaker profiles. Use the tools to look things up, and don't state facts the "
    "tools didn't return. When a question needs judgment (for example, whether an affiliation is "
    "academic), say how you decided. If the tools find nothing, say so plainly. Keep answers short and "
    "list the sessions or speakers you relied on."
)


class LLMNotConfigured(RuntimeError):
    pass


@dataclass
class ToolCallLog:
    name: str
    arguments: dict
    result_count: int | None = None
    error: str = ""


@dataclass
class Answer:
    text: str
    tool_calls: list[ToolCallLog] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    stop_reason: str = ""


Executor = Callable[[str, dict], Any]


class LLMProvider(Protocol):
    name: str

    def answer(self, question: str, *, tools: list[query_tools.Tool], execute: Executor, system: str) -> Answer: ...


class NullProvider:
    name = "none"

    def answer(self, question, *, tools, execute, system):
        raise LLMNotConfigured('No LLM provider configured (set EVENT_PLANNER["LLM_PROVIDER"]).')


def _anthropic():
    from .providers.anthropic_provider import AnthropicProvider

    return AnthropicProvider()


PROVIDERS: dict[str, Callable[[], LLMProvider]] = {"": NullProvider, "none": NullProvider, "anthropic": _anthropic}


def get_provider(name: str | None = None) -> LLMProvider:
    name = settings.EVENT_PLANNER.get("LLM_PROVIDER", "") if name is None else name
    try:
        return PROVIDERS[name]()
    except KeyError:
        raise LLMNotConfigured(f"Unknown LLM provider {name!r}. Options: {', '.join(sorted(PROVIDERS))}") from None


class QueryService:
    def __init__(self, organization, provider: LLMProvider | None = None):
        self.organization = organization
        self.provider = provider or get_provider()
        self.calls: list[ToolCallLog] = []

    def execute(self, name: str, arguments: dict) -> str:
        """Run one tool call; returns JSON text for the model. Errors become
        text so the provider can report them back as an error result."""
        log = ToolCallLog(name=name, arguments=arguments)
        self.calls.append(log)
        try:
            result = query_tools.run_tool(self.organization, name, arguments)
        except KeyError:
            log.error = f"Unknown tool {name!r}"
            raise
        log.result_count = len(result) if isinstance(result, list) else None
        return json.dumps(result, ensure_ascii=False, default=str)

    def ask(self, question: str) -> Answer:
        answer = self.provider.answer(question, tools=query_tools.TOOLS, execute=self.execute, system=SYSTEM_PROMPT)
        answer.tool_calls = self.calls
        answer.provider = self.provider.name
        return answer


def ask(organization, question: str) -> Answer:
    return QueryService(organization).ask(question)
