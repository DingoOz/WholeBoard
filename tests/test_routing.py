"""The route step: retrying an incomplete route, and what TraceMaker reports (issue #11)."""

import json
from pathlib import Path

def _retry_harness(tmp_path, monkeypatch, results, moved=True):
    """_route_board with TraceMaker faked: each route takes the next result and writes a board saying which run."""
    import threading
    from types import SimpleNamespace
    from wholeboard import pipeline, router
    calls, logs = [], []
    runs = iter(results)

    def route(src, out, reroute=False, **kw):
        calls.append(("route", src.name, reroute))
        out.write_text(f"route {len([c for c in calls if c[0] == 'route'])}")
        return {"complete": False, "refill_open": 0, "seconds": 100, "not_attempted": 0, "failures": [], **next(runs)}

    def place(src, out, mode="routable", **kw):
        calls.append(("place", mode))
        out.write_text("eco")
    monkeypatch.setattr(router, "route", route)
    monkeypatch.setattr(router, "place", place)
    monkeypatch.setattr(pipeline, "_same_placement", lambda a, b: not moved)
    p = SimpleNamespace(cancel=threading.Event(), log=lambda step, s: logs.append(s))
    cfg = {"tools": {"route_time_s": 100}}
    spec = {"board": {}, "impedance": []}
    routed = tmp_path / "b-3-routed.kicad_pcb"
    rr = pipeline._route_board(p, cfg, spec, tmp_path / "b-2-placed.kicad_pcb", routed, tmp_path / "b-2b-eco.kicad_pcb",
                               None, None, [])
    return rr, calls, logs, routed


def test_the_route_retry_is_announced_up_front(tmp_path, monkeypatch):
    rr, calls, logs, _ = _retry_harness(tmp_path, monkeypatch, [{"routed": 10, "connections": 10, "complete": True}])
    assert rr["complete"] and calls == [("route", "b-2-placed.kicad_pcb", False)]
    assert "up to 100 s" in logs[0] and "routed once more" in logs[0]


def test_no_retry_when_the_router_ran_out_of_time_before_trying_most(tmp_path, monkeypatch):
    """1184/1194 after the whole limit with 9 of 10 never tried: a second run with the same limit can't do better."""
    rr, calls, logs, _ = _retry_harness(tmp_path, monkeypatch,
                                        [{"routed": 1184, "connections": 1194, "not_attempted": 9, "seconds": 99}])
    assert calls == [("route", "b-2-placed.kicad_pcb", False)]            # no ECO pass, no second route
    assert rr["routed"] == 1184 and "ran out of time before trying 9 of the 10" in logs[-1]


def test_retry_when_the_open_connections_were_tried(tmp_path, monkeypatch):
    # most failures were tried (boxed in, say) or the router stopped well inside its limit: the nudge may help
    for first in ({"routed": 8, "connections": 10, "not_attempted": 0, "seconds": 99},
                  {"routed": 8, "connections": 10, "not_attempted": 2, "seconds": 30}):
        rr, calls, logs, routed = _retry_harness(tmp_path, monkeypatch, [first, {"routed": 10, "connections": 10,
                                                                                 "complete": True}])
        assert calls == [("route", "b-2-placed.kicad_pcb", False), ("place", "eco"),
                         ("route", "b-2b-eco.kicad_pcb", True)]
        assert rr["complete"] and routed.read_text() == "route 2"


def test_no_retry_when_the_nudge_moved_nothing(tmp_path, monkeypatch):
    rr, calls, logs, routed = _retry_harness(tmp_path, monkeypatch, [{"routed": 8, "connections": 10}], moved=False)
    assert calls == [("route", "b-2-placed.kicad_pcb", False), ("place", "eco")]
    assert "moved no parts" in logs[-1] and routed.read_text() == "route 1"


def test_a_worse_retry_puts_the_first_board_back(tmp_path, monkeypatch):
    rr, calls, logs, routed = _retry_harness(tmp_path, monkeypatch, [{"routed": 9, "connections": 10},
                                                                     {"routed": 7, "connections": 10}])
    assert rr["routed"] == 9 and routed.read_text() == "route 1"


def test_route_counts_the_connections_never_tried(tmp_path, monkeypatch):
    from wholeboard import router
    stuck = {"unrouted_by_reason": [{"name": "not_attempted", "count": 7}, {"name": "boxed_in", "count": 2}]}

    def fake_stream(cmd, log, cancel, timeout):
        out = Path(cmd[cmd.index("-o") + 1])
        out.write_text("pcb")
        Path(cmd[cmd.index("--json") + 1]).write_text(json.dumps({"routed": 91, "connections": 100}))
        Path(cmd[cmd.index("--stuck-report") + 1]).write_text(json.dumps(stuck))
        return 0
    monkeypatch.setattr(router, "find", lambda name, cfg=None: "/bin/tracemaker")
    monkeypatch.setattr(router, "_stream", fake_stream)
    monkeypatch.setattr(router, "_copy_project", lambda a, b: None)
    rr = router.route(tmp_path / "in.kicad_pcb", tmp_path / "out.kicad_pcb", seconds=10)
    assert rr["not_attempted"] == 7 and not rr["complete"]
    def broken_report(cmd, log, cancel, timeout):                       # a broken report counts as none
        Path(cmd[cmd.index("-o") + 1]).write_text("pcb")
        Path(cmd[cmd.index("--stuck-report") + 1]).write_text("{")
        return 0
    monkeypatch.setattr(router, "_stream", broken_report)
    assert router.route(tmp_path / "in.kicad_pcb", tmp_path / "out.kicad_pcb", seconds=10)["not_attempted"] == 0
