"""User settings, stored as JSON in the user's home folder."""

from __future__ import annotations

import json
import os
import platform
import threading
from pathlib import Path

APP_DIR = Path(os.environ.get("WHOLEBOARD_HOME", Path.home() / ".wholeboard"))
CONFIG_FILE = APP_DIR / "config.json"
DEFAULT_PROJECTS = Path.home() / "WholeBoardProjects"

DEFAULTS: dict = {
    "ai": {
        # provider: "anthropic" (Claude, cloud) or "openai" (any OpenAI-compatible server:
        # OpenAI, Ollama, LM Studio, llama.cpp, vLLM, OpenRouter ...)
        "provider": "anthropic",
        "anthropic_api_key": "",
        "anthropic_model": "claude-opus-5-5",
        "anthropic_effort": "high",
        "openai_base_url": "http://localhost:11434/v1",
        "openai_api_key": "",
        "openai_model": "qwen3:32b",
        "openai_price_in": 0,       # US$ per million tokens, for cost tracking (0 = free / local)
        "openai_price_out": 0,
        "max_turns": 60,
        # a stronger model that plans and reviews while the model above does the legwork
        "expert": {"enabled": False, "provider": "anthropic", "model": "claude-opus-5-5", "effort": "high"},
        "vision": "auto",           # show board renders to the AI for a visual review: auto | on | off
    },
    "tools": {
        "kicad_cli": "",            # empty = auto-detect
        "tracemaker": "",           # empty = auto-detect
        "tracemaker_place": "",     # empty = auto-detect
        "tracemaker_wsl": False,    # Windows: run TraceMaker inside WSL
        "freecad": "",              # FreeCAD (freecadcmd, the app, or an AppImage); empty = auto-detect
        "ngspice": "",              # ngspice program or KiCad's libngspice; empty = auto-detect
        "platformio": "",           # pio program, for compiling the starter firmware; empty = auto-detect
        "models3d": "auto",         # 3D part models: auto (download missing ones), local, off
        "models3d_dir": "",         # extra folder of KiCad 3D models (kicad-packages3D)
        "footprint_dir": "",        # empty = auto-detect
        "symbol_dir": "",           # empty = auto-detect
        "route_time_s": 120,
        "place_time_s": 60,
        "live_viewer": True,
        "viewer_port": 8766,
    },
    "jlcpcb": {
        "quantity": 5,
        "assembly": True,
        "assembly_quantity": 2,
        "pcba_type": "economic",    # economic | standard
        "surface_finish": "HASL(with lead)",
        "solder_mask": "Green",
        "shipping_country": "AU",
        "shipping_method": "Global Standard Direct",
        "currency": "USD",
    },
    "suppliers": {                  # optional: prices for parts JLCPCB cannot fit
        "mouser_api_key": "",
        "digikey_client_id": "",
        "digikey_client_secret": "",
    },
    "printing": {                   # for the enclosure's print cost estimate
        "material": "PLA",
        "price_per_kg": 25.0,       # filament, in your currency
        "rate_cm3_h": 15.0,         # volume your printer lays down per hour (Bambu/Prusa MK4 ~25, older ~10)
        "power_w": 120.0,
        "price_kwh": 0.30,
        "machine_per_h": 0.0,       # wear, or a print service's hourly rate
    },
    "github": {                     # for publishing boards; empty = use the GitHub CLI's sign-in (gh auth login)
        "token": "",
    },
    "projects_dir": str(DEFAULT_PROJECTS),
    "port": 8770,
}

_lock = threading.Lock()


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    with _lock:
        data: dict = {}
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text())
            except (OSError, json.JSONDecodeError):
                data = {}
        return _merge(DEFAULTS, data)


def save(cfg: dict) -> None:
    with _lock:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, indent=2))
        if platform.system() != "Windows":
            os.chmod(tmp, 0o600)  # holds API keys
        tmp.replace(CONFIG_FILE)


def update(patch: dict) -> dict:
    cfg = _merge(load(), patch)
    save(cfg)
    return cfg


def public(cfg: dict) -> dict:
    """The config with secrets masked, for sending to the browser."""
    out = json.loads(json.dumps(cfg))
    out.pop("server", None)
    for sect, key in SECRETS:
        if sect in out:
            v = out[sect].get(key) or ""
            out[sect][key] = ("•" * 8 + v[-4:]) if v else ""
    return out


SECRETS = (("ai", "anthropic_api_key"), ("ai", "openai_api_key"), ("suppliers", "mouser_api_key"),
           ("suppliers", "digikey_client_secret"), ("github", "token"))
