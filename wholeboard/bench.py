"""The benchmark: reference prompts run end to end, to see whether a change to a model, a prompt or a block helps.

    python -m wholeboard.bench                         # every prompt in bench/prompts.json, with the AI in Settings
    python -m wholeboard.bench --only blink555,can_node
    python -m wholeboard.bench --worker openai:qwen3-coder --base-url http://localhost:8080/v1 --expert claude-opus-5-5
    python -m wholeboard.bench --table                 # only rewrite bench/RESULTS.md from bench/results/

Each prompt runs unattended (the designer is told not to ask questions, and any question it asks anyway gets
"use your judgement"). For each run it records: completed or not, AI rebuilds, ERC errors, DRC errors, unrouted
connections, simulation failures, the board cost from the quote, tokens and API cost, and the time taken. A run
is saved to bench/results/<date>-<label>.json and bench/RESULTS.md is rewritten with every run.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import uuid
from pathlib import Path

from . import config, pipeline
from .project import STEPS, Project

ROOT = Path(__file__).resolve().parent.parent / "bench"
PROMPTS = ROOT / "prompts.json"
RESULTS = ROOT / "results"
TABLE = ROOT / "RESULTS.md"
NOTE = ("\n\nThis is an unattended benchmark run: do not ask questions. Choose sensible defaults, build the board, "
        "get a clean build and a quote, and finish. Skip the enclosure.")


def _override(ai: dict) -> None:
    """Use other models for this process only: config.load() returns the settings with these merged in."""
    if not ai:
        return
    real = config.load

    def load():
        cfg = real()
        cfg["ai"] = config._merge(cfg["ai"], ai)
        return cfg
    config.load = load


def run_one(item: dict, out_root: Path, timeout_s: float, log=print) -> dict:
    from .ai import agent as agent_mod
    root = out_root / item["id"]
    root.mkdir(parents=True, exist_ok=True)
    state = {"id": f"bench-{item['id']}-{uuid.uuid4().hex[:4]}", "title": item["id"], "prompt": item["prompt"],
             "created": time.time(), "status": "idle",
             "steps": {k: {"status": "pending", "detail": ""} for k, _ in STEPS},
             "spec": None, "revision": 0, "quote": None, "visuals": {}}
    p = Project(root, state)
    p.save()
    builds: list[dict] = []
    questions = []

    def listen(e: dict) -> None:
        if e.get("type") == "question":
            questions.append(e.get("question"))
            threading.Thread(target=agent_mod.answer, args=(p, e["id"], "Use your best judgement; this is an "
                             "unattended run."), daemon=True).start()
    p.listeners.append(listen)
    real_build = pipeline.build

    def build(proj, spec, *a, **kw):
        res = real_build(proj, spec, *a, **kw)
        if proj is p:
            builds.append(res)
        return res
    pipeline.build = build
    started = time.time()
    try:
        agent_mod.Agent(p).start(item["prompt"] + NOTE)
        p.worker.join(timeout_s)
        timed_out = p.worker.is_alive()
        if timed_out:
            p.cancel.set()
            p.worker.join(60)
    finally:
        pipeline.build = real_build
    last = builds[-1] if builds else {}
    ok = [b for b in builds if b.get("ok")]
    final = ok[-1] if ok else last
    usage = p.state.get("usage") or {}
    rt = final.get("routing") or {}
    sim = final.get("simulation") or {}
    row = {
        "id": item["id"], "completed": bool(ok), "finished": bool(p.state.get("finished")), "timed_out": timed_out,
        "builds": len(builds), "rebuilds": max(0, len(builds) - 1), "stage": None if ok else last.get("stage"),
        "erc_errors": len((final.get("erc") or {}).get("errors") or []),
        "drc_errors": (final.get("drc") or {}).get("errors"),
        "unrouted": (rt["connections"] - rt["routed"]) if rt.get("connections") is not None else None,
        "sim_failures": sum(len((b.get("simulation") or {}).get("problems") or []) > 0 for b in builds),
        "sim_problems": len(sim.get("problems") or []),
        "board_cost": (p.state.get("quote") or {}).get("total"),
        "tokens": sum(int(usage.get(k) or 0) for k in ("input", "output", "cache_read", "cache_write")),
        "api_cost": round(float(usage.get("cost") or 0), 4), "by_model": usage.get("by_model"),
        "questions": len(questions), "minutes": round((time.time() - started) / 60, 1), "folder": str(root),
    }
    log(f"{item['id']}: {'completed' if row['completed'] else 'failed at ' + str(row['stage'])}, "
        f"{row['rebuilds']} rebuilds, ${row['api_cost']:.2f} API, {row['minutes']} min")
    return row


def label(cfg_ai: dict) -> str:
    def name(provider, model):
        return model or provider
    w = name(cfg_ai.get("provider"), cfg_ai.get("anthropic_model") if cfg_ai.get("provider") == "anthropic"
             else cfg_ai.get("openai_model"))
    ex = cfg_ai.get("expert") or {}
    return w + (f" + {ex.get('model')}" if ex.get("enabled") else "")


def summarise(run: dict) -> dict:
    rows = run["rows"]
    n = len(rows) or 1
    done = [r for r in rows if r["completed"]]

    def avg(key, rs=rows):
        vals = [r[key] for r in rs if r.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None
    return {"completed": f"{len(done)}/{len(rows)}", "rate": len(done) / n, "rebuilds": avg("rebuilds"),
            "erc": sum(r["erc_errors"] or 0 for r in rows), "drc": sum(r["drc_errors"] or 0 for r in rows),
            "unrouted": sum(r["unrouted"] or 0 for r in rows), "sim_failures": sum(r["sim_failures"] for r in rows),
            "board_cost": avg("board_cost", done), "tokens": round(sum(r["tokens"] for r in rows) / n),
            "api_cost": round(sum(r["api_cost"] for r in rows), 2), "minutes": avg("minutes")}


def table() -> str:
    runs = []
    for f in sorted(RESULTS.glob("*.json")):
        try:
            runs.append(json.loads(f.read_text()))
        except (OSError, ValueError):
            continue
    lines = ["# Benchmark results", "",
             "Written by `python -m wholeboard.bench`; see [bench/README.md](README.md). Each row is one run of the "
             "reference prompts. ERC, DRC and unrouted count what was left on the last clean (or last) build; "
             "simulation failures count builds stopped by the simulation.", ""]
    if not runs:
        lines.append("No runs yet.")
        return "\n".join(lines) + "\n"
    lines += ["| Date | Models | Prompts | Completed | Avg rebuilds | ERC | DRC | Unrouted | Sim failures | Avg board cost | "
              "Tokens per prompt | API cost | Avg minutes |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in runs:
        s = summarise(r)
        lines.append(f"| {r['date'][:10]} | {r['label']} | {len(r['rows'])} | {s['completed']} | {s['rebuilds']} | "
                     f"{s['erc']} | {s['drc']} | {s['unrouted']} | {s['sim_failures']} | "
                     + (f"${s['board_cost']:.2f}" if s["board_cost"] is not None else "–")
                     + f" | {s['tokens']:,} | ${s['api_cost']:.2f} | {s['minutes']} |")
    last = runs[-1]
    lines += ["", f"## Latest run: {last['label']}, {last['date'][:16].replace('T', ' ')}", "",
              "| Prompt | Result | Rebuilds | ERC | DRC | Unrouted | Sim failures | Board cost | Tokens | API cost | Minutes |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in last["rows"]:
        res = "✓" if r["completed"] else f"✗ {r.get('stage') or ('timed out' if r.get('timed_out') else '')}"
        lines.append(f"| {r['id']} | {res} | {r['rebuilds']} | {r['erc_errors']} | {r['drc_errors'] if r['drc_errors'] is not None else '–'} | "
                     f"{r['unrouted'] if r['unrouted'] is not None else '–'} | {r['sim_failures']} | "
                     + (f"${r['board_cost']:.2f}" if r.get("board_cost") is not None else "–")
                     + f" | {r['tokens']:,} | ${r['api_cost']:.2f} | {r['minutes']} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m wholeboard.bench", description=__doc__.split("\n")[0])
    ap.add_argument("--only", help="comma-separated prompt ids")
    ap.add_argument("--worker", help="provider:model for the worker, e.g. anthropic:claude-sonnet-5-5 or openai:qwen3")
    ap.add_argument("--base-url", help="the OpenAI-compatible server for an openai worker")
    ap.add_argument("--expert", help="expert model (Claude); 'none' to turn the expert off")
    ap.add_argument("--timeout", type=float, default=45, help="minutes per prompt (default 45)")
    ap.add_argument("--label", help="name for this run in the table")
    ap.add_argument("--table", action="store_true", help="only rewrite bench/RESULTS.md")
    args = ap.parse_args(argv)
    if args.table:
        TABLE.write_text(table())
        print(f"wrote {TABLE}")
        return 0
    ai: dict = {}
    if args.worker:
        prov, _, model = args.worker.partition(":")
        ai["provider"] = prov
        ai["anthropic_model" if prov == "anthropic" else "openai_model"] = model
    if args.base_url:
        ai["openai_base_url"] = args.base_url
    if args.expert:
        ai["expert"] = {"enabled": False} if args.expert == "none" else {"enabled": True, "provider": "anthropic",
                                                                          "model": args.expert}
    _override(ai)
    items = json.loads(PROMPTS.read_text())
    if args.only:
        want = set(args.only.split(","))
        items = [i for i in items if i["id"] in want]
    lab = args.label or label(config.load()["ai"])
    stamp = time.strftime("%Y-%m-%dT%H%M%S")
    out_root = Path(config.load()["projects_dir"]).expanduser() / "bench" / f"{stamp}-{re.sub(r'[^A-Za-z0-9.+-]+', '_', lab)}"
    print(f"Benchmark '{lab}': {len(items)} prompts, projects in {out_root}", flush=True)
    rows = []
    for it in items:
        try:
            rows.append(run_one(it, out_root, args.timeout * 60, log=lambda s: print(s, flush=True)))
        except Exception as e:  # noqa: BLE001
            print(f"{it['id']}: the run failed: {e}", file=sys.stderr, flush=True)
            rows.append({"id": it["id"], "completed": False, "stage": f"runner error: {e}", "builds": 0, "rebuilds": 0,
                         "erc_errors": 0, "drc_errors": None, "unrouted": None, "sim_failures": 0, "board_cost": None,
                         "tokens": 0, "api_cost": 0.0, "minutes": 0})
    RESULTS.mkdir(parents=True, exist_ok=True)
    run = {"date": time.strftime("%Y-%m-%dT%H:%M:%S"), "label": lab, "ai": {k: v for k, v in config.load()["ai"].items()
           if not k.endswith("api_key")}, "rows": rows}
    (RESULTS / f"{stamp}-{re.sub(r'[^A-Za-z0-9.+-]+', '_', lab)}.json").write_text(json.dumps(run, indent=1))
    TABLE.write_text(table())
    s = summarise(run)
    print(f"Done: {s['completed']} completed, ${s['api_cost']:.2f} API cost. Table: {TABLE}")
    return 0 if rows and all(r["completed"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
