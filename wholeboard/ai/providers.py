"""AI providers behind one small interface.

- AnthropicSession: Claude through the official Anthropic SDK (cloud).
- OpenAISession: any OpenAI-compatible chat server, which covers local models
  (Ollama, LM Studio, llama.cpp server, vLLM) and other clouds. The model must
  support tool (function) calling.

Each session keeps its history in the provider's own format and only ever
appends to it, except that OpenAISession shortens its older turns when the
prompt nears the model's context window (see OpenAISession.compact).
"""

from __future__ import annotations

import ipaddress
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
    notice: str = ""           # shown to the user in the chat, e.g. that older steps were shortened
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


def unreachable(url: str, e: Exception) -> str:
    """Why the AI server could not be reached, told apart by the error under the client's "Connection error"."""
    causes, x = [], e
    while x is not None and len(causes) < 8:
        causes.append(type(x).__name__)
        x = x.__cause__ or x.__context__
    if "ConnectionRefusedError" in causes or "ConnectError" in causes:
        return f"Nothing is answering at {url}. Start the AI server, or check its address in Settings."
    if "RemoteProtocolError" in causes or "ReadError" in causes:
        return (f"The AI server at {url} dropped the connection before it replied. Something between WholeBoard and "
                "the model, such as a proxy or port forwarder, may be giving up on slow replies: try the model "
                "server's own port.")
    if "APITimeoutError" in causes:
        return f"The AI server at {url} took too long to reply."
    return f"Cannot reach the AI server at {url}: {e}"


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
        if not self.tools:
            kwargs.pop("tools")
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


# Where local OpenAI-compatible servers listen by default, on the computer running WholeBoard.
LOCAL_SERVERS = [
    ("Ollama", 11434), ("LM Studio", 1234), ("llama.cpp or Strata", 8080), ("vLLM", 8000), ("Jan", 1337),
    ("KoboldCpp", 5001), ("text-generation-webui", 5000), ("GPT4All", 4891),
    # Strata's setup serves on 8080; `strata launch` defaults to 8095, and a second install often goes on 8090
    ("Strata", 8095), ("Strata", 8090),
]
# owned_by values that name the server better than its port does (8080 is shared by several)
_OWNERS = {"llamacpp": "llama.cpp", "vllm": "vLLM", "localai": "LocalAI", "koboldcpp": "KoboldCpp"}


def _get_json(url: str, body: dict | None = None, timeout: float = 2.0) -> Any:
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _gb(n: float | None) -> str | None:
    return f"{n / 1e9:.1f} GB" if n else None


def _ctx(n: int | None) -> str | None:
    return f"{n // 1000}k context" if n else None


def _probe(name: str, base: str) -> list[dict]:
    """The chat models one server offers, or [] if nothing answers there."""
    root = base[:-3] if base.endswith("/v1") else base
    models: list[dict] = []
    if name == "Ollama":
        try:
            tags = _get_json(root + "/api/tags")["models"]
        except Exception:  # noqa: BLE001 - not Ollama after all; try the OpenAI route below
            tags = None
        if tags is not None:
            for t in tags[:60]:
                d = t.get("details") or {}
                try:
                    caps = _get_json(root + "/api/show", {"model": t["name"]}).get("capabilities")
                except Exception:  # noqa: BLE001
                    caps = None
                if caps and "completion" not in caps:
                    continue                                  # embedding-only model
                models.append({"model": t["name"], "tools": ("tools" in caps) if caps else None,
                               "detail": " · ".join(x for x in (d.get("parameter_size"), d.get("quantization_level"),
                                                                 _gb(t.get("size"))) if x)})
            return [{"server": name, "base_url": base, **m} for m in models]
    if name == "LM Studio":
        try:
            for m in _get_json(root + "/api/v0/models")["data"]:
                if m.get("type") == "embeddings":
                    continue
                caps = m.get("capabilities")
                models.append({"model": m["id"], "tools": ("tool_use" in caps) if caps is not None else None,
                               "detail": " · ".join(x for x in (m.get("quantization"), _ctx(m.get("max_context_length")),
                                                                 m.get("state")) if x)})
            return [{"server": name, "base_url": base, **m} for m in models]
        except Exception:  # noqa: BLE001 - older LM Studio: plain OpenAI route
            pass
    try:
        data = _get_json(base + "/models")["data"]
    except Exception:  # noqa: BLE001 - nothing listening, or not an OpenAI-compatible server
        return []
    # Name the server only when it says what it is: a port alone can be anything (a proxy, say).
    where, tools = base.split("//", 1)[-1].removesuffix("/v1"), None
    def strata() -> bool:
        try:
            return str(_get_json(root + "/health").get("service", "")).lower() == "strata"
        except Exception:  # noqa: BLE001
            return False
    try:
        # llama.cpp's /props; Strata (stratallm.org) serves the same shape and names itself in build_info,
        # when its engine reports a version
        props = _get_json(root + "/props")
        if "default_generation_settings" in props:
            build = str(props.get("build_info") or "")
            where = build if build.lower().startswith("strata") else "Strata" if strata() else "llama.cpp"
            template, caps = str(props.get("chat_template") or ""), props.get("chat_template_caps")
            if isinstance(caps, dict) and "supports_tool_calls" in caps:
                tools = bool(caps["supports_tool_calls"])
                # Strata checks only for Qwen's <tool_call> format, so a DeepSeek V4.1 template (DSML calls,
                # which Strata does parse) reads as no tools; the template taking tools is the better sign there
                if not tools and where.startswith("Strata"):
                    tools = "tools" in template
            elif template:                              # a template that never mentions tools cannot take them
                tools = "tools" in template
    except Exception:  # noqa: BLE001 - not llama.cpp; Strata still answers /health
        if strata():
            where = "Strata"
    for m in data:
        if re.search(r"embed", m.get("id", ""), re.I):
            continue
        status = m.get("status")
        arch = m.get("architecture") or {}
        if "text" not in (arch.get("output_modalities") or ["text"]):
            continue                                      # an image generator (Strata lists one beside the chat model)
        detail = [_ctx((m.get("meta") or {}).get("n_ctx")),
                  "reads images" if "image" in (arch.get("input_modalities") or []) else None,
                  status.get("value") if isinstance(status, dict) else None]
        models.append({"server": _OWNERS.get(str(m.get("owned_by", "")).lower(), where), "base_url": base,
                       "model": m["id"], "tools": tools, "detail": " · ".join(x for x in detail if x)})
    return models


def served_context(base: str, model: str) -> int | None:
    """The context window, in tokens, the server at base runs model with, when it says."""
    root = base.rstrip("/").removesuffix("/v1")
    try:                                      # llama.cpp and Strata (meta.n_ctx), vLLM (max_model_len)
        for m in _get_json(base.rstrip("/") + "/models")["data"]:
            if m.get("id") == model:
                n = (m.get("meta") or {}).get("n_ctx") or m.get("max_model_len") or m.get("context_length")
                if n:
                    return int(n)
    except Exception:  # noqa: BLE001
        pass
    for url, keys in ((root + "/props", ("default_generation_settings", "n_ctx")),          # llama.cpp
                      (f"{root}/api/v0/models/{model}", ("loaded_context_length",)),      # LM Studio
                      (f"{root}/api/v0/models/{model}", ("max_context_length",))):
        try:
            v = _get_json(url)
            for k in keys:
                v = v[k]
            return int(v)
        except Exception:  # noqa: BLE001
            continue
    return None


def find_local_models(extra_url: str | None = None) -> dict:
    """Look for local model servers on their usual ports (and at extra_url) and list their chat models."""
    from concurrent.futures import ThreadPoolExecutor
    targets = [(n, f"http://localhost:{port}/v1") for n, port in LOCAL_SERVERS]
    extra = (extra_url or "").strip().rstrip("/")
    if extra.startswith("http://") and extra not in {b for _, b in targets}:
        targets.insert(0, ("Ollama" if ":11434" in extra else "LM Studio" if ":1234" in extra else "Server", extra))
    with ThreadPoolExecutor(len(targets)) as pool:
        found = list(pool.map(lambda t: _probe(*t), targets))
    return {"models": [m for ms in found for m in ms], "checked": [b for _, b in targets]}


# ---------------------------------------------------------------- model servers on the LAN
# The private IPv4 ranges (RFC 1918). Only addresses in these are scanned, so the search stays on the LAN.
PRIVATE_V4 = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
LAN_MAX_HOSTS = 1024          # a /22 takes about ten seconds; a bigger subnet is narrowed to the /24 around us


def lan_addresses() -> list[str]:
    """This computer's own IPv4 addresses in the private ranges."""
    import socket
    found: set[str] = set()
    for far in ("10.255.255.254", "172.31.255.254", "192.168.255.254"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((far, 9))           # UDP connect sends nothing; the OS just picks the outgoing address
                found.add(s.getsockname()[0])
        except OSError:
            pass
    try:
        found.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    return sorted(a for a in found if any(ipaddress.ip_address(a) in n for n in PRIVATE_V4))


def _prefix_lengths() -> dict[str, int]:
    """Each local IPv4 address's subnet prefix length, from `ip` (Linux) or `ifconfig` (macOS)."""
    import subprocess
    for cmd, pat in ((["ip", "-4", "-o", "addr", "show"], r"inet (\d+\.\d+\.\d+\.\d+)/(\d+)"),
                     (["ifconfig"], r"inet (\d+\.\d+\.\d+\.\d+) netmask (0x[0-9a-f]{8})")):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        found = {a: int(p) if p.isdigit() else bin(int(p, 16)).count("1") for a, p in re.findall(pat, out)}
        if found:
            return found
    return {}


def lan_networks() -> list[dict]:
    """The subnet this computer is on, for each of its LAN addresses (a /24 where the size is unknown)."""
    prefixes = _prefix_lengths()
    nets: dict[ipaddress.IPv4Network, dict] = {}
    for a in lan_addresses():
        prefix = prefixes.get(a, 24)
        net = ipaddress.ip_network(f"{a}/{prefix}", strict=False)
        whole = None
        if net.num_addresses > LAN_MAX_HOSTS:
            whole, net = str(net), ipaddress.ip_network(f"{a}/24", strict=False)
        nets.setdefault(net, {"network": net, "whole": whole})
    return [nets[n] for n in sorted(nets)]


def _socket_budget() -> int:
    """How many connections to try at once: half the open-file limit (macOS allows only 256), at most 512."""
    try:
        import resource
        return max(32, min(512, resource.getrlimit(resource.RLIMIT_NOFILE)[0] // 2))
    except (ImportError, OSError, ValueError):   # Windows has no such limit
        return 512


async def _open_ports(hosts: list[str], ports: list[int], timeout: float = 0.6) -> list[tuple[str, int]]:
    """The (host, port) pairs that accept a TCP connection."""
    import asyncio
    sem = asyncio.Semaphore(_socket_budget())

    async def one(host: str, port: int):
        async with sem:
            try:
                _, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
            except (OSError, asyncio.TimeoutError):
                return None
            w.close()
            return host, port
    return [r for r in await asyncio.gather(*(one(h, p) for h in hosts for p in ports)) if r]


def find_lan_models() -> dict:
    """Look for model servers on the other computers in this computer's subnet and list their chat models."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    nets = lan_networks()
    own = set(lan_addresses())                # this computer is covered by find_local_models
    hosts = [str(h) for n in nets for h in n["network"].hosts() if str(h) not in own]
    names = {port: name for name, port in LOCAL_SERVERS}
    open_ = asyncio.run(_open_ports(hosts, list(names))) if hosts else []
    models: list[dict] = []
    if open_:
        with ThreadPoolExecutor(min(32, len(open_))) as pool:
            for ms in pool.map(lambda hp: _probe(names[hp[1]], f"http://{hp[0]}:{hp[1]}/v1"), open_):
                for m in ms:
                    host = m["base_url"].split("//", 1)[1].rsplit(":", 1)[0]
                    if host not in m["server"]:
                        m["server"] += f" on {host}"
                    models.append(m)
    return {"models": models, "addresses": len(hosts), "open": len(open_),
            "networks": [{"network": str(n["network"]), "whole": n["whole"]} for n in nets]}


class OpenAISession:
    def __init__(self, cfg: dict, system: str, tools: list[dict]):
        import openai

        self._openai = openai
        # One reply can take most of an hour on a local model that thinks at length (DeepSeek V4.1 on Strata
        # can write as few as 15 tokens a second), and it costs nothing to wait.
        self.client = openai.OpenAI(base_url=cfg.get("openai_base_url") or None,
                                    api_key=openai_key(cfg), timeout=7200 if _local(cfg) else 1800)
        self.model = cfg.get("openai_model") or "qwen3:32b"
        self.schemas = {t["name"]: t["input_schema"] for t in tools}
        self.tools = [{"type": "function", "function": {
            "name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
            for t in tools]
        self.messages: list = [{"role": "system", "content": system}]
        self.vision_failed = False
        self.window = int(cfg.get("openai_context") or 0) or None    # None: ask the server at the first step
        self.asked_window = bool(self.window)
        self.per_char = 0.3                   # tokens per character of the history, measured at each reply
        self.recall = None                    # () -> text that brings a shortened history up to date (the spec)

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

    # ------------------------------------------------------------ keeping the prompt inside the context window
    COMPACT_AT = 0.7      # of the window: leaves room for a long thinking reply and the next tool results
    COMPACT_TO = 0.45
    KEEP_TURNS = 3        # the newest replies, with their tool results, are never shortened

    @staticmethod
    def _chars(m: dict) -> int:
        """A message's size in characters, an image counted as the text it costs a model about."""
        n = len(json.dumps({k: v for k, v in m.items() if k != "content" and not k.startswith("_")}))
        c = m.get("content")
        if isinstance(c, list):
            return n + sum(len(x.get("text", "")) if x.get("type") == "text" else 3000 for x in c)
        return n + len(c or "")

    def _size(self) -> int:
        return len(json.dumps(self.tools)) + sum(self._chars(m) for m in self.messages)

    def estimate(self) -> int:
        """The prompt's size in tokens, scaled from what the server counted for the last request."""
        return int(self._size() * self.per_char)

    def _context_window(self) -> int | None:
        if not self.asked_window:
            self.asked_window = True
            self.window = served_context(str(self.client.base_url), self.model)
        return self.window

    def _tail(self, turns: int) -> int:
        """The index where the newest turns (each an assistant reply) begin."""
        replies = [i for i, m in enumerate(self.messages) if m["role"] == "assistant"]
        return replies[-turns] if len(replies) >= turns else len(self.messages)

    @staticmethod
    def _shorten(m: dict) -> None:
        m.pop("reasoning_content", None)
        if m.pop("_recall", False):
            m["content"] = "(An older copy of the design spec was here; a later one follows.)"
        elif m["role"] == "tool" and len(m["content"]) > 600:
            m["content"] = (m["content"][:400] + "\n… (shortened to fit the model's context; "
                            "call the tool again if you need all of it)")
        elif m.get("_images") or isinstance(m.get("content"), list):
            m.pop("_images", None)
            m["content"] = "(Board renders shown here earlier were removed to fit the model's context.)"
        for tc in m.get("tool_calls") or []:
            if len(tc["function"]["arguments"]) > 1500:
                tc["function"]["arguments"] = json.dumps(
                    {"shortened": "these arguments were removed to fit the model's context"})

    def compact(self, force: bool = False) -> str:
        """Shorten older turns when the prompt nears the context window; what was done, for the chat ("" if nothing).

        First the older turns lose their thinking, long tool results, long call arguments (a whole spec, say) and
        images. If that is not enough, they are replaced by one message listing the calls made and what was said.
        Either way recall() (the current spec) follows, since the call that sent it may be among those shortened.
        The changed history costs the server one full read of the prompt; it is cached again after that.
        """
        window = self._context_window()
        before = self.estimate()
        if not window or (before < self.COMPACT_AT * window and not force):
            return ""
        target = self.COMPACT_TO * window
        for keep in (self.KEEP_TURNS, 1):
            for m in self.messages[2:self._tail(keep)]:
                self._shorten(m)
            if self.estimate() <= target:
                break
        else:
            cut = self._tail(1)
            done = []
            for m in self.messages[2:cut]:
                if m["role"] == "assistant":
                    if (m.get("content") or "").strip():
                        done.append("You said: " + m["content"].strip()[:600])
                    for tc in m.get("tool_calls") or []:
                        done.append(f"You called {tc['function']['name']}({tc['function']['arguments'][:150]})")
            if len(done) > 80:
                done = done[:20] + [f"… {len(done) - 60} more steps …"] + done[-40:]
            note = ("(Earlier steps of this conversation were removed to fit the model's context. In order:\n"
                    + "\n".join(done) + ")")
            self.messages[2:cut] = [{"role": "user", "content": note}] if cut > 2 else []
        extra = self.recall() if self.recall else ""
        if extra:
            self.messages.append({"role": "user", "content": extra, "_recall": True})
        after = self.estimate()
        return (f"The conversation was near the model's {window // 1000}k-token context, so older steps were "
                f"shortened (about {before // 1000}k → {after // 1000}k tokens).")

    def _tool_args(self) -> dict:
        return {"tools": self.tools, "tool_choice": "auto"} if self.tools else {}

    def _wire(self) -> list:
        return [{k: v for k, v in m.items() if not k.startswith("_")} for m in self.messages]

    def step(self) -> Turn:
        o = self._openai
        notice = self.compact()
        try:
            try:
                wire = self._wire()
                resp = self.client.chat.completions.create(model=self.model, messages=wire, **self._tool_args())
            except (o.BadRequestError, o.InternalServerError, o.UnprocessableEntityError) as e:
                if re.search(r"context|too long|too many tokens|exceed", str(e), re.I) and len(self.messages) > 3:
                    if not self.window:            # the server knows its limit even when it would not say it
                        self.window = int(self.estimate() / self.COMPACT_AT)
                    notice = self.compact(force=True) or notice
                elif not any(m.get("_images") for m in self.messages) or not self._drop_images():
                    raise
                del e
                wire = self._wire()
                resp = self.client.chat.completions.create(model=self.model, messages=wire, **self._tool_args())
        except o.AuthenticationError as e:
            raise AIError("The AI server rejected the API key. Check it in Settings.") from e
        except o.NotFoundError as e:
            raise AIError(f"Model {self.model!r} not found on the AI server.") from e
        except o.APIConnectionError as e:
            raise AIError(unreachable(str(self.client.base_url), e)) from e
        except o.APIStatusError as e:
            raise AIError(f"AI server error {e.status_code}: {e.message}") from e
        choice = resp.choices[0]
        m = choice.message
        entry: dict = {"role": "assistant", "content": m.content or ""}
        # DeepSeek V4.1 (on Strata or DeepSeek's own API) returns its thinking apart from the reply, and with tools
        # its template puts every earlier turn's thinking back in the prompt: keep it, or each step it sees reads as
        # one it answered without thinking. Only the replies that carry it send it back.
        reasoning = (m.model_extra or {}).get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            entry["reasoning_content"] = reasoning
        turn = Turn(text=(m.content or "").strip(), notice=notice)
        if resp.usage and resp.usage.prompt_tokens:
            size = len(json.dumps(self.tools)) + sum(self._chars(x) for x in wire)
            self.per_char = resp.usage.prompt_tokens / max(size, 1)
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
            turn.text = re.sub(r"<think>.*?</think>", "", turn.text, flags=re.S).strip()
        return turn


def make_session(ai_cfg: dict, system: str, tools: list[dict]):
    if ai_cfg.get("provider") == "openai":
        return OpenAISession(ai_cfg, system, tools)
    return AnthropicSession(ai_cfg, system, tools)
