"""Publish a finished board to the user's GitHub as a repository: the KiCad project, the files for JLCPCB, renders,
enclosure, firmware and a README. Uses GitHub's REST API, so git does not need to be installed.

The token is the one in Settings, or else the GitHub CLI's (`gh auth token`) when it is signed in.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, design
from .jlc import quote as jquote
from .project import Project

API = "https://api.github.com"
GITIGNORE = "*-backups/\n*.kicad_prl\n_autosave-*\n~*.lck\nfp-info-cache\n.DS_Store\n"


class PublishError(RuntimeError):
    pass


def token(cfg: dict | None = None) -> tuple[str | None, str | None]:
    """(token, where it came from): Settings first, then the GitHub CLI."""
    t = ((cfg or config.load()).get("github") or {}).get("token") or ""
    if t.strip():
        return t.strip(), "settings"
    gh = shutil.which("gh")
    if gh:
        try:
            r = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip(), "gh"
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None, None


def _api(tok: str, method: str, path: str, body: dict | None = None, timeout: float = 60):
    req = urllib.request.Request(API + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "WholeBoard"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read() or b"{}")
        except ValueError:
            data = {}
        msg = data.get("message") or e.reason
        errs = "; ".join(x.get("message") or x.get("code", "") for x in data.get("errors") or [] if isinstance(x, dict))
        if e.code == 401:
            raise PublishError("GitHub did not accept the token. Sign in again with `gh auth login`, or check the "
                               "token in Settings → GitHub.") from None
        if e.code == 403 and "rate limit" in str(msg).lower():
            raise PublishError("GitHub's rate limit was reached; try again in a few minutes.") from None
        raise PublishError(f"GitHub said: {msg}" + (f" ({errs})" if errs else "")) from None
    except urllib.error.URLError as e:
        raise PublishError(f"Cannot reach GitHub: {e.reason}") from None


def account(cfg: dict | None = None) -> dict:
    tok, source = token(cfg)
    if not tok:
        return {"ok": False, "error": "No GitHub account: sign in with `gh auth login` on the computer running "
                                      "WholeBoard, or add a token in Settings → GitHub."}
    try:
        user = _api(tok, "GET", "/user", timeout=15)
    except PublishError as e:
        return {"ok": False, "error": str(e), "source": source}
    return {"ok": True, "login": user["login"], "source": source}


def repo_name(text: str) -> str:
    """A repository name from the board's title: letters, digits, '.', '-' and '_' only."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", text.strip()).strip("-.")[:80]
    return name or "pcb"


# ---------------------------------------------------------------- what goes in the repository
def _folder(p: Project) -> Path:
    folder = (p.state.get("artifacts") or {}).get("folder")
    if not folder or not Path(folder).is_dir():
        raise PublishError("This board has no finished build to publish yet.")
    return Path(folder)


def files(p: Project) -> dict[str, Path]:
    """Repository path -> file on disk, from the latest build."""
    rdir = _folder(p)
    spec = design.normalise(p.state.get("spec") or {})
    name = spec.get("name") or Path((p.state.get("artifacts") or {}).get("pcb") or "board").stem
    rev = p.state.get("revision", 0)
    out: dict[str, Path] = {}

    def add(path: str, f: Path):
        if f.is_file():
            out[path] = f

    add("design.json", rdir / "design.json")
    for ext in ("kicad_pro", "kicad_sch", "kicad_pcb"):
        add(f"kicad/{name}.{ext}", rdir / f"{name}.{ext}")
    for f in sorted(rdir.glob("*.kicad_sch")):            # the sheets of a hierarchical schematic
        add(f"kicad/{f.name}", f)
    for f in sorted((rdir / "fab").glob("*")):
        if f.suffix in (".zip", ".csv", ".html") and not f.name.endswith("-pos.csv"):
            add(f"fab/{f.name}", f)
    for f in sorted(rdir.glob("render-*.png")):
        add(f"images/{f.name}", f)
    for f in sorted((rdir / "schematic-svg").glob("*.svg")):    # name.svg, then name-<sheet>.svg for sheets
        sheet = f.stem[len(name):] if f.stem.startswith(name) else "-" + f.stem
        add(f"images/schematic{sheet}.svg", f)
    enc = rdir / "enclosure"
    for f in sorted(enc.glob("*")):
        if f.suffix.lower() in (".stl", ".step", ".fcstd", ".fcmacro"):
            add(f"enclosure/{f.name}", f)
    fw = rdir / "firmware"
    for f in sorted(fw.rglob("*")):
        if f.is_file() and not any(part.startswith(".") for part in f.relative_to(fw).parts):
            add(f"firmware/{f.relative_to(fw).as_posix()}", f)
    add(f"docs/{name}-bringup.md", rdir / f"{name}-bringup.md")
    add("docs/report.pdf", p.root / "exports" / f"{name}-rev{rev}-report.pdf")
    return out


def readme(p: Project, paths: list[str]) -> str:
    st = p.state
    spec = design.normalise(st.get("spec") or {})
    b = spec.get("board") or {}
    title = p.display_title()
    lines = [f"# {title}", ""]
    if "images/render-iso.png" in paths:
        lines += ["![3D render of the board](images/render-iso.png)", ""]
    if st.get("prompt"):
        lines += ["> " + "\n> ".join(str(st["prompt"]).strip().splitlines()), ""]
    facts = [f"{b.get('width_mm', '?')} × {b.get('height_mm', '?')} mm", f"{b.get('layers', 2)} layers",
             f"{b.get('thickness_mm', 1.6)} mm thick", f"{len(spec.get('components') or [])} parts",
             f"revision {st.get('revision', 0)}"]
    total = (st.get("quote") or {}).get("total")
    if total:
        q = st["quote"]
        facts.append(f"about {q.get('currency', 'USD')} {total:.2f} for {q.get('quantity', '?')} boards at JLCPCB")
    lines += [" · ".join(facts), ""]
    if st.get("final_summary"):
        summary = re.sub(r"^(#{1,5}) ", r"#\1 ", str(st["final_summary"]).strip(), flags=re.M)   # nest its headings
        lines += ["## About this board", "", summary, ""]

    rows = jquote.bom_rows(spec)
    if rows:
        lines += ["## Parts", "", "| Refs | Value | Footprint | LCSC |", "|---|---|---|---|"]
        cell = lambda s: str(s).replace("|", "\\|")  # noqa: E731
        for r in rows:
            lcsc = f"[{r['lcsc']}](https://jlcpcb.com/partdetail/{r['lcsc']})" if r["lcsc"] else ""
            lines.append(f"| {cell(', '.join(r['refs']))} | {cell(r['value'])} | {cell(r['footprint'])} | {lcsc} |")
        lines.append("")

    gerbers = next((x for x in paths if x.endswith("-gerbers.zip")), None)
    bom = next((x for x in paths if x.endswith("-bom.csv")), None)
    cpl = next((x for x in paths if x.endswith("-cpl.csv")), None)
    if gerbers:
        lines += ["## Ordering", "", f"Upload [`{gerbers}`]({gerbers}) to JLCPCB."
                  + (f" For assembly, add [`{bom}`]({bom}) and [`{cpl}`]({cpl})." if bom and cpl else "")
                  + " Check part rotations in JLCPCB's assembly preview before you order.", ""]

    groups = [("kicad/", "KiCad project (open the .kicad_pro in KiCad)"), ("fab/", "Files for JLCPCB"),
              ("images/", "Renders and schematic"), ("enclosure/", "3D-printable enclosure"),
              ("firmware/", "Starter firmware (PlatformIO)"), ("docs/", "Bring-up guide and report"),
              ("design.json", "The design as WholeBoard describes it; import it to change and rebuild the board")]
    lines += ["## Files", ""]
    for prefix, label in groups:
        if any(x.startswith(prefix) for x in paths):
            lines.append(f"- [`{prefix}`]({prefix.rstrip('/')}): {label}")
    lines += ["", "Designed with WholeBoard.", ""]
    return "\n".join(lines)


def preview(p: Project) -> dict:
    fs = files(p)
    listed = [{"path": k, "size": v.stat().st_size} for k, v in sorted(fs.items())]
    text = readme(p, list(fs))
    listed.insert(0, {"path": "README.md", "size": len(text.encode())})
    return {"files": listed, "total": sum(f["size"] for f in listed), "name": repo_name(p.display_title()),
            "description": _description(p), "readme": text, "published": p.state.get("github")}


def _description(p: Project) -> str:
    b = (design.normalise(p.state.get("spec") or {}).get("board") or {})
    return f"{p.display_title()}: a {b.get('width_mm', '?')} × {b.get('height_mm', '?')} mm, {b.get('layers', 2)}-layer PCB"[:300]


# ---------------------------------------------------------------- publishing
def publish(p: Project, name: str | None = None, description: str | None = None, private: bool = True,
            log=lambda m: None) -> dict:
    """Create the repository (or add a commit to the one this board was published to) with the latest build."""
    tok, _ = token()
    if not tok:
        raise PublishError(account()["error"])
    fs = files(p)
    blobs = {"README.md": readme(p, list(fs)).encode(), ".gitignore": GITIGNORE.encode(),
             **{k: v.read_bytes() for k, v in fs.items()}}
    login = _api(tok, "GET", "/user")["login"]
    prev = p.state.get("github") or {}
    if prev.get("repo") and (not name or prev["repo"].split("/", 1)[1] == name):
        full = prev["repo"]
        repo = _api(tok, "GET", f"/repos/{full}")
        log(f"Updating {full}")
    else:
        name = repo_name(name or p.display_title())
        log(f"Creating {login}/{name}")
        repo = _api(tok, "POST", "/user/repos", {"name": name, "description": description or _description(p),
                                                 "private": bool(private), "auto_init": True,
                                                 "has_wiki": False, "has_projects": False})
        full = repo["full_name"]
        try:
            _api(tok, "PUT", f"/repos/{full}/topics", {"names": ["pcb", "kicad", "jlcpcb", "wholeboard"]})
        except PublishError:
            pass                                             # topics are a nicety
    branch = repo.get("default_branch") or "main"
    head = None
    for _ in range(5):                                       # a brand-new repository's first commit can lag a moment
        try:
            head = _api(tok, "GET", f"/repos/{full}/git/ref/heads/{branch}")["object"]["sha"]
            break
        except PublishError:
            time.sleep(1.5)
    if not head:
        raise PublishError(f"GitHub created {full} but its {branch} branch is not ready yet; publish again.")

    log(f"Uploading {len(blobs)} files")
    upload = lambda data: _api(tok, "POST", f"/repos/{full}/git/blobs",  # noqa: E731
                               {"content": base64.b64encode(data).decode(), "encoding": "base64"}, timeout=300)["sha"]
    with ThreadPoolExecutor(4) as pool:
        shas = dict(zip(sorted(blobs), pool.map(upload, [blobs[k] for k in sorted(blobs)])))
    tree = [{"path": path, "mode": "100644", "type": "blob", "sha": sha} for path, sha in shas.items()]
    t = _api(tok, "POST", f"/repos/{full}/git/trees", {"tree": tree})     # the whole tree: removed files go too
    rev = p.state.get("revision", 0)
    msg = f"Revision {rev} of {p.display_title()}" if prev.get("repo") == full else f"{p.display_title()}, revision {rev}"
    commit = _api(tok, "POST", f"/repos/{full}/git/commits", {"message": msg, "tree": t["sha"], "parents": [head]})
    _api(tok, "PATCH", f"/repos/{full}/git/refs/heads/{branch}", {"sha": commit["sha"]})
    info = {"repo": full, "url": repo["html_url"], "private": repo.get("private", private), "revision": rev,
            "commit": commit["sha"], "published": time.time()}
    with p.lock:
        p.state["github"] = info
        p.save()
    return info
