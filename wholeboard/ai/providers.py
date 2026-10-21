"""AI providers behind one small interface.

- AnthropicSession: Claude through the official Anthropic SDK (cloud).
- OpenAISession: any OpenAI-compatible chat server, which covers local models
  (Ollama, LM Studio, llama.cpp server, vLLM) and other clouds. The model must
  support tool (function) calling.

Each session keeps its history in the provider's own format and only ever
appends to it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# Claude models that accept the server-side refusal fallback ("default" routing).
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict
    error: str | None = None   # set when the arguments could not be parsed


@dataclass
class Turn:
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    stop: str = ""             # end | tool_use | max_tokens | refusal
    note: str = ""             # e.g. which model answered after a fallback
    usage: dict = field(default_factory=dict)


class AIError(RuntimeError):
    pass


# US$ per million tokens: input, output, cache read, cache write (5-minute cache: 1.25 x input).
CLAUDE_PRICES = {
    "claude-fable-5-1": (10, 50, 0.25, 12.5), "claude-fable-5": (10, 50, 1.0, 12.5),
    "claude-mythos-5-1": (10, 50, 0.25, 12.5), "claude-mythos-5": (10, 50, 1.0, 12.5),
    "claude-opus-5-5": (4, 20, 0.20, 5.0), "claude-opus-5": (5, 25, 0.50, 6.25),
    "claude-opus-4-8": (5, 25, 0.50, 6.25), "claude-opus-4-7": (5, 25, 0.50, 6.25),
    "claude-sonnet-5-5": (2, 10, 0.20, 2.5), "claude-sonnet-5": (2, 10, 0.20, 2.5),
    "claude-sonnet-4-6": (3, 15, 0.30, 3.75), "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "claude-haiku-4-5": (1, 5, 0.10, 1.25),
}


def turn_cost(model: str, usage: dict, cfg: dict) -> float | None:
    """The price of one turn in US$, or None when the model's price is unknown."""
    if cfg.get("provider") == "openai":
        pin, pout = float(cfg.get("openai_price_in") or 0), float(cfg.get("openai_price_out") or 0)
        if not pin and not pout:
            return 0.0 if _local(cfg) else None
        return (usage.get("input", 0) * pin + usage.get("output", 0) * pout) / 1e6
    base = re.sub(r"-\d{8}$", "", model or "")
    price = CLAUDE_PRICES.get(base)
    if not price:
        return None
    pin, pout, pread, pwrite = price
    if base == "claude-haiku-5-5" and usage.get("input", 0) + usage.get("cache_read", 0) > 100_000:
        pin, pout = 0.50, 2.50
    return (usage.get("input", 0) * pin + usage.get("output", 0) * pout + usage.get("cache_read", 0) * pread
            + usage.get("cache_write", 0) * pwrite) / 1e6


def _local(cfg: dict) -> bool:
    url = (cfg.get("openai_base_url") or "").lower()
    return any(h in url for h in ("localhost", "127.0.0.1", "0.0.0.0", "192.168.", "10.", ".local"))


def _validate(schema: dict, data: Any) -> str | None:
    """Minimal check of a tool input against its JSON schema (required keys and types)."""
    if not isinstance(data, dict):
        return "arguments must be a JSON object"
    for key in schema.get("required", []):
        if key not in data:
            return f"missing required argument {key!r}"
    types = {"string": str, "object": dict, "array": list, "integer": int,
             "number": (int, float), "boolean": bool}
    for key, sub in (schema.get("properties") or {}).items():
        if key in data and sub.get("type") in types and not isinstance(data[key], types[sub["type"]]):
            return f"argument {key!r} must be of type {sub['type']}"
    return None


class AnthropicSession:
    def __init__(self, cfg: dict, system: str, tools: list[dict]):
        import anthropic

        self._anthropic = anthropic
        key = cfg.get("anthropic_api_key") or None
        self.client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
        self.model = cfg.get("anthropic_model") or "claude-opus-5-5"
        self.effort = cfg.get("anthropic_effort") or "high"
        self.system = system
        self.schemas = {t["name"]: t["input_schema"] for t in tools}
        self.tools = [{**t, "eager_input_streaming": True} for t in tools]
        self.messages: list = []

    @property
    def label(self) -> str:
        return f"Claude ({self.model})"

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_tool_results(self, results: list[tuple[str, str | dict, bool]], extra_text: str | None = None) -> None:
        """A result is text, or {"text": ..., "images": [{"media_type", "data" (base64), "label"}]}."""
        content: list = []
        for tid, out, err in results:
            if isinstance(out, dict):
                blocks: list = [{"type": "text", "text": out.get("text", "")}]
                for im in out.get("images") or []:
                    blocks.append({"type": "text", "text": im.get("label", "image")})
                    blocks.append({"type": "image", "source": {"type": "base64", "media_type": im["media_type"],
                                                                "data": im["data"]}})
                out = blocks
            content.append({"type": "tool_result", "tool_use_id": tid, "content": out, "is_error": err})
        if extra_text:
            content.append({"type": "text", "text": extra_text})
        self.messages.append({"role": "user", "content": content})

    def step(self) -> Turn:
        a = self._anthropic
        kwargs: dict = dict(
            model=self.model, max_tokens=64000, system=self.system, tools=self.tools,
            messages=self.messages, thinking={"type": "adaptive"},
            output_config={"effort": self.effort}, cache_control={"type": "ephemeral"},
        )
        if self.model in _FALLBACK_MODELS:
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        try:
            with self.client.beta.messages.stream(**kwargs) as stream:
                msg = stream.get_final_message()
        except a.AuthenticationError as e:
            raise AIError("Claude rejected the API key. Check it in Settings.") from e
        except a.NotFoundError as e:
            raise AIError(f"Model {self.model!r} was not found: {e.message}") from e
        except a.RateLimitError as e:
            raise AIError("Claude rate limit reached; wait a moment and press Continue.") from e
        except a.BadRequestError as e:
            raise AIError(f"Claude rejected the request: {e.message}") from e
        except a.APIStatusError as e:
            raise AIError(f"Claude API error {e.status_code}: {e.message}") from e
        except a.APIConnectionError as e:
            raise AIError(f"Cannot reach the Claude API: {e}") from e

        # Append exactly what came back (thinking blocks included) so history stays valid.
        self.messages.append({"role": "assistant", "content": msg.content})
        u = msg.usage
        turn = Turn(usage={"input": u.input_tokens, "output": u.output_tokens,
                           "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
                           "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
                           "model": msg.model or self.model})
        if msg.model and msg.model != self.model:
            turn.note = f"answered by {msg.model} after a fallback"
        texts = []
        for block in msg.content:
            if block.type == "text":
                texts.append(block.text)
            elif block.type == "tool_use":
                inp = block.input
                if isinstance(inp, str):
                    try:
                        inp = json.loads(inp)
                    except json.JSONDecodeError:
                        inp = {}
                err = _validate(self.schemas.get(block.name, {}), inp)
                turn.calls.append(ToolCall(block.id, block.name, inp if isinstance(inp, dict) else {}, err))
        turn.text = "\n".join(texts).strip()
        if msg.stop_reason == "refusal":
            turn.stop = "refusal"
        elif msg.stop_reason == "max_tokens":
            turn.stop = "max_tokens"
            for c in turn.calls:  # a cut-off tool call cannot be trusted
                c.error = c.error or "the response was cut off before this call was complete"
        else:
            turn.stop = "tool_use" if turn.calls else "end"
        return turn


def openai_key(cfg: dict) -> str:
    """The key for an OpenAI-compatible server: Settings first, then the provider's usual variable."""
    import os
    if cfg.get("openai_api_key"):
        return cfg["openai_api_key"]
    url = (cfg.get("openai_base_url") or "").lower()
    env = "XAI_API_KEY" if "x.ai" in url else "OPENAI_API_KEY" if "openai.com" in url else None
    return (os.environ.get(env) if env else None) or "not-needed"


def list_models(cfg: dict) -> list[str]:
    """Model ids the configured provider offers this key."""
    if cfg.get("provider") == "anthropic":
        import anthropic
        key = cfg.get("anthropic_api_key") or None
        client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
        return [m.id for m in client.models.list(limit=100)]
    import openai
    client = openai.OpenAI(base_url=cfg.get("openai_base_url") or None, api_key=openai_key(cfg), timeout=30)
    return sorted(m.id for m in client.models.list())


class OpenAISession:
    def __init__(self, cfg: dict, system: str, tools: list[dict]):
        import openai

        self._openai = openai
        self.client = openai.OpenAI(base_url=cfg.get("openai_base_url") or None,
                                    api_key=openai_key(cfg), timeout=1800)
        self.model = cfg.get("openai_model") or "qwen3:32b"
        self.schemas = {t["name"]: t["input_schema"] for t in tools}
        self.tools = [{"type": "function", "function": {
            "name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
            for t in tools]
        self.messages: list = [{"role": "system", "content": system}]
        self.vision_failed = False

    @property
    def label(self) -> str:
        return f"{self.model} (OpenAI-compatible)"

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_tool_results(self, results: list[tuple[str, str | dict, bool]], extra_text: str | None = None) -> None:
        images: list = []
        for tid, content, err in results:
            if isinstance(content, dict):
                images += content.get("images") or []
                content = content.get("text", "")
            self.messages.append({"role": "tool", "tool_call_id": tid,
                                  "content": ("ERROR: " if err else "") + content})
        if images:
            # Tool messages carry text only, so the images follow as a user message.
            parts: list = [{"type": "text", "text": "Board renders for your review: "
                            + ", ".join(i.get("label", "image") for i in images)}]
            parts += [{"type": "image_url", "image_url": {"url": f"data:{i['media_type']};base64,{i['data']}"}}
                      for i in images]
            self.messages.append({"role": "user", "content": parts, "_images": True})
        if extra_text:
            self.messages.append({"role": "user", "content": extra_text})

    def _drop_images(self) -> bool:
        """Replace image messages with a note, for servers whose model cannot read images."""
        found = False
        for m in self.messages:
            if m.get("_images"):
                m.pop("_images")
                m["content"] = ("(The board renders could not be shown: this model does not accept images. "
                                "Skip report_review.)")
                found = True
        if found:
            self.vision_failed = True
        return found

    def _wire(self) -> list:
        return [{k: v for k, v in m.items() if not k.startswith("_")} for m in self.messages]

    def step(self) -> Turn:
        o = self._openai
        try:
            try:
                resp = self.client.chat.completions.create(
                    model=self.model, messages=self._wire(), tools=self.tools, tool_choice="auto")
            except (o.BadRequestError, o.InternalServerError, o.UnprocessableEntityError) as e:
                if not any(m.get("_images") for m in self.messages):
                    raise
                if not self._drop_images():
                    raise
                del e
                resp = self.client.chat.completions.create(
                    model=self.model, messages=self._wire(), tools=self.tools, tool_choice="auto")
        except o.AuthenticationError as e:
            raise AIError("The AI server rejected the API key. Check it in Settings.") from e
        except o.NotFoundError as e:
            raise AIError(f"Model {self.model!r} not found on the AI server.") from e
        except o.APIConnectionError as e:
            raise AIError(f"Cannot reach the AI server at {self.client.base_url}: {e}") from e
        except o.APIStatusError as e:
            raise AIError(f"AI server error {e.status_code}: {e.message}") from e
        choice = resp.choices[0]
        m = choice.message
        entry: dict = {"role": "assistant", "content": m.content or ""}
        turn = Turn(text=(m.content or "").strip())
        if resp.usage:
            cached = getattr(getattr(resp.usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
            turn.usage = {"input": (resp.usage.prompt_tokens or 0) - cached, "output": resp.usage.completion_tokens or 0,
                          "cache_read": cached, "model": self.model}
        if m.tool_calls:
            entry["tool_calls"] = [{"id": tc.id, "type": "function", "function": {
                "name": tc.function.name, "arguments": tc.function.arguments}} for tc in m.tool_calls]
            for tc in m.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    err = _validate(self.schemas.get(tc.function.name, {}), args)
                except json.JSONDecodeError as e:
                    args, err = {}, f"arguments were not valid JSON: {e}"
                turn.calls.append(ToolCall(tc.id, tc.function.name, args if isinstance(args, dict) else {}, err))
        self.messages.append(entry)
        if choice.finish_reason == "length":
            turn.stop = "max_tokens"
            for c in turn.calls:
                c.error = c.error or "the response was cut off before this call was complete"
        else:
            turn.stop = "tool_use" if turn.calls else "end"
        # Some local models print their reasoning in <think> tags; keep it out of the chat.
        if "<think>" in turn.text:
            import re
            turn.text = re.sub(r"<think>.*?</think>", "", turn.text, flags=re.S).strip()
        return turn


def make_session(ai_cfg: dict, system: str, tools: list[dict]):
    if ai_cfg.get("provider") == "openai":
        return OpenAISession(ai_cfg, system, tools)
    return AnthropicSession(ai_cfg, system, tools)
