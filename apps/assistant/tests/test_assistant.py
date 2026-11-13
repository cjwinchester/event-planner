import json
from types import SimpleNamespace as NS

import pytest

from apps.assistant import tools
from apps.assistant.llm import QueryService
from apps.assistant.providers.anthropic_provider import AnthropicProvider


@pytest.fixture
def sessions(world, make_session):
    from apps.events.models import Event

    ours = make_session("Python for reporters", speakers=[world.alice], session_type=world.hands_on)
    other = Event.objects.create(
        organization=world.other_org,
        name="Other 2027",
        slug="other-2027",
        start_date=world.event.start_date,
        end_date=world.event.end_date,
        timezone="UTC",
        publish_schedule=False,
    )
    make_session("Python secrets (unpublished)", event=other)
    return ours


@pytest.mark.django_db
def test_tools_hide_other_orgs_unpublished_sessions(world, sessions):
    titles = [r["title"] for r in tools.search_sessions(world.org, text="python")]
    assert titles == ["Python for reporters"]
    assert tools.search_sessions(world.other_org, text="python for") == []  # ours isn't published/ready


@pytest.mark.django_db
def test_speaker_tool_returns_public_fields_only(world, sessions):
    row = tools.search_speakers(world.org, name="Alice")[0]
    assert "email" not in json.dumps(row)


def _block(**kw):
    return NS(**kw)


class FakeClient:
    """Mimics client.beta.messages.create: one tool round, then an answer."""

    def __init__(self):
        self.calls = []
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return NS(
                stop_reason="tool_use",
                model=kwargs["model"],
                content=[_block(type="tool_use", id="tu1", name="search_sessions", input={"text": "python"})],
            )
        return NS(stop_reason="end_turn", model=kwargs["model"], content=[_block(type="text", text="One session.")])


@pytest.mark.django_db
def test_anthropic_provider_tool_loop(world, sessions):
    client = FakeClient()
    answer = QueryService(world.org, provider=AnthropicProvider(client=client)).ask("Python sessions?")
    assert answer.text == "One session." and answer.tool_calls[0].result_count == 1
    first = client.calls[0]
    assert first["model"] == "claude-opus-5-5" and all(t["strict"] for t in first["tools"])
    tool_result = client.calls[1]["messages"][-1]["content"][0]
    assert tool_result["tool_use_id"] == "tu1" and not tool_result["is_error"]
