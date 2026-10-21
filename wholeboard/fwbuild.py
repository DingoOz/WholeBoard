"""Compile the starter firmware with PlatformIO, and collect what a browser needs to flash it."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

# ESP32 flash layout for the Arduino framework: (file, offset); the bootloader sits at 0x1000 on the
# original ESP32 and ESP32-S2, at 0 on the newer chips.
ESP_BOOT_AT = {"ESP32": 0x1000, "ESP32-S2": 0x1000}
ESP_CHIPS = {"ESP32-S3": "esp32s3", "ESP32-S2": "esp32s2", "ESP32-C3": "esp32c3", "ESP32-C6": "esp32c6",
             "ESP32": "esp32"}


def find(cfg: dict | None = None) -> str | None:
    """The pio program: Settings, PATH, WholeBoard's own Python, or PlatformIO's installer folder."""
    from . import config
    cfg = cfg or config.load()
    configured = (cfg.get("tools") or {}).get("platformio")
    if configured and Path(configured).exists():
        return configured
    for name in ("pio", "platformio"):
        hit = shutil.which(name)
        if hit:
            return hit
    exe = "pio.exe" if platform.system() == "Windows" else "pio"
    for d in (Path(sys.executable).parent, Path.home() / ".platformio" / "penv" / ("Scripts" if platform.system() == "Windows" else "bin")):
        if (d / exe).exists():
            return str(d / exe)
    return None


def version(exe: str) -> str | None:
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60)
        return r.stdout.strip().split("version")[-1].strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def install(log=print) -> str:
    """pip-install PlatformIO into WholeBoard's own Python."""
    log("Installing PlatformIO with pip (its compilers download on the first build)…")
    subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "platformio"], check=True)
    exe = find()
    if not exe:
        raise RuntimeError("PlatformIO installed, but pio was not found")
    return exe


def _artifacts(fw_dir: Path, env: str, family: str) -> dict:
    build = fw_dir / ".pio" / "build" / env
    out: dict = {"files": [], "kind": None}
    if family.startswith("ESP32"):
        chip = ESP_CHIPS.get(family, "esp32")
        parts = [("bootloader.bin", ESP_BOOT_AT.get(family, 0)), ("partitions.bin", 0x8000), ("firmware.bin", 0x10000)]
        boot_app0 = next(iter((Path.home() / ".platformio" / "packages").glob(
            "framework-arduinoespressif32*/tools/partitions/boot_app0.bin")), None)
        if boot_app0:
            parts.insert(2, (str(boot_app0), 0xE000))
        files = []
        for name, offset in parts:
            f = Path(name) if os.path.isabs(name) else build / name
            if f.exists():
                files.append({"path": f, "offset": offset, "name": f.name})
        if any(f["name"] == "firmware.bin" for f in files):
            out.update(kind="esp", chip=chip, files=files)
    elif (build / "firmware.uf2").exists():
        out.update(kind="uf2", files=[{"path": build / "firmware.uf2", "name": "firmware.uf2"}])
    elif (build / "firmware.hex").exists():
        out.update(kind="hex", files=[{"path": build / "firmware.hex", "name": "firmware.hex"}])
    elif (build / "firmware.bin").exists():
        out.update(kind="bin", files=[{"path": build / "firmware.bin", "name": "firmware.bin"}])
    return out


def build(fw_dir: Path, env: str, family: str, log=print, cancel: threading.Event | None = None,
          timeout: float = 1800) -> dict:
    """Run `pio run` in the firmware folder. The first build of a family downloads its compiler (minutes)."""
    exe = find()
    if not exe:
        return {"ok": False, "error": "PlatformIO is not installed. Install it from the firmware card or with: "
                                      "python -m wholeboard.doctor --install platformio"}
    started = time.time()
    log(f"Compiling with PlatformIO ({env}); the first build of a chip family downloads its compiler.")
    proc = subprocess.Popen([exe, "run", "-d", str(fw_dir), "-e", env], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, errors="replace")
    tail: list[str] = []
    assert proc.stdout
    for line in proc.stdout:
        line = line.rstrip()
        tail = (tail + [line])[-60:]
        if line and (line.startswith(("Compiling", "Linking", "Building", "Tool Manager", "Platform Manager",
                                      "RAM:", "Flash:", "Error", "error", "*** ")) or "error:" in line or "SUCCESS" in line):
            log(line[:300])
        if cancel is not None and cancel.is_set():
            proc.kill()
            return {"ok": False, "error": "stopped"}
        if time.time() - started > timeout:
            proc.kill()
            return {"ok": False, "error": f"the build took longer than {timeout / 60:.0f} minutes"}
    code = proc.wait()
    res: dict = {"ok": code == 0, "seconds": round(time.time() - started, 1)}
    usage = [l.strip() for l in tail if l.strip().startswith(("RAM:", "Flash:"))]
    if usage:
        res["usage"] = usage
    if code != 0:
        errs = [l for l in tail if "error" in l.lower()]
        res["error"] = "\n".join((errs or tail)[-12:])
        return res
    res.update(_artifacts(fw_dir, env, family))
    return res
