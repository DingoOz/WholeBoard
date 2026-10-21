"""Run TraceMaker's placer and router.

TraceMaker builds natively on Linux and macOS. On Windows it runs inside WSL:
set tools.tracemaker_wsl = true and give the Linux path of the binary.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import threading
from pathlib import Path, PureWindowsPath

from . import config

_SIBLING = Path(__file__).resolve().parents[2] / "TraceMaker" / "build" / "release" / "src"


def _candidates(name: str) -> list[Path]:
    sub = "app" if name == "tracemaker" else "place"
    out = [_SIBLING / sub / name,
           Path.home() / "TraceMaker" / "build" / "release" / "src" / sub / name,
           Path("/opt/tracemaker/bin") / name, Path("/usr/local/bin") / name]
    # Installed by `python -m wholeboard.doctor --install tracemaker`.
    tools = Path(os.environ.get("WHOLEBOARD_HOME", Path.home() / ".wholeboard")) / "tools" / "tracemaker"
    out.append(tools / "current" / "bin" / name)
    for preset in ("release", "cpu-only", "macos-metal", "macos-cpu"):
        out.append(tools / "src" / "build" / preset / "src" / sub / name)
    if platform.system() == "Darwin":
        for preset in ("macos-metal", "macos-cpu"):
            out.append(_SIBLING.parents[1] / preset / "src" / sub / name)
    return out


def find(name: str, cfg: dict | None = None) -> str | None:
    """name: 'tracemaker' or 'tracemaker-place'."""
    cfg = cfg or config.load()
    key = "tracemaker" if name == "tracemaker" else "tracemaker_place"
    configured = cfg["tools"].get(key)
    if cfg["tools"].get("tracemaker_wsl"):
        return configured or name
    if configured and Path(configured).exists():
        return configured
    if configured and name == "tracemaker-place":
        sib = Path(configured).parent.parent / "place" / name
        if sib.exists():
            return str(sib)
    found = shutil.which(name)
    if found:
        return found
    return next((str(p) for p in _candidates(name) if p.exists()), None)


def _wsl_path(p: str | Path) -> str:
    s = str(p)
    if re.match(r"^[A-Za-z]:\\", s):
        w = PureWindowsPath(s)
        return f"/mnt/{w.drive[0].lower()}/" + "/".join(w.parts[1:])
    return s.replace("\\", "/")


def _cmd(exe: str, args: list[str], cfg: dict) -> list[str]:
    if cfg["tools"].get("tracemaker_wsl"):
        conv = [_wsl_path(a) if (":" in a and "\\" in a) else a for a in args]
        return ["wsl", "--", exe, *conv]
    return [exe, *args]


def version(cfg: dict | None = None) -> str | None:
    cfg = cfg or config.load()
    exe = find("tracemaker", cfg)
    if not exe:
        return None
    try:
        r = subprocess.run(_cmd(exe, ["version"], cfg), capture_output=True, text=True, timeout=30)
        return (r.stdout or r.stderr).strip().splitlines()[0] if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return None


def _stream(cmd: list[str], log, cancel: threading.Event | None, timeout: float) -> int:
    env = dict(os.environ)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            bufsize=1, env=env)
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    try:
        assert proc.stdout
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                log(line)
            if cancel is not None and cancel.is_set():
                proc.kill()
                break
        return proc.wait()
    finally:
        timer.cancel()


def place(pcb_in: Path, pcb_out: Path, log=print, cancel=None, mode: str = "routable",
          scratch: bool = True, seconds: float | None = None) -> dict:
    cfg = config.load()
    exe = find("tracemaker-place", cfg)
    if not exe:
        raise RuntimeError("tracemaker-place not found. Build TraceMaker or set its path in Settings.")
    t = seconds or cfg["tools"].get("place_time_s", 60)
    args = [str(pcb_in), "-o", str(pcb_out), "--mode", mode, "--time", str(t)]
    if scratch:
        args.append("--scratch")
    _copy_project(pcb_in, pcb_out)
    # routable mode trial-routes each candidate placement; on some footprints (a module whose thermal vias sit under
    # its exposed pad) those trials never succeed and it overruns its time, so it gets a tighter limit and a fallback
    code = _stream(_cmd(exe, args, cfg), log, cancel, timeout=t * (3 if mode == "routable" else 6) + 120)
    if (code != 0 or not pcb_out.exists()) and mode == "routable" and not (cancel and cancel.is_set()):
        log(f"Routability-checked placement did not finish (exit code {code}); placing without the routing check.")
        pcb_out.unlink(missing_ok=True)
        args[args.index("--mode") + 1] = "full"
        code = _stream(_cmd(exe, args, cfg), log, cancel, timeout=t * 6 + 120)
    if code != 0 or not pcb_out.exists():
        raise RuntimeError(f"tracemaker-place exited with code {code}")
    return {"ok": True}


_VIEW_TOKEN: str | None = None


def lan_mode() -> bool:
    """True when WholeBoard listens on the network rather than this machine only."""
    host = os.environ.get("WHOLEBOARD_HOST", "127.0.0.1")
    return host not in ("127.0.0.1", "localhost", "::1")


def viewer_token() -> str | None:
    """Token for TraceMaker's live viewer when other machines must reach it, else None."""
    global _VIEW_TOKEN
    if not lan_mode():
        return None
    if _VIEW_TOKEN is None:
        import secrets
        _VIEW_TOKEN = secrets.token_hex(16)
    return _VIEW_TOKEN


def route(pcb_in: Path, pcb_out: Path, log=print, cancel=None, seconds: float | None = None,
          view_port: int | None = None, embed_origin: str | None = None,
          reroute: bool = False, diff_pairs: bool = False) -> dict:
    cfg = config.load()
    exe = find("tracemaker", cfg)
    if not exe:
        raise RuntimeError("tracemaker not found. Build TraceMaker or set its path in Settings.")
    t = seconds or cfg["tools"].get("route_time_s", 120)
    summary = pcb_out.with_suffix(".route.json")
    stuck = pcb_out.with_suffix(".stuck.json")
    args = [str(pcb_in), "-o", str(pcb_out), "--time", str(t), "--json", str(summary),
            "--stuck-report", str(stuck)]
    if reroute:
        args.append("--reroute")
    if diff_pairs:
        args.append("--diff-pairs")
    if view_port:
        args += ["--view", "--no-freeze", "--view-port", str(view_port)]
        token = viewer_token()
        if token and embed_origin:
            # Other machines open WholeBoard by this machine's address, whichever one they used.
            port = embed_origin.rsplit(":", 1)[-1]
            if port.isdigit():
                embed_origin += f" http://*:{port}"
        if embed_origin:
            args += ["--view-embed", embed_origin]
        if token:
            args += ["--view-host", "0.0.0.0", "--view-token", token]
    _copy_project(pcb_in, pcb_out)
    code = _stream(_cmd(exe, ["route", *args], cfg), log, cancel, timeout=t * 10 + 300)
    if not pcb_out.exists():
        raise RuntimeError(f"tracemaker route exited with code {code} and wrote no board")
    data = json.loads(summary.read_text()) if summary.exists() else {}
    failures = data.get("failures") or []
    return {
        "routed": data.get("routed"), "connections": data.get("connections"),
        "tracks": data.get("tracks"), "vias": (data.get("style") or {}).get("vias"),
        "seconds": data.get("seconds"), "variant": data.get("variant_name"),
        "complete": data.get("routed") is not None and data.get("routed") == data.get("connections"),
        "failures": failures[:30],
    }


def _copy_project(pcb_in: Path, pcb_out: Path) -> None:
    """Keep the .kicad_pro (net classes, rules) beside every board copy."""
    src = pcb_in.with_suffix(".kicad_pro")
    dst = pcb_out.with_suffix(".kicad_pro")
    if src.exists() and src != dst:
        shutil.copyfile(src, dst)


def viewer_available(cfg: dict | None = None) -> bool:
    cfg = cfg or config.load()
    exe = find("tracemaker", cfg)
    if not exe or cfg["tools"].get("tracemaker_wsl"):
        return False
    if os.environ.get("TM_VIEW_DIR"):
        return True
    p = Path(exe).resolve()
    if (p.parent.parent / "share" / "tracemaker" / "viewer").exists():
        return True
    for parent in p.parents:
        if (parent / "viewer" / "dist" / "index.html").exists():
            return True
    return False
