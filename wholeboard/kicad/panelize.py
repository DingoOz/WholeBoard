"""Panels drawn with KiKit: copies of the board on rails, ready to order as one panel.

V-cut for straight-edged boards (the copies touch, cut lines on the board edges), tab routing with mouse bites for
rounded or odd outlines (a 2 mm milled gap, 3 mm tabs perforated with 0.5 mm holes). 5 mm rails top and bottom
carry three 1.152 mm tooling holes (JLCPCB's size) and three fiducials (1 mm copper, 2 mm mask opening) for the
assembly machine. Each copy's parts are renamed R1_0, R1_1 ... so the panel's CPL and BOM name every part once.

KiKit (MIT licence) runs on KiCad's own Python. WholeBoard uses a `kikit` command on the PATH, or, when kicad-cli
is the Docker image, KiKit's packages in ~/.wholeboard/kikit-py run inside that image
(python -m wholeboard.doctor --install kikit puts them there).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

PY_DIR = Path.home() / ".wholeboard" / "kikit-py"
IMAGE = os.environ.get("TRACEMAKER_KICAD_IMAGE", "kicad/kicad:10.0.6")
RAIL_MM = 5.0
GAP_MM = 2.0
TOOLING_MM = 1.152
VCUT_LAYER = "Cmts.User"


class PanelError(RuntimeError):
    pass


def _docker() -> list[str] | None:
    if not shutil.which("docker") or not (PY_DIR / "kikit").is_dir():
        return None
    return ["docker", "run", "--rm", "-u", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp",
            "-e", f"PYTHONPATH={PY_DIR}", "-v", f"{Path.home()}:{Path.home()}", IMAGE, "python3", "-m", "kikit.ui"]


def command() -> list[str] | None:
    """How to run KiKit here, or None."""
    exe = shutil.which("kikit")
    if exe:
        return [exe]
    return _docker() if hasattr(os, "getuid") else None


def available() -> bool:
    return command() is not None


def install(log=print) -> bool:
    """Install KiKit where WholeBoard can run it."""
    cli = shutil.which("kicad-cli") or ""
    docker = cli and "docker" in Path(cli).read_text(errors="ignore")[:2000] if cli and Path(cli).is_file() else False
    if docker:
        r = subprocess.run(["docker", "run", "--rm", IMAGE, "python3", "-c",
                            "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
                           capture_output=True, text=True, timeout=300)
        ver = r.stdout.strip().splitlines()[-1] if r.returncode == 0 and r.stdout.strip() else "3.13"
        uv = shutil.which("uv")
        PY_DIR.mkdir(parents=True, exist_ok=True)
        if uv:
            cmd = [uv, "pip", "install", "--target", str(PY_DIR), "--python-version", ver,
                   "--python-platform", "x86_64-manylinux_2_28", "kikit"]
        else:
            cmd = ["python3", "-m", "pip", "install", "--target", str(PY_DIR), "--python-version", ver,
                   "--platform", "manylinux_2_28_x86_64", "--only-binary=:all:", "kikit"]
        log(f"Installing KiKit for the KiCad image's Python {ver} into {PY_DIR}")
        subprocess.run(cmd, check=True)
        return True
    # a native KiCad: install into the Python that has pcbnew
    for py in ("python3", "python"):
        exe = shutil.which(py)
        if exe and subprocess.run([exe, "-c", "import pcbnew"], capture_output=True).returncode == 0:
            subprocess.run([exe, "-m", "pip", "install", "--user", "kikit"], check=True)
            return True
    raise PanelError("KiKit needs KiCad's Python (the one that can 'import pcbnew'). On Windows and macOS install "
                     "it from KiCad's Plugin and Content Manager, then make sure 'kikit' is on the PATH.")


def args(nx: int, ny: int, vcut: bool, fiducials: bool = True, tooling: bool = True) -> list[str]:
    space = 0 if vcut else GAP_MM
    out = ["--layout", f"grid; rows: {ny}; cols: {nx}; space: {space}mm; renameref: {{orig}}_{{n}}",
           "--framing", f"railstb; width: {RAIL_MM}mm; space: {GAP_MM}mm"]
    if vcut:
        # V-cut lines on a drawing layer (exported with the Gerbers), never on Edge.Cuts, which is milled
        out += ["--tabs", "full", "--cuts", f"vcuts; clearance: 0.4mm; layer: {VCUT_LAYER}"]
    else:
        out += ["--tabs", "fixed; width: 3mm; vcount: 2; hcount: 2",
                "--cuts", "mousebites; drill: 0.5mm; spacing: 0.8mm; offset: 0.25mm; prolong: 0.5mm"]
    if tooling:
        out += ["--tooling", f"3hole; hoffset: 2.5mm; voffset: {RAIL_MM / 2}mm; size: {TOOLING_MM}mm"]
    if fiducials:
        out += ["--fiducials", f"3fid; hoffset: 6mm; voffset: {RAIL_MM / 2}mm; coppersize: 1mm; opening: 2mm"]
    out += ["--post", "millradius: 0.5mm"]           # a 1 mm tool: JLCPCB mills 1 mm slots
    return out


def make(pcb: Path, out: Path, nx: int, ny: int, vcut: bool, log=print, **kw) -> Path:
    """Draw the panel: out is the panel's .kicad_pcb."""
    cmd = command()
    if not cmd:
        raise PanelError("KiKit is not installed: run python -m wholeboard.doctor --install kikit")
    out.parent.mkdir(parents=True, exist_ok=True)
    full = [*cmd, "panelize", *args(nx, ny, vcut, **kw), str(pcb), str(out)]
    r = subprocess.run(full, capture_output=True, text=True, timeout=900, cwd=pcb.parent)
    noise = re.compile(r"Debug: Adding duplicate image handler|^\s*$")
    for line in (r.stdout + r.stderr).splitlines():
        if not noise.search(line):
            log(line[:300])
    if r.returncode != 0 or not out.exists():
        raise PanelError(f"KiKit failed ({r.returncode}): {r.stderr.strip()[-500:]}")
    for junk in out.parent.glob("~*.lck"):
        junk.unlink(missing_ok=True)
    return out


def copies(ref: str, n: int) -> list[str]:
    return [f"{ref}_{i}" for i in range(n)]          # KiKit counts the copies from 0
