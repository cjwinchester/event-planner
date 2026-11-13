"""Claude provider (Anthropic Python SDK) — skeleton, not yet exercised in CI.

Install: `uv sync --extra ai` (or `uv add anthropic`); credentials come from
ANTHROPIC_API_KEY (or an `ant auth login` profile).

Choices, per the current Claude API docs:
  * model `claude-opus-5-5`, effort set explicitly (this model defaults to
    "medium"; lookups over a database don't need more). Thinking is always
    on for this model — no `thinking` parameter is sent.
  * forced tool_choice is rejected on this model, so tools are offered with
    tool_choice auto, `strict: true` schemas, and the system prompt steers.
  * server-side refusal fallback is enabled (`fallbacks: "default"`, beta
    `server-side-fallback-2026-07-01`); a final `refusal` stop reason means
    the whole chain declined, and we return a plain message.
  * manual tool loop: every tool_result goes back in ONE user message;
    failed tools return `is_error: true`; `pause_turn` is re-sent.
"""

from __future__ import annotations

from apps.assistant.llm import Answer

MODEL = "claude-opus-5-5"
MAX_TOOL_ROUNDS = 8


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, client=None, model: str = MODEL, effort: str = "medium"):
        if client is None:
            import anthropic  # optional dependency (extra "ai")

            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.effort = effort

    def _tool_defs(self, tools):
        return [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema, "strict": True}
            for t in tools
        ]

    def answer(self, question, *, tools, execute, system):
        messages = [{"role": "user", "content": question}]
        tool_defs = self._tool_defs(tools)
        response = None
        for _ in range(MAX_TOOL_ROUNDS):
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                tools=tool_defs,
                messages=messages,
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            if response.stop_reason == "refusal":
                return Answer(
                    text="The model declined to answer this question.", model=response.model, stop_reason="refusal"
                )
            if response.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": response.content})
                continue
            if response.stop_reason != "tool_use":
                break  # end_turn / max_tokens / stop_sequence

            messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                try:
                    content, is_error = execute(block.name, block.input), False
                except Exception as exc:  # report tool failures back to the model
                    content, is_error = f"Error: {exc}", True
                results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": is_error}
                )
            messages.append({"role": "user", "content": results})
        else:
            return Answer(
                text="Stopped: too many tool calls for one question.", model=self.model, stop_reason="tool_limit"
            )

        text = "".join(b.text for b in response.content if b.type == "text")
        return Answer(text=text, model=response.model, stop_reason=response.stop_reason or "")
