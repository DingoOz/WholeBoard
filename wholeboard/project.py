"""Projects on disk: state, an append-only event log, and live listeners."""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from . import config

STEPS = [
    ("brief", "Requirements"),
    ("parts", "Parts & architecture"),
    ("netlist", "Design check"),
    ("schematic", "Schematic & ERC"),
    ("sim", "Simulation"),
    ("layout", "Board & footprints"),
    ("place", "Placement"),
    ("route", "Routing"),
    ("drc", "DRC & copper pour"),
    ("outputs", "3D views & fab files"),
    ("quote", "JLCPCB quote"),
    ("review", "AI visual review"),
    ("enclosure", "3D-printed enclosure"),
]


# One-word labels for the step bar (the full titles are the tooltips).
SHORT = {"brief": "Request", "parts": "Parts", "netlist": "Check", "schematic": "Schematic", "sim": "Simulate",
         "layout": "Board", "place": "Place", "route": "Route", "drc": "DRC", "outputs": "Outputs",
         "quote": "Quote", "review": "Review", "enclosure": "Enclosure"}


class Project:
    def __init__(self, root: Path, state: dict):
        self.root = root
        self.state = state
        self.events: list[dict] = []
        self.listeners: list[Callable[[dict], None]] = []
        self.lock = threading.RLock()
        # runtime only
        self.session = None
        self.worker: threading.Thread | None = None
        self.fw_thread: threading.Thread | None = None
        self.simlab_lock = threading.Lock()
        self.cancel = threading.Event()
        self.answers: dict[str, str] = {}
        self.answer_ready = threading.Condition(self.lock)
        self.inbox: list[str] = []

    @property
    def id(self) -> str:
        return self.state["id"]

    # -------------------------------------------------------------- persistence
    def save(self) -> None:
        with self.lock:
            tmp = self.root / "state.json.tmp"
            tmp.write_text(json.dumps(self.state, indent=1))
            tmp.replace(self.root / "state.json")

    def emit(self, event: dict) -> dict:
        with self.lock:
            event = {**event, "seq": len(self.events) + 1, "t": time.time()}
            self.events.append(event)
            with open(self.root / "events.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(event) + "\n")
            listeners = list(self.listeners)
        for fn in listeners:
            try:
                fn(event)
            except Exception:  # noqa: BLE001 - a dead socket must not stop the work
                pass
        return event

    # -------------------------------------------------------------- helpers
    def set_status(self, status: str, note: str = "") -> None:
        with self.lock:
            self.state["status"] = status
            self.state["status_note"] = note
            self.state["updated"] = time.time()
            self.save()
        self.emit({"type": "status", "status": status, "note": note})

    def step(self, key: str, status: str, detail: str = "") -> None:
        with self.lock:
            self.state["steps"][key] = {"status": status, "detail": detail}
            self.save()
        self.emit({"type": "step", "key": key, "status": status, "detail": detail})

    def chat(self, role: str, text: str) -> None:
        self.emit({"type": "chat", "role": role, "text": text})

    def log(self, step: str, text: str) -> None:
        self.emit({"type": "log", "step": step, "text": text})

    def visual(self, step: str, kind: str, title: str, **data) -> None:
        with self.lock:
            self.state.setdefault("visuals", {})[f"{step}:{kind}"] = {"title": title, **data}
            self.save()
        self.emit({"type": "visual", "step": step, "kind": kind, "title": title, **data})

    def file_url(self, path: Path) -> str:
        rel = path.resolve().relative_to(self.root.resolve()).as_posix()
        return f"/api/projects/{self.id}/files/{rel}?v={int(time.time() * 1000)}"

    def tail_url(self, path: Path) -> str:
        """For a file a tool is still appending lines to (read with the server's tail endpoint)."""
        rel = path.resolve().relative_to(self.root.resolve()).as_posix()
        return f"/api/projects/{self.id}/tail/{rel}"

    def display_title(self) -> str:
        """The name the user gave it, else the designer's title, else the design's name, else the request."""
        s, spec = self.state, self.state.get("spec") or {}
        if s.get("custom_title"):
            return s["custom_title"]
        if spec.get("title"):
            return str(spec["title"])
        if spec.get("name") and spec["name"] != "board":
            words = str(spec["name"]).replace("_", " ").replace("-", " ").split()
            return " ".join(words)[:1].upper() + " ".join(words)[1:]
        return s.get("title") or "New board"

    def thumbnail(self) -> str | None:
        """The first 3D render of the latest build, for the boards list."""
        images = ((self.state.get("visuals") or {}).get("outputs:images") or {}).get("images") or []
        return images[0].get("url") if images else None

    def summary(self) -> dict:
        s = self.state
        return {"id": s["id"], "title": self.display_title(), "prompt": s.get("prompt"), "status": s.get("status"),
                "created": s.get("created"), "updated": s.get("updated") or s.get("created"),
                "total": (s.get("quote") or {}).get("total"), "thumb": self.thumbnail(),
                "hidden": bool(s.get("hidden"))}


class Manager:
    def __init__(self):
        self.projects: dict[str, Project] = {}
        self.lock = threading.Lock()
        self.root = Path(config.load()["projects_dir"]).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        for d in sorted(self.root.iterdir()):
            sf = d / "state.json"
            if not sf.exists():
                continue
            try:
                state = json.loads(sf.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            p = Project(d, state)
            ef = d / "events.jsonl"
            if ef.exists():
                for line in ef.read_text(encoding="utf-8").splitlines():
                    try:
                        p.events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            if state.get("status") in ("running", "waiting"):
                state["status"] = "stopped"
                state["status_note"] = "WholeBoard was restarted; send a message to continue."
            self.projects[state["id"]] = p

    def create(self, prompt: str) -> Project:
        title = re.sub(r"\s+", " ", prompt).strip()[:70] or "New board"
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "board"
        pid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        root = self.root / f"{pid}-{slug}"
        root.mkdir(parents=True)
        state = {"id": pid, "title": title, "prompt": prompt, "created": time.time(),
                 "status": "idle", "steps": {k: {"status": "pending", "detail": ""} for k, _ in STEPS},
                 "spec": None, "revision": 0, "quote": None, "visuals": {}}
        p = Project(root, state)
        p.save()
        with self.lock:
            self.projects[pid] = p
        return p

    def get(self, pid: str) -> Project | None:
        return self.projects.get(pid)

    def list(self) -> list[dict]:
        return sorted((p.summary() for p in self.projects.values()),
                      key=lambda s: -(s.get("created") or 0))

    def set_hidden(self, hidden: bool) -> int:
        """Clear the boards list (or bring it back) without touching any board's files."""
        n = 0
        for p in list(self.projects.values()):
            with p.lock:
                if bool(p.state.get("hidden")) != hidden:
                    p.state["hidden"] = hidden
                    p.save()
                    n += 1
        return n
