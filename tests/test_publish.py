import json

import pytest

from wholeboard import config, publish
from wholeboard.project import Project


def _board(tmp_path, spec_name="blinker"):
    root = tmp_path / "board"
    rdir = root / "rev2"
    (rdir / "fab").mkdir(parents=True)
    (rdir / "schematic-svg").mkdir()
    (rdir / "firmware" / "src").mkdir(parents=True)
    for f in ("design.json", f"{spec_name}.kicad_pro", f"{spec_name}.kicad_sch", f"{spec_name}.kicad_pcb",
              f"{spec_name}-3-routed.kicad_pcb", f"{spec_name}.geo.json", f"{spec_name}.kicad_prl", "render-iso.png",
              f"fab/{spec_name}-gerbers.zip", f"fab/{spec_name}-bom.csv", f"fab/{spec_name}-cpl.csv",
              f"fab/{spec_name}-pos.csv", f"schematic-svg/{spec_name}.svg", "firmware/src/main.cpp"):
        (rdir / f).write_text("x")
    spec = {"name": spec_name, "board": {"width_mm": 30, "height_mm": 20, "layers": 2}, "components": [], "nets": []}
    state = {"id": "b1", "title": "A blinker", "prompt": "Two LEDs that blink", "revision": 2, "spec": spec,
             "steps": {}, "status": "done", "final_summary": "## What you got\nA blinker.",
             "artifacts": {"folder": str(rdir), "pcb": str(rdir / f"{spec_name}.kicad_pcb")}}
    (root / "state.json").write_text(json.dumps(state))
    return Project(root, state)


def test_repository_holds_the_build_not_the_working_files(tmp_path):
    p = _board(tmp_path)
    fs = publish.files(p)
    assert set(fs) == {"design.json", "kicad/blinker.kicad_pro", "kicad/blinker.kicad_sch", "kicad/blinker.kicad_pcb",
                       "images/render-iso.png", "images/schematic.svg", "fab/blinker-gerbers.zip",
                       "fab/blinker-bom.csv", "fab/blinker-cpl.csv", "firmware/src/main.cpp"}
    text = publish.readme(p, list(fs))
    assert text.startswith("# Blinker\n") and "![3D render of the board](images/render-iso.png)" in text
    assert "> Two LEDs that blink" in text and "### What you got" in text          # its headings nest under ours
    assert "Upload [`fab/blinker-gerbers.zip`]" in text and "[`firmware/`](firmware)" in text
    assert publish.repo_name("ESP32-C3 sensor (rev B)!") == "ESP32-C3-sensor-rev-B"


def test_publish_creates_then_updates_the_repository(tmp_path, monkeypatch):
    p = _board(tmp_path)
    calls = []

    def api(tok, method, path, body=None, timeout=60):
        calls.append((method, path, body))
        if path == "/user":
            return {"login": "maker"}
        if path == "/user/repos":
            return {"full_name": f"maker/{body['name']}", "html_url": f"https://github.com/maker/{body['name']}",
                    "default_branch": "main", "private": body["private"]}
        if method == "GET" and path.startswith("/repos/") and "/git/" not in path:
            return {"full_name": "maker/Blinker", "html_url": "https://github.com/maker/Blinker", "default_branch": "main"}
        if "/git/ref/heads/" in path:
            return {"object": {"sha": "head0"}}
        if path.endswith("/git/blobs"):
            return {"sha": f"blob{len(calls)}"}
        if path.endswith("/git/trees"):
            return {"sha": "tree1"}
        if path.endswith("/git/commits"):
            return {"sha": "commit1"}
        return {}
    monkeypatch.setattr(publish, "_api", api)
    monkeypatch.setattr(publish, "token", lambda cfg=None: ("tok", "gh"))

    info = publish.publish(p)
    create = next(b for m, path, b in calls if path == "/user/repos")
    assert create["name"] == "Blinker" and create["private"] is True and create["auto_init"]
    tree = next(b for m, path, b in calls if path.endswith("/git/trees"))["tree"]
    assert {"README.md", ".gitignore", "kicad/blinker.kicad_pcb"} <= {t["path"] for t in tree} and "base_tree" not in tree
    commit = next(b for m, path, b in calls if path.endswith("/git/commits"))
    assert commit["parents"] == ["head0"] and commit["tree"] == "tree1"
    assert ("PATCH", "/repos/maker/Blinker/git/refs/heads/main", {"sha": "commit1"}) in calls
    assert info["repo"] == "maker/Blinker" and json.loads((p.root / "state.json").read_text())["github"]["repo"] == "maker/Blinker"

    calls.clear()
    p.state["revision"] = 3
    publish.publish(p)                                             # the same board again: a new commit, no new repo
    assert not any(path == "/user/repos" for m, path, b in calls)
    assert next(b for m, path, b in calls if path.endswith("/git/commits"))["message"].startswith("Revision 3 of")


def test_nothing_to_publish_before_a_build(tmp_path):
    p = _board(tmp_path)
    p.state["artifacts"] = None
    with pytest.raises(publish.PublishError, match="no finished build"):
        publish.files(p)


def test_github_token_is_a_secret():
    shown = config.public(config._merge(config.DEFAULTS, {"github": {"token": "ghp_abcdefgh1234"}}))
    assert shown["github"]["token"].endswith("1234") and "abcdefgh" not in shown["github"]["token"]
