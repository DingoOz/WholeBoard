"""A second, stronger model that plans the design and reviews it, while a cheaper model does the legwork.

The everyday model (Settings → AI) runs every turn: searches, writing the spec, fixing build errors. The expert
is called only at a few points: to plan the architecture once the requirements are clear, to review each clean
build, when the worker has failed to build several times in a row, and when the worker asks it a question.
"""

from __future__ import annotations

import json

from .providers import make_session

SYSTEM = """You are a senior PCB design engineer advising a junior designer (another AI) who builds boards \
for JLCPCB with KiCad, the TraceMaker autorouter and ngspice. The junior does the part searches and writes the \
design spec; you give direction. Be concrete and brief: name parts (with LCSC numbers when you know them), \
values, building blocks, board size and layers, and the risks to watch. Do not write the full spec. Use plain \
text with short bullet points, under 300 words."""


def settings(ai: dict) -> dict | None:
    """The expert's provider settings, or None when no expert is set up."""
    ex = ai.get("expert") or {}
    if not ex.get("enabled") or not ex.get("model"):
        return None
    cfg = dict(ai, provider=ex.get("provider") or "anthropic")
    if cfg["provider"] == "anthropic":
        cfg["anthropic_model"] = ex["model"]
        cfg["anthropic_effort"] = ex.get("effort") or "high"
    else:
        cfg["openai_model"] = ex["model"]
    return cfg


def ask(cfg: dict, text: str, images: list[dict] | None = None) -> tuple[str, dict]:
    """One question to the expert. Returns its answer and the turn's usage."""
    sess = make_session(cfg, SYSTEM, [])
    sess.add_user(text)
    if images:     # replace the plain text turn with text plus pictures, in the provider's format
        if cfg.get("provider") == "openai":
            pics = [{"type": "image_url", "image_url": {"url": f"data:{i['media_type']};base64,{i['data']}"}}
                    for i in images]
        else:
            pics = [{"type": "image", "source": {"type": "base64", "media_type": i["media_type"], "data": i["data"]}}
                    for i in images]
        sess.messages[-1] = {"role": "user", "content": [{"type": "text", "text": text}] + pics}
    turn = sess.step()
    return turn.text.strip(), turn.usage


def plan_prompt(requirements: list[str], blocks: list[dict]) -> str:
    return ("Plan this board. The requirements, from the user's messages:\n\n" + "\n\n".join(requirements)
            + "\n\nProven building blocks the junior can drop in whole (id: title):\n"
            + "\n".join(f"- {b['id']}: {b['title']}" for b in blocks)
            + "\n\nGive: the architecture (which blocks, which other key parts), supply and power budget, board size "
              "and layer count, connectors and where they go, and the 3 biggest risks.")


def review_prompt(spec: dict, result: dict) -> str:
    keep = {k: result.get(k) for k in ("simulation", "board_checks", "drc", "routing", "quote", "impedance", "warnings",
                                       "part_notes", "erc") if result.get(k)}
    return ("The junior's board built cleanly. Review it like a design review before ordering: real electrical "
            "mistakes, missing protection or support parts, bad part choices, layout or connector problems, cost. "
            "List each issue as PROBLEM (must fix) or NOTE, most important first; say 'No problems' if it is good.\n\n"
            f"Design spec:\n{json.dumps(spec)[:60000]}\n\nBuild results:\n{json.dumps(keep, default=str)[:20000]}")


def stuck_prompt(spec: dict, failures: list[dict]) -> str:
    return ("The junior has failed to build this board several times in a row. Work out what is really wrong and "
            "tell them exactly what to change.\n\n"
            f"Design spec:\n{json.dumps(spec)[:60000]}\n\nThe last failures:\n{json.dumps(failures, default=str)[:20000]}")
