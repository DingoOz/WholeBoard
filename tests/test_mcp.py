import json
import subprocess
import sys

from wholeboard import mcp


def test_handshake_and_tools():
    init = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}})
    assert init["result"]["protocolVersion"] == "2025-03-26" and "tools" in init["result"]["capabilities"]
    assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    names = [t["name"] for t in mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]]
    assert names == ["simulate", "power_budget", "impedance_check", "board_checks", "jlc_quote", "stock_check", "bringup_guide"]
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
