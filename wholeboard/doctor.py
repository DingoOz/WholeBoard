"""Check WholeBoard's dependencies and install the ones that need no administrator rights.

    python -m wholeboard.doctor                 # show what is installed and what is missing
    python -m wholeboard.doctor --install       # also install TraceMaker, FreeCAD, ngspice and KiCad libraries
    python -m wholeboard.doctor --install tracemaker freecad ngspice libs platformio

The install.sh / install.ps1 scripts install the system packages (Python, KiCad, compilers) and then
run this. Everything installed here goes under ~/.wholeboard/tools.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from . import config, enclosure, ngspice, router
from .kicad import cli, libs

TOOLS = Path(os.environ.get("WHOLEBOARD_HOME", Path.home() / ".wholeboard")) / "tools"
TM_DIR = TOOLS / "tracemaker"
TM_REPO = "DingoOz/TraceMaker"


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "WholeBoard"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _download(url: str, dest: Path, log=print) -> str:
    h = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": "WholeBoard"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
            h.update(chunk)
    return h.hexdigest()


def _glibc() -> tuple[int, int]:
    name, ver = platform.libc_ver()
    try:
        major, minor = ver.split(".")[:2]
        return int(major), int(minor)
    except ValueError:
        return (0, 0)


# ----------------------------------------------------------------------------------- TraceMaker
def install_tracemaker(log=print) -> bool:
    system, machine = platform.system(), platform.machine().lower()
    if system == "Windows":
        return _tracemaker_wsl(log)
    if system == "Linux" and machine in ("x86_64", "amd64") and _glibc() >= (2, 38):
        return _tracemaker_release(log)
    return _tracemaker_source(log)


def _tracemaker_release(log) -> bool:
    rel = _get_json(f"https://api.github.com/repos/{TM_REPO}/releases/latest")
    assets = {a["name"]: a["browser_download_url"] for a in rel["assets"]}
    name = next((n for n in assets if n.endswith("-linux-x86_64.tar.gz") and "pcm" not in n), None)
    if not name:
        log(f"TraceMaker {rel['tag_name']} has no Linux download; building from source instead.")
        return _tracemaker_source(log)
    TM_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tar = Path(tmp) / name
        log(f"Downloading {name}…")
        digest = _download(assets[name], tar, log)
        if "SHA256SUMS" in assets:
            sums_file = Path(tmp) / "SHA256SUMS"
            _download(assets["SHA256SUMS"], sums_file)
            sums = dict(reversed(line.split()) for line in sums_file.read_text().splitlines() if line.strip())
            if sums.get(name) != digest:
                raise RuntimeError("The TraceMaker download is corrupt (checksum mismatch); try again.")
            log("Checksum verified.")
        with tarfile.open(tar) as t:
            try:
                t.extractall(TM_DIR, filter="data")
            except TypeError:
                t.extractall(TM_DIR)  # noqa: S202 - Python < 3.12 has no filter; the checksum was verified
    folder = TM_DIR / name.removesuffix(".tar.gz")
    current = TM_DIR / "current"
    if current.is_symlink() or current.exists():
        current.unlink() if current.is_symlink() else shutil.rmtree(current)
    current.symlink_to(folder.name)
    log(f"TraceMaker installed in {folder}")
    return True


def _tracemaker_source(log) -> bool:
    for tool in ("git", "cmake"):
        if not shutil.which(tool):
            raise RuntimeError(f"Building TraceMaker needs {tool}. Run install.sh, which installs the build tools.")
    src = TM_DIR / "src"
    TM_DIR.mkdir(parents=True, exist_ok=True)
    if (src / ".git").exists():
        log("Updating the TraceMaker source…")
        subprocess.run(["git", "-C", str(src), "pull", "--ff-only"], check=False)
    else:
        log("Downloading the TraceMaker source…")
        subprocess.run(["git", "clone", "--depth", "1", f"https://github.com/{TM_REPO}.git", str(src)], check=True)
    if platform.system() == "Darwin":
        preset = "macos-metal" if platform.machine() == "arm64" else "macos-cpu"
        prefix = subprocess.run(["brew", "--prefix"], capture_output=True, text=True).stdout.strip()
        configure = ["cmake", "--preset", preset, f"-DCMAKE_PREFIX_PATH={prefix}"]
    else:
        preset = "release" if shutil.which("nvcc") else "cpu-only"
        configure = ["cmake", "--preset", preset]
    log(f"Building TraceMaker ({preset}); this takes several minutes…")
    subprocess.run(configure, cwd=src, check=True)
    subprocess.run(["cmake", "--build", "--preset", preset, "--target", "tracemaker", "tracemaker-place"],
                   cwd=src, check=True)
    if shutil.which("npm") and (src / "viewer" / "package.json").exists():
        log("Building TraceMaker's live viewer…")
        subprocess.run("npm install && npm run build", shell=True, cwd=src / "viewer", check=False)
    log("TraceMaker built.")
    return True


def _tracemaker_wsl(log) -> bool:
    """Windows: TraceMaker runs inside WSL. Install the Linux release there and point WholeBoard at it."""
    if not shutil.which("wsl"):
        raise RuntimeError("TraceMaker needs WSL on Windows. In an administrator PowerShell run "
                           "'wsl --install -d Ubuntu-24.04', restart, then run install.bat again.")
    script = (
        "set -e; mkdir -p ~/.local/share/tracemaker ~/.local/bin; cd ~/.local/share/tracemaker; "
        f"url=$(curl -s https://api.github.com/repos/{TM_REPO}/releases/latest | "
        "grep -o 'https://[^\"]*linux-x86_64.tar.gz' | grep -v pcm | head -1); "
        "curl -sL \"$url\" -o tm.tar.gz && tar xzf tm.tar.gz && rm tm.tar.gz; "
        "d=$(ls -d tracemaker-*-linux-x86_64 | sort -V | tail -1); "
        "ln -sf \"$PWD/$d/bin/tracemaker\" ~/.local/bin/tracemaker; "
        "ln -sf \"$PWD/$d/bin/tracemaker-place\" ~/.local/bin/tracemaker-place; "
        "echo installed $d")
    log("Installing TraceMaker inside WSL…")
    r = subprocess.run(["wsl", "--", "bash", "-lc", script], capture_output=True, text=True)
    log((r.stdout + r.stderr).strip())
    if r.returncode != 0:
        raise RuntimeError("Installing TraceMaker in WSL failed (see above).")
    home = subprocess.run(["wsl", "--", "bash", "-lc", "echo $HOME"], capture_output=True, text=True).stdout.strip()
    cfg = config.load()
    cfg["tools"].update({"tracemaker_wsl": True, "tracemaker": f"{home}/.local/bin/tracemaker",
                         "tracemaker_place": f"{home}/.local/bin/tracemaker-place"})
    config.save(cfg)
    return True


# ----------------------------------------------------------------------------------- ngspice
NGSPICE_VERSIONS = ["45.2", "45", "44.2"]


def install_ngspice(log=print) -> bool:
    """macOS: Homebrew. Windows: the official build into ~/.wholeboard/tools/ngspice. Linux: the
    distribution's package (needs sudo, so this only says what to run)."""
    system = platform.system()
    if system == "Darwin":
        if not shutil.which("brew"):
            raise RuntimeError("Install Homebrew (brew.sh), then run: brew install ngspice")
        log("Installing ngspice with Homebrew…")
        subprocess.run(["brew", "install", "ngspice"], check=True)
        return True
    if system == "Windows":
        dest = TOOLS / "ngspice"
        dest.mkdir(parents=True, exist_ok=True)
        for v in NGSPICE_VERSIONS:
            url = f"https://sourceforge.net/projects/ngspice/files/ng-spice-rework/{v}/ngspice-{v}_64.7z/download"
            arc = dest / f"ngspice-{v}_64.7z"
            try:
                log(f"Downloading ngspice {v}…")
                _download(url, arc)
            except OSError as e:
                log(f"  not available ({e})")
                continue
            # Windows 10+ ships bsdtar, which reads 7z archives.
            r = subprocess.run(["tar", "-xf", str(arc), "-C", str(dest)], capture_output=True, text=True)
            arc.unlink(missing_ok=True)
            if r.returncode == 0 and (dest / "Spice64" / "bin" / "ngspice_con.exe").exists():
                cfg = config.load()
                cfg["tools"]["ngspice"] = str(dest / "Spice64" / "bin" / "ngspice_con.exe")
                config.save(cfg)
                log(f"ngspice {v} installed in {dest}")
                return True
            log(f"  could not unpack it: {r.stderr.strip()[:200]}")
        raise RuntimeError("Could not download ngspice. KiCad's own copy is used when KiCad is installed.")
    for mgr, cmd in (("apt-get", "sudo apt-get install -y ngspice"), ("dnf", "sudo dnf install -y ngspice"),
                     ("pacman", "sudo pacman -S --needed ngspice"), ("zypper", "sudo zypper install -y ngspice")):
        if shutil.which(mgr):
            raise RuntimeError(f"Install it with: {cmd}   (install.sh does this)")
    raise RuntimeError("Install ngspice with your package manager.")


# ----------------------------------------------------------------------------------- report
def check() -> list[tuple[str, bool, str, str]]:
    """(name, ok, detail, how to fix) for every dependency."""
    cfg = config.load()
    rows = []
    py_ok = sys.version_info >= (3, 10)
    rows.append(("Python", py_ok, platform.python_version(), "Install Python 3.10 or newer."))
    kc = cli.find_kicad_cli(cfg)
    kv = cli.version() if kc else None
    rows.append(("KiCad", bool(kv), f"{kv} · {kc}" if kv else "kicad-cli not found",
                 "Install KiCad 9 or 10 (install.sh / install.bat do this)."))
    st = libs.status(cfg)
    rows.append(("KiCad libraries", st["footprints"] > 0,
                 f"{st['footprints']} footprints" if st["footprints"] else "not found",
                 "python -m wholeboard.doctor --install libs"))
    tm = router.find("tracemaker", cfg)
    tv = router.version(cfg) if tm else None
    rows.append(("TraceMaker", bool(tv), f"{tv} · {tm}" if tv else "not found",
                 "python -m wholeboard.doctor --install tracemaker"))
    fc = enclosure.find_freecad(cfg)
    fv = enclosure.freecad_version(fc) if fc else None
    rows.append(("FreeCAD", bool(fv), f"{fv} · {fc[0]}" if fv else "not found (needed for enclosures)",
                 "python -m wholeboard.doctor --install freecad"))
    ng = ngspice.find(cfg)
    nv = ngspice.version(ng) if ng else None
    rows.append(("ngspice", bool(nv), f"{nv} · {ngspice.label(ng)}" if nv else "not found (needed for simulation)",
                 "python -m wholeboard.doctor --install ngspice"))
    from . import models3d
    m3 = models3d.mode(cfg)
    dirs = models3d._system_dirs()
    cached = sum(1 for _ in models3d.CACHE.rglob("*.step")) if models3d.CACHE.is_dir() else 0
    rows.append(("3D part models", m3 != "off" and (bool(dirs) or m3 == "auto"),
                 "off" if m3 == "off" else (f"KiCad models in {dirs[0]}" if dirs else "downloaded when a board needs them")
                 + (f", {cached} cached" if cached else ""),
                 "Turn them on in Settings → KiCad & TraceMaker → 3D part models."))
    from . import fwbuild
    pio = fwbuild.find(cfg)
    pv = fwbuild.version(pio) if pio else None
    rows.append(("PlatformIO", bool(pv), f"PlatformIO {pv}" if pv else "not found (optional: compiles the starter firmware)",
                 "python -m wholeboard.doctor --install platformio"))
    ai = cfg["ai"]
    if ai["provider"] == "anthropic":
        ok = bool(ai["anthropic_api_key"] or os.environ.get("ANTHROPIC_API_KEY"))
        detail = f"Claude {ai['anthropic_model']}" + ("" if ok else ": no API key yet")
    else:
        ok, detail = bool(ai["openai_base_url"]), f"{ai['openai_model']} @ {ai['openai_base_url']}"
    rows.append(("AI", ok, detail, "Open Settings in WholeBoard and follow 'How to get an API key'."))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m wholeboard.doctor", description=__doc__.split("\n")[0])
    ap.add_argument("--install", nargs="*", metavar="WHAT",
                    help="install missing tools: tracemaker, freecad, ngspice, libs, platformio "
                         "(default: all but platformio)")
    ap.add_argument("--force", action="store_true", help="reinstall even if found")
    args = ap.parse_args(argv)

    failed = False
    if args.install is not None:
        rows = {name: ok for name, ok, _, _ in check()}
        wanted = set(args.install or ["tracemaker", "freecad", "ngspice", "libs"])
        jobs = [("tracemaker", "TraceMaker", install_tracemaker),
                ("freecad", "FreeCAD", lambda log: bool(enclosure.fetch_freecad(log))),
                ("ngspice", "ngspice", install_ngspice),
                ("libs", "KiCad libraries", lambda log: libs.fetch_from_gitlab(log=log)),
                ("platformio", "PlatformIO", lambda log: bool(__import__("wholeboard.fwbuild").fwbuild.install(log)))]
        for key, label, fn in jobs:
            if key not in wanted:
                continue
            if rows.get(label) and not args.force:
                print(f"✓ {label} is already installed")
                continue
            print(f"→ Installing {label}")
            try:
                fn(lambda s: print("   " + str(s)))
            except Exception as e:  # noqa: BLE001
                failed = True
                print(f"✗ {label}: {e}")
        print()
    rows = check()
    width = max(len(r[0]) for r in rows)
    print("WholeBoard dependencies")
    for name, ok, detail, fix in rows:
        print(f"  {'✓' if ok else '✗'} {name.ljust(width)}  {detail}")
        if not ok:
            print(f"    {' ' * width}  fix: {fix}")
    missing = [r[0] for r in rows if not r[1] and r[0] not in ("AI", "PlatformIO")]
    if args.install is not None and failed:
        return 2
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
