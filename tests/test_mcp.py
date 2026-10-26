import json
import subprocess
import sys
import threading

import pytest

from wholeboard import config, mcp


def test_handshake_and_tools():
    init = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}})
    assert init["result"]["protocolVersion"] == "2025-03-26" and "tools" in init["result"]["capabilities"]
    assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    names = [t["name"] for t in mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]]
    assert names[:7] == ["simulate", "power_budget", "impedance_check", "board_checks", "jlc_quote", "stock_check", "bringup_guide"]
    assert {"design_guide", "new_board", "open_board", "simulate_design", "submit_design", "finish"} <= set(names)
    assert not {"ask_user", "consult_expert"} & set(names)            # Claude Code asks the user, and is the expert
    bad = mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "nope"}})
    assert bad["error"]["code"] == -32602
    missing = mcp.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                          "params": {"name": "simulate", "arguments": {"project": "/no/such/folder"}}})
    assert missing["result"]["isError"] and "not a folder" in missing["result"]["content"][0]["text"]


def test_pairs_found_by_name():
    spec = {"nets": [{"name": n} for n in ("USB_DP", "USB_DN", "ETH_TXP", "ETH_TXN", "SDA", "LVDS+", "LVDS-")]}
    groups = {tuple(g["nets"]): g.get("profile") for g in mcp._pairs(spec)}
    assert groups == {("USB_DP", "USB_DN"): "usb2_hs", ("ETH_TXP", "ETH_TXN"): "ethernet_100", ("LVDS+", "LVDS-"): None}


def test_stdio():
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    p = subprocess.run([sys.executable, "-m", "wholeboard.mcp"], input="".join(json.dumps(m) + "\n" for m in msgs),
                       capture_output=True, text=True, timeout=60)
    replies = [json.loads(l) for l in p.stdout.splitlines()]
    assert [r["id"] for r in replies] == [1, 2] and replies[0]["result"] == {}


# ---------------------------------------------------------------- designing a board from Claude Code
def _call(name, **args):
    r = mcp.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": name, "arguments": args}})
    return r.get("result") or r


@pytest.fixture
def wholeboard(tmp_path, monkeypatch):
    """The WholeBoard server in-process, with the MCP server's HTTP calls routed into it."""
    from fastapi.testclient import TestClient
    from wholeboard import project, server
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "load", lambda: {"projects_dir": str(tmp_path / "boards"), "ai": {}})
    monkeypatch.setattr(server, "manager", project.Manager())
    monkeypatch.setattr(server, "_agents", {})
    monkeypatch.setitem(mcp._board, "id", None)
    c = TestClient(server.app)

    def http(method, path, body=None, timeout=30):
        r = c.request(method, path, json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"WholeBoard said: {r.json()['detail']}")
        return r.json()
    monkeypatch.setattr(mcp, "_http", http)
    return server, c


def test_claude_code_designs_a_board_through_mcp(wholeboard):
    server, c = wholeboard
    assert _call("search_parts", query="AMS1117")["isError"]                # no board yet
    started = json.loads(_call("new_board", request="Four LEDs that blink in turn")["content"][0]["text"])
    p = server.mgr().get(started["board"])
    assert started["url"].endswith("#" + p.id)
    assert p.state["driver"] == "claude-code" and (p.state["status"], p.state["status_note"]) == ("idle", "Designing in Claude Code")
    assert [e["text"] for e in p.events if e["type"] == "chat"] == ["Four LEDs that blink in turn"]

    _call("record_answer", question="An enclosure?", answer="No, but add mounting holes")
    asked = [e for e in p.events if e["type"] in ("question", "answer")]
    assert [e.get("question") or e.get("text") for e in asked] == ["An enclosure?", "No, but add mounting holes"]

    blocks = _call("list_blocks")
    assert not blocks["isError"] and json.loads(blocks["content"][0]["text"])          # ran in the server
    assert p.events[-1]["type"] == "tool" and p.worker is None

    # typed in WholeBoard's chat while Claude Code designs: queued for its next tool result, not a second designer
    c.post(f"/api/projects/{p.id}/message", json={"text": "Make them green"})
    assert p.worker is None and p.inbox == ["Make them green"]
    assert "Make them green" in _call("block_info", block="led_indicator")["content"][0]["text"]

    _call("finish", summary="Done.")
    assert p.state["status"] == "done" and p.state["finished"]
    opened = json.loads(_call("open_board", board=p.id)["content"][0]["text"])
    assert opened["finished"] and opened["steps"]["enclosure"].startswith("done")


def test_claude_code_waits_for_wholeboards_own_designer(wholeboard):
    server, c = wholeboard
    p = server.mgr().create("A blinker")
    hold = threading.Event()
    p.worker = threading.Thread(target=hold.wait, daemon=True)
    p.worker.start()
    try:
        r = _call("list_blocks", board=p.id)
        assert r["isError"] and "own designer" in r["content"][0]["text"]
    finally:
        hold.set()
    assert c.post(f"/api/projects/{p.id}/tool", json={"name": "ask_user", "args": {}}).status_code == 404


def test_design_guide_names_the_mcp_tools():
    g = json.loads(json.dumps(_call("design_guide")))["content"][0]["text"]
    assert "record_answer" in g and "simulate_design" in g and "new_board" in g
    assert "ask_user" not in g and "Call simulate to" not in g


def test_claude_code_screen(wholeboard, tmp_path, monkeypatch):
    server, c = wholeboard
    monkeypatch.setattr(mcp.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(server.shutil, "which", lambda name: "/usr/bin/claude")
    info = c.get("/api/claude-code").json()
    assert info["claude"] == "/usr/bin/claude" and not info["added"] and not info["local"]
    assert info["command"].startswith("claude mcp add --scope user wholeboard -- ") and info["command"].endswith(" -m wholeboard.mcp")
    assert mcp.add_command(9100)[6:8] == ["-e", "WHOLEBOARD_URL=http://127.0.0.1:9100"]    # not the configured port
    assert c.post("/api/claude-code/add").status_code == 403                               # only from this computer

    ran = []
    def run(argv, **kw):
        ran.append(argv)
        (tmp_path / ".claude.json").write_text(json.dumps({"mcpServers": {"wholeboard": {"command": argv[-3], "args": argv[-2:]}}}))
        return subprocess.CompletedProcess(argv, 0, "Added stdio MCP server wholeboard", "")
    monkeypatch.setattr(server.subprocess, "run", run)
    from fastapi.testclient import TestClient
    local = TestClient(server.app, client=("127.0.0.1", 50000))
    assert local.post("/api/claude-code/add").json()["added"]
    assert ran[0][:6] == ["/usr/bin/claude", "mcp", "add", "--scope", "user", "wholeboard"]
    assert local.get("/api/claude-code").json()["added"]
