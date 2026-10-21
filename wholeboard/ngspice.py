"""Find and run ngspice, the circuit simulator, and read its results.

ngspice is used in one of three ways, whichever is found first:
  - the ngspice program (installed by install.sh / Homebrew / the official Windows build),
  - the ngspice shared library that KiCad bundles for its own simulator (run in a child
    process through ngspice_shared.py, so a crash cannot take WholeBoard down),
  - the ngspice inside the Docker image when kicad-cli itself is a Docker wrapper.

Netlists carry their own .control block that runs the analyses and writes ASCII raw files,
so every engine is driven the same way: run the netlist in its folder, then read the files.
"""

from __future__ import annotations

import glob
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import config


class SpiceError(RuntimeError):
    pass


def _exe_candidates() -> list[str]:
    system = platform.system()
    if system == "Windows":
        roots = [os.environ.get("ProgramFiles", r"C:\Program Files"), "C:\\"]
        return [str(Path(r) / "Spice64" / "bin" / n) for r in roots for n in ("ngspice_con.exe", "ngspice.exe")] + \
            [str(config.APP_DIR / "tools" / "ngspice" / "Spice64" / "bin" / "ngspice_con.exe")]
    if system == "Darwin":
        return ["/opt/homebrew/bin/ngspice", "/usr/local/bin/ngspice"]
    return ["/usr/bin/ngspice", "/usr/local/bin/ngspice"]


def _lib_candidates() -> list[str]:
    system = platform.system()
    if system == "Windows":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pats = [rf"{pf}\KiCad\*\bin\ngspice.dll", rf"{pf}\KiCad\*\bin\libngspice-0.dll"]
    elif system == "Darwin":
        pats = ["/Applications/KiCad/KiCad.app/Contents/Frameworks/libngspice*.dylib",
                "/Applications/KiCad/KiCad.app/Contents/PlugIns/sim/libngspice*.dylib",
                "/opt/homebrew/lib/libngspice*.dylib", "/usr/local/lib/libngspice*.dylib"]
    else:
        pats = ["/usr/lib/x86_64-linux-gnu/libngspice.so*", "/usr/lib/aarch64-linux-gnu/libngspice.so*",
                "/usr/lib64/libngspice.so*", "/usr/lib/libngspice.so*", "/usr/local/lib/libngspice.so*"]
    out = []
    for pat in pats:
        out += sorted(glob.glob(pat), reverse=True)
    return out


def _docker_image(cfg: dict) -> str | None:
    """The KiCad image a Docker kicad-cli wrapper uses (its images include ngspice)."""
    from .kicad import cli
    kc = cli.find_kicad_cli(cfg)
    if not kc or not shutil.which("docker"):
        return None
    try:
        text = Path(kc).read_text(errors="ignore")[:20000]
    except OSError:
        return None
    if "docker" not in text:
        return None
    m = re.search(r"kicad/kicad:[\w.\-]+", text)
    return os.environ.get("TRACEMAKER_KICAD_IMAGE") or (m.group(0) if m else None)


def find(cfg: dict | None = None) -> dict | None:
    """{"kind": "exe" | "lib" | "docker", "path": ...} or None."""
    cfg = cfg or config.load()
    configured = (cfg["tools"].get("ngspice") or "").strip()
    if configured and Path(configured).exists():
        kind = "lib" if re.search(r"\.(so|dylib|dll)(\.\d+)*$", configured) and "ngspice_con" not in configured else "exe"
        return {"kind": kind, "path": configured}
    exe = shutil.which("ngspice_con") or shutil.which("ngspice")
    if exe:
        return {"kind": "exe", "path": exe}
    for c in _exe_candidates():
        if Path(c).exists():
            return {"kind": "exe", "path": c}
    for c in _lib_candidates():
        if Path(c).exists():
            return {"kind": "lib", "path": c}
    img = _docker_image(cfg)
    if img:
        return {"kind": "docker", "path": img}
    return None


_version_cache: dict[str, str | None] = {}


def version(engine: dict | None) -> str | None:
    if not engine:
        return None
    key = f"{engine['kind']}:{engine['path']}"
    if key in _version_cache:
        return _version_cache[key]
    v = None
    try:
        if engine["kind"] == "exe":
            r = subprocess.run([engine["path"], "--version"], capture_output=True, text=True, timeout=30)
            m = re.search(r"ngspice-(\S+)", r.stdout + r.stderr)
            v = m.group(1) if m else "found"
        elif engine["kind"] == "docker":
            r = subprocess.run(_docker_cmd(engine["path"], Path.home(), ["ngspice", "--version"]),
                               capture_output=True, text=True, timeout=120)
            m = re.search(r"ngspice-(\S+)", r.stdout + r.stderr)
            v = m.group(1) + " (Docker)" if m else None
        else:
            v = "shared library"
    except (OSError, subprocess.TimeoutExpired):
        v = None
    _version_cache[key] = v
    return v


def label(engine: dict | None) -> str:
    if not engine:
        return "not found"
    return {"exe": "ngspice", "lib": "KiCad's ngspice library", "docker": "ngspice in Docker"}[engine["kind"]] \
        + f" · {engine['path']}"


def _docker_cmd(image: str, folder: Path, args: list[str]) -> list[str]:
    folder = folder.resolve()
    mounts = ["-v", f"{folder}:{folder}"]
    uid = [] if platform.system() == "Windows" else ["-u", f"{os.getuid()}:{os.getgid()}"]
    cmd = ["docker", "run", "--rm", *uid, "-e", "HOME=/tmp", *mounts, "-w", str(folder), image, *args]
    if platform.system() == "Linux":
        try:
            ok = subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        if not ok and shutil.which("sg"):   # the shell predates joining the docker group
            import shlex
            return ["sg", "docker", "-c", " ".join(shlex.quote(c) for c in cmd)]
    return cmd


def run(netlist: Path, engine: dict, timeout: float = 120, cancel: threading.Event | None = None) -> str:
    """Run a netlist (with its own .control block) in its folder. Returns ngspice's output."""
    folder = netlist.parent
    if engine["kind"] == "exe":
        cmd = [engine["path"], "-b", netlist.name]
    elif engine["kind"] == "lib":
        cmd = [sys.executable, "-m", "wholeboard.ngspice_shared", engine["path"], netlist.name]
    else:
        cmd = _docker_cmd(engine["path"], folder, ["ngspice", "-b", netlist.name])
    env = dict(os.environ)
    if engine["kind"] == "lib":
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        proc = subprocess.Popen(cmd, cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace", env=env)
    except OSError as e:
        raise SpiceError(f"Could not start ngspice: {e}") from e
    out: list[str] = []
    reader = threading.Thread(target=lambda: out.extend(proc.stdout), daemon=True)
    reader.start()
    start = time.time()
    while proc.poll() is None:
        if cancel is not None and cancel.is_set():
            proc.kill()
            raise SpiceError("cancelled")
        if time.time() - start > timeout:
            proc.kill()
            raise SpiceError(f"ngspice took longer than {timeout:.0f} s; shorten the simulated time")
        time.sleep(0.05)
    reader.join(timeout=5)
    return "".join(out)


def errors_in(output: str) -> list[str]:
    """Lines of ngspice output that report real problems."""
    keep = []
    for line in output.splitlines():
        low = line.lower()
        if ("error" in low or "singular matrix" in low or "timestep too small" in low
                or "no convergence" in low) and "warning" not in low and "errors: 0" not in low:
            keep.append(line.strip())
    return keep[:20]


# ------------------------------------------------------------------------------- raw files
def read_raw(path: Path) -> dict:
    """Read an ASCII raw file: {"plot": name, "vars": [names], "data": {name: [values]}}.

    AC results keep complex values as (re, im) tuples."""
    text = path.read_text(errors="replace")
    head, _, body = text.partition("Values:")
    names: list[str] = []
    plot, flags, in_vars = "", "real", False
    for line in head.splitlines():
        if line.startswith("Plotname:"):
            plot = line.split(":", 1)[1].strip()
        elif line.startswith("Flags:"):
            flags = line.split(":", 1)[1].strip()
        elif line.startswith("Variables:"):
            in_vars = True
        elif in_vars and line.strip():
            parts = line.split()
            if len(parts) >= 2:
                names.append(parts[1].lower())
    n = len(names)
    data: dict[str, list] = {k: [] for k in names}
    tokens = body.split()
    i = 0
    cplx = "complex" in flags
    while i + n < len(tokens) + 1 and n:
        row = tokens[i + 1:i + 1 + n]
        if len(row) < n:
            break
        for k, tok in zip(names, row):
            if cplx:
                re_, _, im = tok.partition(",")
                data[k].append((float(re_), float(im or 0)))
            else:
                data[k].append(float(tok.split(",")[0]))
        i += 1 + n
    return {"plot": plot, "vars": names, "data": data}
