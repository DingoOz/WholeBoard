import json
import threading

from wholeboard import bench, config, pipeline
from wholeboard.ai import agent as agent_mod


class FakeAgent:
    """Builds twice (the first fails at routing), asks one question, uses some tokens."""
    def __init__(self, p, origin=None):
        self.p = p

    def start(self, text):
        p = self.p
        assert "unattended benchmark run" in text

        def work():
            p.emit({"type": "question", "id": "q1", "question": "Which colour?", "options": []})
            with p.answer_ready:
                while "q1" not in p.answers:
                    p.answer_ready.wait(0.1)
            pipeline.build(p, {"name": "x"})
            pipeline.build(p, {"name": "x"})
            p.state["usage"] = {"input": 1000, "output": 200, "cost": 0.05}
            p.state["quote"] = {"total": 12.5}
            p.state["finished"] = True
        p.worker = threading.Thread(target=work, daemon=True)
        p.worker.start()


def test_run_one_records_metrics(tmp_path, monkeypatch):
    results = iter([{"ok": False, "stage": "routing", "routing": {"connections": 10, "routed": 8}},
                    {"ok": True, "erc": {"errors": []}, "drc": {"errors": 0}, "routing": {"connections": 10, "routed": 10},
                     "simulation": {"problems": []}}])
    monkeypatch.setattr(pipeline, "build", lambda p, spec, *a, **k: next(results))
    monkeypatch.setattr(agent_mod, "Agent", FakeAgent)
    row = bench.run_one({"id": "t", "prompt": "a board"}, tmp_path, 30, log=lambda s: None)
    assert row["completed"] and row["rebuilds"] == 1 and row["unrouted"] == 0 and row["drc_errors"] == 0
    assert row["tokens"] == 1200 and row["api_cost"] == 0.05 and row["board_cost"] == 12.5 and row["questions"] == 1


def test_table(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "RESULTS", tmp_path)
    row = {"id": "a", "completed": True, "rebuilds": 2, "erc_errors": 0, "drc_errors": 0, "unrouted": 0,
           "sim_failures": 1, "board_cost": 20.0, "tokens": 5000, "api_cost": 0.4, "minutes": 6.0}
    bad = {**row, "id": "b", "completed": False, "stage": "routing", "unrouted": 3, "board_cost": None}
    (tmp_path / "r.json").write_text(json.dumps({"date": "2026-10-22T10:00:00", "label": "m1", "rows": [row, bad]}))
    t = bench.table()
    assert "| 2026-10-22 | m1 | 2 | 1/2 | 2.0 | 0 | 0 | 3 | 2 | $20.00 | 5,000 | $0.80 | 6.0 |" in t
    assert "| b | ✗ routing |" in t


def test_model_override(monkeypatch):
    real = config.load
    try:
        bench._override({"provider": "openai", "openai_model": "m"})
        assert config.load()["ai"]["openai_model"] == "m" and "anthropic_model" in config.load()["ai"]
    finally:
        config.load = real
