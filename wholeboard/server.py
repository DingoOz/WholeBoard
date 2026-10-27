"""FastAPI server: REST API, live event stream and the web UI."""

from __future__ import annotations

import asyncio
import json
import platform
import re
import shutil
import subprocess
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, auth, config, design, enclosure, mcp, ngspice, pipeline, publish, revisions, router
from .ai import agent as agent_mod
from .ai.providers import AIError, make_session
from .kicad import cli, libs
from .project import SHORT, STEPS, Manager

WEB = Path(__file__).with_name("web")
app = FastAPI(title="WholeBoard", version=__version__)
app.mount("/static", StaticFiles(directory=WEB), name="static")
app.add_middleware(auth.AuthMiddleware)
manager: Manager | None = None
_agents: dict[str, agent_mod.Agent] = {}
_status_cache: dict = {}


def mgr() -> Manager:
    global manager
    if manager is None:
        manager = Manager()
    return manager


def _project(pid: str):
    p = mgr().get(pid)
    if not p:
        raise HTTPException(404, "project not found")
    return p


def _agent(p, request: Request | None = None) -> agent_mod.Agent:
    a = _agents.get(p.id)
    if a is None:
        origin = str(request.base_url).rstrip("/") if request else None
        a = agent_mod.Agent(p, origin)
        _agents[p.id] = a
    return a


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/login")
def login_form(request: Request):
    if not auth.enabled() or auth.is_local(request.scope) or auth.valid_token(request.cookies.get(auth.COOKIE)):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(auth.login_page())


@app.post("/api/login")
async def login(request: Request):
    ip = (request.client.host if request.client else "") or "?"
    if not auth.allow_attempt(ip):
        await asyncio.sleep(2)
        return HTMLResponse(auth.login_page("Too many tries. Wait a minute and try again."), status_code=429)
    password = auth.form_password(await request.body())
    stored = (config.load().get("server") or {}).get("password_hash") or ""
    ok = await asyncio.to_thread(auth.check_password, password, stored)
    if not ok:
        await asyncio.sleep(1)
        return HTMLResponse(auth.login_page("That password is not right."), status_code=401)
    r = RedirectResponse("/", status_code=303)
    r.set_cookie(auth.COOKIE, auth.make_token(), max_age=auth.LIFETIME_S, httponly=True, samesite="lax")
    return r


@app.post("/api/logout")
def logout():
    r = JSONResponse({"ok": True})
    r.delete_cookie(auth.COOKIE)
    return r


@app.post("/api/auth/password")
async def set_lan_password(request: Request):
    """Set or clear the network password. Only from the computer running WholeBoard."""
    if not auth.is_local(request.scope):
        raise HTTPException(403, "the password can only be changed on the computer running WholeBoard")
    password = str((await request.json()).get("password") or "")
    if password and len(password) < 6:
        raise HTTPException(400, "use at least 6 characters")
    await asyncio.to_thread(auth.set_password, password or None)
    return {"enabled": bool(password)}


@app.get("/api/status")
def status(request: Request, refresh: bool = False):
    cfg = config.load()
    if refresh or not _status_cache:
        _status_cache.update({
            "kicad_cli": cli.find_kicad_cli(cfg), "kicad_version": cli.version(),
            "tracemaker": router.find("tracemaker", cfg), "tracemaker_version": router.version(cfg),
            "placer": router.find("tracemaker-place", cfg), "viewer": router.viewer_available(cfg),
        })
        fc = enclosure.find_freecad(cfg)
        _status_cache.update({"freecad": " ".join(fc) if fc else None,
                              "freecad_version": enclosure.freecad_version(fc) if fc else None})
        ng = ngspice.find(cfg)
        _status_cache.update({"ngspice": ngspice.label(ng) if ng else None,
                              "ngspice_version": ngspice.version(ng) if ng else None})
    ai = cfg["ai"]
    if ai["provider"] == "openai":
        ai_label, ai_ready = f"{ai['openai_model']} @ {ai['openai_base_url']}", bool(ai["openai_base_url"])
    else:
        import os
        ai_label = ai["anthropic_model"]
        ai_ready = bool(ai["anthropic_api_key"] or os.environ.get("ANTHROPIC_API_KEY")
                        or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    return {**_status_cache, "libs": libs.status(cfg), "ai": {"provider": ai["provider"], "label": ai_label,
            "ready": ai_ready}, "os": platform.system(), "version": __version__,
            "auth": {"enabled": auth.enabled(), "local": auth.is_local(request.scope)},
            "steps": [{"key": k, "title": t, "short": SHORT.get(k, t)} for k, t in STEPS]}


@app.get("/api/config")
def get_config():
    return config.public(config.load())


@app.post("/api/config")
async def set_config(request: Request):
    patch = await request.json()
    patch.pop("server", None)             # the password has its own endpoint
    for sect, key in config.SECRETS:
        v = (patch.get(sect) or {}).get(key)
        if isinstance(v, str) and v.startswith("•"):
            patch[sect].pop(key)          # masked value sent back unchanged
    cfg = config.update(patch)
    libs._INDEX.clear()
    _status_cache.clear()
    return config.public(cfg)


@app.post("/api/ai/test")
def ai_test():
    cfg = config.load()
    try:
        s = make_session(cfg["ai"], "Reply with the single word OK.", [])
        s.add_user("Say OK.")
        t = s.step()
        return {"ok": True, "reply": t.text[:200], "model": s.label}
    except AIError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@app.post("/api/ai/models")
def ai_models():
    from .ai.providers import list_models
    try:
        return {"ok": True, "models": list_models(config.load()["ai"])}
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "401" in msg or "authentication" in msg.lower() or "api key" in msg.lower():
            msg = "The provider rejected the API key. Check it was copied in full."
        return {"ok": False, "error": msg[:300]}


@app.post("/api/ai/local-models")
async def ai_local_models(request: Request):
    """Search this computer for local model servers; body {url} adds the Server URL from the form."""
    from .ai.providers import find_local_models
    body = await request.json() if await request.body() else {}
    return await asyncio.to_thread(find_local_models, body.get("url"))


_lan_search = threading.Lock()


@app.post("/api/ai/lan-models")
async def ai_lan_models():
    """Search the subnet this computer is on for model servers on other computers."""
    from .ai.providers import find_lan_models
    if not _lan_search.acquire(blocking=False):
        raise HTTPException(409, "a LAN search is already running")
    try:
        return await asyncio.to_thread(find_lan_models)
    finally:
        _lan_search.release()


@app.post("/api/tools/freecad/fetch")
async def fetch_freecad():
    lines: list[str] = []
    try:
        cmd = await asyncio.to_thread(enclosure.fetch_freecad, lines.append)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "log": lines + [str(e)]}
    _status_cache.clear()
    return {"ok": bool(cmd), "log": lines, "freecad": " ".join(cmd) if cmd else None}


@app.post("/api/tools/ngspice/fetch")
async def fetch_ngspice():
    from . import doctor
    lines: list[str] = []
    try:
        ok = await asyncio.to_thread(doctor.install_ngspice, lines.append)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "log": lines + [str(e)]}
    _status_cache.clear()
    return {"ok": bool(ok), "log": lines}


@app.post("/api/libs/fetch")
async def fetch_libs(request: Request):
    body = await request.json()
    source = body.get("source", "gitlab")
    lines: list[str] = []
    try:
        if source == "docker":
            ok = await asyncio.to_thread(libs.fetch_from_docker, log=lines.append)
        else:
            ok = await asyncio.to_thread(libs.fetch_from_gitlab, log=lines.append)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "log": lines + [str(e)]}
    return {"ok": ok, "log": lines, "status": libs.status()}


@app.get("/api/projects")
def list_projects():
    return mgr().list()


@app.post("/api/projects/clear")
async def clear_projects(request: Request):
    """Hide every board from the list (restore=true shows them again). Nothing is deleted."""
    body = await request.json() if await request.body() else {}
    return {"changed": mgr().set_hidden(not body.get("restore"))}


@app.post("/api/projects")
async def create_project(request: Request):
    body = await request.json()
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "describe the board first")
    p = mgr().create(prompt)
    if body.get("driver") == "claude-code":
        _agent(p, request).adopt(prompt)
    else:
        _agent(p, request).start(prompt)
    return {**p.summary(), "url": f"{str(request.base_url).rstrip('/')}/#{p.id}"}


@app.post("/api/projects/{pid}/tool")
async def outside_tool(pid: str, request: Request):
    """One designer tool call from Claude Code (through `python -m wholeboard.mcp`), run here so the page follows it."""
    p = _project(pid)
    body = await request.json()
    try:
        return await asyncio.to_thread(_agent(p, request).run_outside, str(body.get("name")), body.get("args") or {})
    except agent_mod.UnknownTool as e:
        raise HTTPException(404, f"no designer tool {e}") from None
    except agent_mod.Busy as e:
        raise HTTPException(409, str(e)) from None
    except Exception as e:  # noqa: BLE001 - the agent sees the error and can fix its call
        return {"text": f"{type(e).__name__}: {e}", "error": True}


@app.get("/api/claude-code")
def claude_code(request: Request):
    """What the "Design with Claude Code" screen shows: the line that adds WholeBoard, and how far along it is."""
    seen = [p.state.get("driver_seen") or 0 for p in mgr().projects.values() if p.state.get("driver") == "claude-code"]
    return {"claude": shutil.which("claude"), "added": mcp.added(),
            "command": mcp.shell_line(mcp.add_command(request.url.port)),
            "local": auth.is_local(request.scope), "url": f"http://127.0.0.1:{request.url.port or 80}",
            "last_used": max(seen, default=None), "boards": len(seen)}


@app.post("/api/claude-code/add")
def claude_code_add(request: Request):
    """Run `claude mcp add` for the user. Only from this computer: it changes this computer's Claude Code."""
    if not auth.is_local(request.scope):
        raise HTTPException(403, "only the computer running WholeBoard can add it to its Claude Code")
    exe = shutil.which("claude")
    if not exe:
        raise HTTPException(404, "Claude Code (the claude command) is not installed on this computer")
    argv = [exe] + mcp.add_command(request.url.port)[1:]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise HTTPException(500, f"claude mcp add did not run: {e}") from None
    out = (r.stdout + r.stderr).strip()
    if r.returncode and not mcp.added():
        raise HTTPException(500, out or f"claude mcp add exited with {r.returncode}")
    return {"added": mcp.added(), "output": out}


@app.get("/api/github")
def github_account():
    return publish.account()


@app.get("/api/projects/{pid}/publish")
def publish_preview(pid: str):
    """What publishing this board to GitHub would put in the repository."""
    try:
        return publish.preview(_project(pid))
    except publish.PublishError as e:
        raise HTTPException(400, str(e)) from None


_publishing: set[str] = set()


@app.post("/api/projects/{pid}/publish")
async def publish_board(pid: str, request: Request):
    """Create a GitHub repository with the board's latest build, or add a commit to the one it was published to."""
    p = _project(pid)
    body = await request.json()
    if pid in _publishing:
        raise HTTPException(409, "this board is already being published")
    _publishing.add(pid)
    try:
        info = await asyncio.to_thread(publish.publish, p, (body.get("name") or "").strip() or None,
                                       (body.get("description") or "").strip() or None, body.get("private", True) is not False)
    except publish.PublishError as e:
        raise HTTPException(400, str(e)) from None
    finally:
        _publishing.discard(pid)
    p.chat("system", f"Published revision {info['revision']} to GitHub: {info['url']}")
    return info


@app.post("/api/import")
async def import_project(request: Request):
    """Start a project from an existing KiCad schematic/board (files or a zip, base64 in JSON)."""
    import base64
    from .kicad import importer
    body = await request.json()
    try:
        files = [(f["name"], base64.b64decode(f["data"])) for f in body.get("files") or []]
    except (KeyError, ValueError, TypeError):
        raise HTTPException(400, "send files as [{name, data (base64)}]")
    if not files:
        raise HTTPException(400, "choose a .kicad_sch, .kicad_pcb or a zip of the project")
    title = (body.get("title") or "").strip() or Path(files[0][0]).stem
    p = mgr().create(f"Imported KiCad project {title}")
    p.state["title"] = title
    p.save()
    try:
        importer.save_upload(files, p.root / "import")
    except (ValueError, OSError) as e:
        import shutil
        shutil.rmtree(p.root, ignore_errors=True)
        mgr().projects.pop(p.id, None)
        raise HTTPException(400, str(e))
    use_ai = body.get("ai", True)
    origin = str(request.base_url).rstrip("/")
    agent = _agent(p, request) if use_ai else None

    def work():
        p.set_status("running", "Reading the KiCad project")
        p.step("brief", "running", "Importing the KiCad project")
        try:
            res = importer.to_spec(p.root / "import", title, log=lambda m: p.log("brief", m))
        except Exception as e:  # noqa: BLE001
            p.step("brief", "error", str(e))
            p.set_status("error", str(e))
            return
        p.visual("brief", "import", "Imported KiCad project",
                 report={k: res[k] for k in ("notes", "errors", "warnings", "files", "counts")})
        p.step("brief", "done", f"{res['counts']['components']} parts, {res['counts']['nets']} nets imported")
        spec = res["spec"]
        if agent:
            p.set_status("idle", "")
            with p.lock:
                p.worker = None   # hand over to the agent's own thread
            agent.start(import_prompt(res))
            return
        if res["errors"]:
            p.state["spec"] = spec
            p.save()
            p.chat("system", "The import needs fixing before it can be built: " + "; ".join(res["errors"][:8])
                   + ". Edit the spec, or ask the AI to fix it.")
            p.set_status("idle", "")
            return
        p.set_status("running", "Building the imported design")
        try:
            out = pipeline.build(p, spec, origin, by="imported")
            p.chat("system", f"Imported {res['counts']['components']} parts and {res['counts']['nets']} nets and built "
                   "the board: " + ("clean and quoted." if out.get("ok") else f"it stopped at {out.get('stage')}.")
                   + " Configure an AI in Settings to have it review and improve the design.")
            p.set_status("done" if out.get("ok") else "idle", "")
        except pipeline.Cancelled:
            p.set_status("stopped", "Stopped by the user")
        except Exception as e:  # noqa: BLE001
            p.set_status("error", str(e))

    p.worker = threading.Thread(target=work, daemon=True)
    p.worker.start()
    return p.summary()


def import_prompt(res: dict) -> str:
    files = " and ".join(f for f in (res["files"].get("schematic"), res["files"].get("board")) if f)
    parts = [f"I imported an existing KiCad design ({files}). The spec below was read from it. Keep the "
             "circuit as it is. Check it and fix what the import notes and validation list: find JLCPCB parts "
             "(same value and package, basic parts first) for parts without an LCSC number, and swap footprints "
             "that are not in KiCad's standard libraries for standard ones with the same pads. Placements come "
             "from the original board; keep them as hints. Ask me only if something is unclear, then simulate "
             "and submit the design."]
    for label, key in (("Import notes", "notes"), ("Validation errors", "errors"), ("Validation warnings", "warnings")):
        if res[key]:
            parts.append(label + ":\n" + "\n".join("- " + x for x in res[key][:40]))
    parts.append("Spec:\n```json\n" + json.dumps(res["spec"], indent=1) + "\n```")
    return "\n\n".join(parts)


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    p = _project(pid)
    return {"state": p.state, "events": p.events, "title": p.display_title()}


@app.patch("/api/projects/{pid}")
async def rename_project(pid: str, request: Request):
    """Rename a board; an empty title goes back to the designer's own."""
    p = _project(pid)
    title = re.sub(r"\s+", " ", str((await request.json()).get("title") or "")).strip()[:80]
    with p.lock:
        if title:
            p.state["custom_title"] = title
        else:
            p.state.pop("custom_title", None)
        p.save()
    return p.summary()


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    p = _project(pid)
    if p.worker and p.worker.is_alive():
        raise HTTPException(409, "stop the project first")
    import shutil
    shutil.rmtree(p.root, ignore_errors=True)
    mgr().projects.pop(pid, None)
    return {"ok": True}


@app.post("/api/projects/{pid}/message")
async def message(pid: str, request: Request):
    p = _project(pid)
    text = ((await request.json()).get("text") or "").strip()
    if not text:
        raise HTTPException(400, "empty message")
    _agent(p, request).start(text)
    return {"ok": True}


@app.post("/api/projects/{pid}/answer")
async def answer(pid: str, request: Request):
    p = _project(pid)
    body = await request.json()
    agent_mod.answer(p, body["id"], str(body.get("text", "")))
    return {"ok": True}


@app.post("/api/projects/{pid}/cancel")
def cancel(pid: str):
    p = _project(pid)
    p.cancel.set()
    with p.answer_ready:
        p.answer_ready.notify_all()
    return {"ok": True}


@app.post("/api/projects/{pid}/quote")
async def requote(pid: str, request: Request):
    p = _project(pid)
    if not p.state.get("spec"):
        raise HTTPException(400, "no design yet")
    opts = await request.json()
    res = await asyncio.to_thread(pipeline.run_quote, p, p.state["spec"], opts)
    return res


@app.post("/api/projects/{pid}/panel")
async def make_panel(pid: str, request: Request):
    """Draw the panel with KiKit and export its Gerbers, BOM and CPL."""
    p = _project(pid)
    if not p.state.get("spec"):
        raise HTTPException(400, "no design yet")
    body = await request.json()
    nx, ny = int(body.get("nx") or 0), int(body.get("ny") or 0)
    if not (1 <= nx <= 10 and 1 <= ny <= 10 and nx * ny >= 2):
        raise HTTPException(400, "nx and ny must make at least two boards")
    return await asyncio.to_thread(pipeline.run_panel, p, p.state["spec"], nx, ny)


@app.post("/api/projects/{pid}/swaps")
async def apply_swaps(pid: str, request: Request):
    """Use cheaper or in-stock LCSC parts from the quote's suggestions, then re-quote."""
    p = _project(pid)
    if not p.state.get("spec"):
        raise HTTPException(400, "no design yet")
    if p.worker and p.worker.is_alive():
        raise HTTPException(409, "the project is busy")
    body = await request.json()
    from .jlc import savings
    with p.lock:
        changed = savings.apply(p.state["spec"], body.get("swaps") or [])
        p.save()
    if not changed:
        return {"changed": []}
    files = await asyncio.to_thread(pipeline.refresh_bom, p, p.state["spec"])
    res = await asyncio.to_thread(pipeline.run_quote, p, p.state["spec"], (p.state.get("visuals", {}).get("quote:quote") or {}).get("options"))
    p.chat("system", f"Switched {', '.join(changed)} to cheaper JLCPCB parts" + (f" and rewrote {', '.join(files)}" if files else "")
           + (f". New total ${res['total']:.2f}." if res.get("total") is not None else "."))
    return {"changed": changed, "files": files, **res}


@app.post("/api/projects/{pid}/stock")
async def check_stock(pid: str, request: Request):
    """Check every BOM line against JLCPCB's live stock, right before ordering."""
    p = _project(pid)
    if not p.state.get("spec"):
        raise HTTPException(400, "no design yet")
    body = await request.json() if (await request.body()) else {}
    from .jlc import stock
    opts = {**config.load()["jlcpcb"], **((p.state.get("visuals", {}).get("quote:quote") or {}).get("options") or {}),
            **(body or {})}
    res = await asyncio.to_thread(stock.check, p.state["spec"], opts, lambda m: p.log("quote", m))
    with p.lock:
        p.state["stock"] = res
        p.save()
    p.emit({"type": "stock", **res})
    return res


@app.post("/api/projects/{pid}/firmware/build")
def build_firmware(pid: str):
    """Compile the starter firmware in the background; the firmware card updates when it is done."""
    p = _project(pid)
    if p.fw_thread and p.fw_thread.is_alive():
        raise HTTPException(409, "the firmware is already compiling")
    p.fw_thread = threading.Thread(target=pipeline.build_firmware, args=(p,), daemon=True, name=f"fw-{p.id}")
    p.fw_thread.start()
    return {"started": True}


@app.post("/api/projects/{pid}/report")
async def project_report(pid: str):
    p = _project(pid)
    if not p.state.get("spec"):
        raise HTTPException(400, "no design yet")
    pdf = await asyncio.to_thread(pipeline.make_report, p)
    if not pdf:
        raise HTTPException(500, "the report could not be written; see the log")
    return {"url": p.file_url(pdf), "name": pdf.name}


@app.get("/api/projects/{pid}/simlab")
async def simlab_scene(pid: str):
    """The finished board with its simulation, for the 3D Sim Lab."""
    p = _project(pid)
    try:
        return await asyncio.to_thread(pipeline.simlab_scene, p)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/projects/{pid}/simlab/whatif")
async def simlab_whatif(pid: str, request: Request):
    """Re-simulate the board with the Sim Lab's slider values."""
    p = _project(pid)
    body = await request.json()
    try:
        # numbers, except the enclosure's choices (vents, wall material, where it stands)
        values = {str(k): str(v)[:40] if k in ("vents", "material", "placement") else float(v)
                  for k, v in (body.get("values") or {}).items()}
        return await asyncio.to_thread(pipeline.simlab_whatif, p, values)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/projects/{pid}/simlab/video")
async def simlab_video(pid: str, request: Request):
    """Keep a Sim Lab video the browser recorded."""
    p = _project(pid)
    data = await request.body()
    if not data or len(data) > 400 * 1024 * 1024:
        raise HTTPException(400, "no video, or it is too big")
    return await asyncio.to_thread(pipeline.save_simlab_video, p, data, request.headers.get("content-type") or "")


@app.post("/api/projects/{pid}/outline")
async def import_outline(pid: str, request: Request):
    """Read a board outline (and holes) from a DXF and hand it to the designer."""
    import base64
    from . import dxf
    p = _project(pid)
    body = await request.json()
    try:
        text = base64.b64decode(body.get("data") or "").decode("utf-8", errors="replace")
        res = dxf.read(text)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Could not read the DXF: {e}") from e
    name = str(body.get("name") or "outline.dxf")
    holes = res["holes"]
    msg = (f"Use the board outline from {name}: {res['width_mm']:g} × {res['height_mm']:g} mm"
           + (", rectangular." if res["rectangular"] else
              f", custom shape. Set board.outline to {json.dumps(res['outline'])}.")
           + (f" It has {len(holes)} holes; make them the mounting holes with board.mounting_holes "
              f"{json.dumps({'diameter_mm': holes[0]['diameter_mm'], 'positions': [[h['x_mm'], h['y_mm']] for h in holes]})}."
              if holes else "")
           + (" " + " ".join(res["notes"]) if res["notes"] else ""))
    _agent(p, request).start(msg)
    return {**res, "message": msg}


@app.post("/api/tools/platformio/install")
async def install_platformio():
    from . import fwbuild
    try:
        exe = await asyncio.to_thread(fwbuild.install, lambda m: None)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Could not install PlatformIO: {e}") from e
    return {"ok": True, "path": exe}


@app.put("/api/projects/{pid}/enclosure")
async def put_enclosure(pid: str, request: Request):
    """Rebuild the enclosure from an edited enclosure spec, without the AI."""
    p = _project(pid)
    if not p.state.get("spec"):
        raise HTTPException(400, "no design yet")
    enc = await request.json()
    res = await asyncio.to_thread(pipeline.run_enclosure, p, p.state["spec"], enc)
    if res.get("ok") is not False or res.get("collisions"):
        p.chat("system", "Rebuilt the edited enclosure" + (": no collisions." if not res.get("collisions")
                                                           else f" with {len(res['collisions'])} collisions."))
    return res


@app.put("/api/projects/{pid}/spec")
async def put_spec(pid: str, request: Request):
    """Replace the design spec by hand and rebuild it without the AI."""
    p = _project(pid)
    if p.worker and p.worker.is_alive():
        raise HTTPException(409, "the project is busy")
    spec = await request.json()
    rep = design.validate(spec)
    if not rep.ok:
        return JSONResponse({"ok": False, **rep.as_dict()}, status_code=422)
    origin = str(request.base_url).rstrip("/")

    def work():
        p.cancel.clear()
        p.set_status("running", "Rebuilding your edited design")
        try:
            res = pipeline.build(p, spec, origin, by="edited by hand")
            p.chat("system", "Rebuilt the edited design: " + ("clean and quoted." if res.get("ok") else
                   f"stopped at {res.get('stage')}."))
            p.set_status("done" if res.get("ok") else "idle", "")
        except pipeline.Cancelled:
            p.set_status("stopped", "Stopped by the user")
        except Exception as e:  # noqa: BLE001
            p.set_status("error", str(e))

    p.worker = threading.Thread(target=work, daemon=True)
    p.worker.start()
    return {"ok": True}


@app.get("/api/projects/{pid}/revisions")
def list_revisions(pid: str):
    p = _project(pid)
    rows = revisions.listing(p)
    for r in rows:
        if r.get("image"):
            r["image_url"] = f"/api/projects/{pid}/files/{r['image']}"
    return {"revisions": rows, "current": p.state.get("revision")}


@app.get("/api/projects/{pid}/revisions/compare")
def compare_revisions(pid: str, a: int, b: int):
    p = _project(pid)
    try:
        return {"a": a, "b": b, **revisions.compare(revisions.load(p, a), revisions.load(p, b))}
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.post("/api/projects/{pid}/revisions/{rev}/restore")
async def restore_revision(pid: str, rev: int, request: Request):
    """Rebuild an earlier revision's design as a new revision; nothing is deleted."""
    p = _project(pid)
    if p.worker and p.worker.is_alive():
        raise HTTPException(409, "the project is busy")
    try:
        spec = revisions.load(p, rev)
    except KeyError as e:
        raise HTTPException(404, str(e))
    origin = str(request.base_url).rstrip("/")

    def work():
        p.cancel.clear()
        p.set_status("running", f"Rebuilding revision {rev}")
        try:
            res = pipeline.build(p, spec, origin, by=f"restored from rev {rev}")
            p.chat("system", f"Restored revision {rev} as revision {res.get('revision')}"
                   + (": clean and quoted." if res.get("ok") else f"; it stopped at {res.get('stage')}."))
            p.set_status("done" if res.get("ok") else "idle", "")
        except pipeline.Cancelled:
            p.set_status("stopped", "Stopped by the user")
        except Exception as e:  # noqa: BLE001
            p.set_status("error", str(e))

    p.worker = threading.Thread(target=work, daemon=True)
    p.worker.start()
    return {"ok": True}


@app.get("/api/projects/{pid}/files/{path:path}")
def project_file(pid: str, path: str):
    p = _project(pid)
    root = p.root.resolve()
    f = (root / path).resolve()
    if not f.is_relative_to(root) or not f.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(f, headers={"Cache-Control": "no-store"})


@app.post("/api/projects/{pid}/open-folder")
def open_folder(pid: str):
    p = _project(pid)
    folder = (p.state.get("artifacts") or {}).get("folder") or str(p.root)
    system = platform.system()
    cmd = {"Darwin": ["open", folder], "Windows": ["explorer", folder]}.get(system, ["xdg-open", folder])
    try:
        subprocess.Popen(cmd)
    except OSError as e:
        return {"ok": False, "error": str(e), "folder": folder}
    return {"ok": True, "folder": folder}


@app.websocket("/api/projects/{pid}/ws")
async def ws(websocket: WebSocket, pid: str, after: int = 0):
    p = mgr().get(pid)
    if not p:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def listener(event: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    with p.lock:
        backlog = [e for e in p.events if e["seq"] > after]
        p.listeners.append(listener)
    try:
        for e in backlog:
            await websocket.send_text(json.dumps(e))
        while True:
            e = await queue.get()
            await websocket.send_text(json.dumps(e))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        with p.lock:
            if listener in p.listeners:
                p.listeners.remove(listener)
