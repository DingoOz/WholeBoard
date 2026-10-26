"""Tests for the 3D models, cost cutting, import, history, firmware, password, panel and print features."""
import io
import zipfile

import pytest

from wholeboard import auth, config, enclosure, firmware, models3d, revisions
from wholeboard.ai.providers import turn_cost
from wholeboard.jlc import panel, savings
from wholeboard.kicad import importer


@pytest.fixture
def own_config(tmp_path, monkeypatch):
    """Keep password tests away from the real ~/.wholeboard/config.json."""
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "APP_DIR", tmp_path)


# ---------------------------------------------------------------- 3D models
def test_model_rel_path():
    assert models3d.rel_path("${KICAD10_3DMODEL_DIR}/Package_SO.3dshapes/SOIC-8_3.9x4.9mm.wrl") == \
        "Package_SO.3dshapes/SOIC-8_3.9x4.9mm.step"
    assert models3d.rel_path("model.step") is None


def test_step_bbox_reads_exponents(tmp_path, monkeypatch):
    monkeypatch.setattr(models3d, "CACHE", tmp_path / "cache")
    f = tmp_path / "x.step"
    f.write_text("ISO-10303-21;\n#1 = CARTESIAN_POINT('',(0.,0.,0.));\n#2 = CARTESIAN_POINT('',(-0.8,-0.4,3.E-02));\n"
                 "#3 = CARTESIAN_POINT('',(0.8,0.4,0.77));\n")
    assert models3d.step_bbox(f) == [-0.8, -0.4, 0.0, 0.8, 0.4, 0.77]


def test_model_height_follows_rotation(tmp_path, monkeypatch):
    monkeypatch.setattr(models3d, "CACHE", tmp_path / "cache")
    f = tmp_path / "Lib.3dshapes" / "Part.step"
    f.parent.mkdir()
    f.write_text("#1 = CARTESIAN_POINT('',(-1.,-5.,0.));\n#2 = CARTESIAN_POINT('',(1.,5.,2.));\n")
    monkeypatch.setattr(models3d, "local", lambda rel: f)
    flat = {"path": "${X}/Lib.3dshapes/Part.step"}
    assert models3d.model_height(flat) == 2.0
    # Stood up on its end: the 10 mm length becomes the height.
    assert models3d.model_height({**flat, "rotate": [90, 0, 0]}) == 5.0
    assert models3d.model_height({**flat, "offset": [0, 0, 1.5]}) == 3.5


# ---------------------------------------------------------------- cheaper parts
def test_passive_detection():
    line = {"refs": ["R3"], "footprint": "R_0603_1608Metric", "value": "4k7"}
    assert savings._passive(line, {"value": "4k7"}) == ("resistor", "4k7", "0603")
    led = {"refs": ["D1"], "footprint": "LED_0805_2012Metric", "value": "LED"}
    assert savings._passive(led, {"value": "Red LED"}) == ("led", "red", "0805")
    assert savings._passive({"refs": ["U1"], "footprint": "SOIC-8_3.9x4.9mm_P1.27mm"}, {}) is None


def test_capacitor_swap_keeps_voltage_rating():
    old = {"description": "100nF 50V X7R 0603"}
    assert savings._fits(old, {"description": "100nF 50V X7R"}, "capacitor", {})
    assert not savings._fits(old, {"description": "100nF 16V X7R"}, "capacitor", {})
    assert not savings._fits({"description": "10k ±1% 0603"}, {"description": "10k ±5%"}, "resistor", {})


def test_apply_swaps():
    spec = {"components": [{"ref": "R1", "lcsc": "C1", "mpn": "X"}, {"ref": "R2", "lcsc": "C1"}]}
    assert savings.apply(spec, [{"refs": ["R1", "R2"], "lcsc": "c25804"}, {"refs": ["R1"], "lcsc": "bad"}]) == ["R1", "R2"]
    assert spec["components"][0] == {"ref": "R1", "lcsc": "C25804"}


# ---------------------------------------------------------------- import
def test_upload_refuses_escaping_paths(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("proj/board.kicad_sch", "x")
        z.writestr("../evil.txt", "x")
    with pytest.raises(ValueError):
        importer.save_upload([("p.zip", buf.getvalue())], tmp_path / "in")
    assert not (tmp_path / "evil.txt").exists()
    files = importer.save_upload([("C:\\Users\\me\\board.kicad_pcb", b"x")], tmp_path / "ok")
    assert files == [tmp_path / "ok" / "board.kicad_pcb"]


def test_net_names():
    used = set()
    assert importer._net_name("/LED_A", used) == "LED_A"
    assert importer._net_name("Net-(R1-Pad2)", used) == "N_R1-Pad2"
    assert importer._net_name("/LED A", used) == "LED_A_2"


# ---------------------------------------------------------------- history
def test_compare_revisions():
    a = {"board": {"width_mm": 40}, "components": [{"ref": "R1", "value": "10k"}, {"ref": "R2", "value": "1k"}],
         "nets": [{"name": "A", "pins": ["R1.1", "R2.1"]}]}
    b = {"board": {"width_mm": 45}, "components": [{"ref": "R1", "value": "4k7"}, {"ref": "C1", "value": "100n"}],
         "nets": [{"name": "A", "pins": ["R1.1", "C1.1"]}]}
    d = revisions.compare(a, b)
    assert [p["ref"] for p in d["parts_added"]] == ["C1"] and [p["ref"] for p in d["parts_removed"]] == ["R2"]
    assert d["parts_changed"] == [{"ref": "R1", "changes": {"value": ["10k", "4k7"]}}]
    assert d["nets_changed"] == [{"net": "A", "added": ["C1.1"], "removed": ["R2.1"]}]
    text = revisions.brief(d)
    assert "R1 value 10k → 4k7" in text and "board width 40 → 45" in text


# ---------------------------------------------------------------- firmware
def test_gpio_names():
    assert firmware._gpio("esp32", ["IO4"]) == 4
    assert firmware._gpio("esp32", ["SENSOR_VP"]) == 36
    assert firmware._gpio("rp2040", ["GPIO26_ADC0"]) == 26
    assert firmware._gpio("stm32", ["PA5"]) == "PA5"
    assert firmware._gpio("atmega328", ["PC0"]) == "A0"
    assert firmware._gpio("atmega328", ["XTAL1", "PB6"]) is None


def test_firmware_project(tmp_path):
    spec = {"name": "t", "components": [
        {"ref": "U1", "value": "RP2040", "symbol": "MCU_RaspberryPi:RP2040", "footprint": "x"},
        {"ref": "SW1", "value": "button", "footprint": "x"}],
        "nets": [{"name": "GND", "pins": ["U1.19", "SW1.2"]}, {"name": "BTN_A", "pins": ["U1.2", "SW1.1"]}]}
    out = firmware.generate(spec, tmp_path / "fw")
    if not out["pins"]:
        pytest.skip("KiCad symbol library not available")
    assert out["family"] == "RP2040"
    pins = (tmp_path / "fw" / "include" / "pins.h").read_text()
    assert "#define PIN_BTN_A" in pins and "GPIO0" in pins
    assert "INPUT_PULLUP" in (tmp_path / "fw" / "src" / "main.cpp").read_text()


# ---------------------------------------------------------------- password
def test_password_hash_and_tokens(own_config):
    h = auth.hash_password("hunter22")
    assert auth.check_password("hunter22", h) and not auth.check_password("hunter23", h)
    auth.set_password("hunter22")
    t = auth.make_token()
    tampered = t[:-1] + ("1" if t[-1] == "0" else "0")
    assert auth.valid_token(t) and not auth.valid_token(tampered) and not auth.valid_token("1.abc")
    auth.set_password("another1")              # changing it signs everyone out
    assert not auth.valid_token(t)
    auth.set_password(None)
    assert not auth.enabled()


def test_middleware_guards_remote_clients(own_config):
    from fastapi.testclient import TestClient
    from wholeboard import server
    c = TestClient(server.app)
    auth.set_password("secret123")
    try:
        assert c.get("/api/config").status_code == 401
        assert c.post("/api/login", data={"password": "secret123"}, follow_redirects=False).status_code == 303
        assert c.get("/api/config").status_code == 200
        assert c.post("/api/auth/password", json={"password": ""}).status_code == 403
    finally:
        auth.set_password(None)


def test_clearing_the_boards_list_keeps_the_files(own_config, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from wholeboard import project, server
    monkeypatch.setattr(config, "load", lambda: {"projects_dir": str(tmp_path / "boards")})
    monkeypatch.setattr(server, "manager", project.Manager())
    p = server.mgr().create("A blinker")
    c = TestClient(server.app)
    assert c.post("/api/projects/clear", json={}).json()["changed"] == 1
    assert [b["hidden"] for b in c.get("/api/projects").json()] == [True]
    assert (p.root / "state.json").exists() and project.Manager().list()[0]["hidden"]   # survives a restart
    assert c.post("/api/projects/clear", json={"restore": True}).json()["changed"] == 1
    assert not c.get("/api/projects").json()[0]["hidden"]


def test_board_titles_and_renaming(own_config, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from wholeboard import project, server
    monkeypatch.setattr(config, "load", lambda: {"projects_dir": str(tmp_path / "boards")})
    monkeypatch.setattr(server, "manager", project.Manager())
    p = server.mgr().create("I would like a board that has 4 rgb leds on it and can connect to i2c via qwiic")
    assert p.display_title().startswith("I would like")                       # nothing better yet
    p.state["spec"] = {"name": "qwiic_rgb4_pca9685"}
    assert p.display_title() == "Qwiic rgb4 pca9685"                         # the design's name
    p.state["spec"]["title"] = "Qwiic RGB LED driver"
    assert p.display_title() == "Qwiic RGB LED driver"                       # the designer's title
    c = TestClient(server.app)
    assert c.patch(f"/api/projects/{p.id}", json={"title": "  Desk   lamp "}).json()["title"] == "Desk lamp"
    assert c.get(f"/api/projects/{p.id}").json()["title"] == "Desk lamp"
    assert p.state["prompt"].startswith("I would like")                      # the request is kept
    assert c.patch(f"/api/projects/{p.id}", json={"title": ""}).json()["title"] == "Qwiic RGB LED driver"


def test_copper_only_parts_are_not_bought():
    from wholeboard import design
    from wholeboard.jlc import quote
    spec = {"name": "t", "board": {"width_mm": 20, "height_mm": 20, "layers": 2}, "nets": [],
            "components": [{"ref": "R1", "value": "10k", "footprint": "Resistor_SMD:R_0603_1608Metric", "lcsc": "C25804"},
                           {"ref": "JP1", "value": "A0", "footprint": "Jumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm"},
                           {"ref": "TP1", "value": "GND", "footprint": "TestPoint:TestPoint_Pad_D1.5mm"}]}
    assert [r["refs"] for r in quote.bom_rows(spec)] == [["R1"]]
    assert design.copper_only("NetTie:NetTie-2_SMD_Pad0.5mm") and not design.copper_only("Jumper:Jumper_2_Open")


def test_local_model_search_reads_each_server(monkeypatch):
    from wholeboard.ai import providers
    replies = {
        "http://localhost:11434/api/tags": {"models": [
            {"name": "qwen3:32b", "size": 20e9, "details": {"parameter_size": "32.8B", "quantization_level": "Q4_K_M"}},
            {"name": "nomic-embed-text", "size": 2.7e8, "details": {}}]},
        "http://localhost:8080/v1/models": {"data": [{"id": "gemma", "meta": {"n_ctx": 32768}}]},
        "http://localhost:8080/props": {"default_generation_settings": {}, "chat_template": "{{ messages }}"},
        "http://localhost:8000/v1/models": {"data": [{"id": "llama-3", "owned_by": "vllm"}, {"id": "bge-embed"}]},
    }

    def fake(url, body=None, timeout=2.0):
        if url.endswith("/api/show"):
            return {"capabilities": ["embedding"] if "embed" in body["model"] else ["completion", "tools"]}
        if url not in replies:
            raise OSError("connection refused")
        return replies[url]
    monkeypatch.setattr(providers, "_get_json", fake)
    got = {(m["server"], m["model"]): m for m in providers.find_local_models()["models"]}
    assert set(got) == {("Ollama", "qwen3:32b"), ("llama.cpp", "gemma"), ("vLLM", "llama-3")}   # no embedding models
    assert got["Ollama", "qwen3:32b"]["tools"] is True and got["Ollama", "qwen3:32b"]["detail"] == "32.8B · Q4_K_M · 20.0 GB"
    assert got["llama.cpp", "gemma"]["tools"] is False and got["llama.cpp", "gemma"]["detail"] == "32k context"
    assert got["vLLM", "llama-3"]["base_url"] == "http://localhost:8000/v1" and got["vLLM", "llama-3"]["tools"] is None
    assert providers.find_local_models("http://10.0.0.5:9000/v1/")["checked"][0] == "http://10.0.0.5:9000/v1"
    # Strata (stratallm.org) copies llama.cpp's /props but names itself, and states tool support outright
    replies["http://localhost:8080/props"] = {"default_generation_settings": {}, "build_info": "Strata 0.1.47",
                                              "chat_template": "{{ messages }}",
                                              "chat_template_caps": {"supports_tool_calls": True}}
    replies["http://localhost:1337/v1/models"] = {"data": [{"id": "qwen"}]}
    replies["http://localhost:1337/health"] = {"status": "ok", "service": "strata"}
    got = {m["base_url"]: m for m in providers.find_local_models()["models"]}
    assert got["http://localhost:8080/v1"]["server"] == "Strata 0.1.47" and got["http://localhost:8080/v1"]["tools"] is True
    assert got["http://localhost:1337/v1"]["server"] == "Strata"


def test_lan_search_scans_this_computers_subnet(monkeypatch):
    import asyncio
    import socket
    from wholeboard.ai import providers
    monkeypatch.setattr(providers, "lan_addresses", lambda: ["192.168.1.83", "10.4.0.7", "172.20.9.1"])
    monkeypatch.setattr(providers, "_prefix_lengths", lambda: {"192.168.1.83": 24, "10.4.0.7": 16})
    nets = {str(n["network"]): n["whole"] for n in providers.lan_networks()}
    # a /16 is narrowed to the /24 around us; an address of unknown size gets a /24
    assert nets == {"192.168.1.0/24": None, "10.4.0.0/24": "10.4.0.0/16", "172.20.9.0/24": None}

    monkeypatch.setattr(providers, "lan_addresses", lambda: ["192.168.1.83"])
    real_ports, scanned = providers._open_ports, []

    async def fake_ports(hosts, ports):
        scanned.extend(hosts)
        return [("192.168.1.16", 11434), ("192.168.1.1", 8080)]          # Ollama, and a router's web page
    monkeypatch.setattr(providers, "_open_ports", fake_ports)

    def fake_json(url, body=None, timeout=2.0):
        if url == "http://192.168.1.16:11434/api/tags":
            return {"models": [{"name": "qwen3.5:9b", "size": 6.6e9, "details": {}}]}
        if url.endswith("/api/show"):
            return {"capabilities": ["completion", "tools"]}
        raise OSError("not a model server")
    monkeypatch.setattr(providers, "_get_json", fake_json)
    r = providers.find_lan_models()
    assert len(scanned) == 253 and "192.168.1.83" not in scanned                # 254 hosts less this computer
    assert [(m["server"], m["base_url"], m["tools"]) for m in r["models"]] == \
        [("Ollama on 192.168.1.16", "http://192.168.1.16:11434/v1", True)]
    assert r["networks"] == [{"network": "192.168.1.0/24", "whole": None}] and r["open"] == 2

    with socket.socket() as srv:                                                  # the real port check
        srv.bind(("127.0.0.1", 0)); srv.listen()
        port = srv.getsockname()[1]
        with socket.socket() as free:
            free.bind(("127.0.0.1", 0)); closed = free.getsockname()[1]
        assert asyncio.run(real_ports(["127.0.0.1"], [port, closed])) == [("127.0.0.1", port)]


# ---------------------------------------------------------------- panel, print, usage
def test_panel_suggested_when_cheaper():
    from wholeboard.jlc import quote
    spec = {"name": "t", "board": {"width_mm": 42, "height_mm": 26, "layers": 2, "corner_radius_mm": 0},
            "components": [], "nets": []}
    q = quote.full_quote(spec, {"quantity": 50, "assembly": False})
    po = q["panel_options"]
    r = po["recommended"]
    assert r and r["boards"] >= 50 and r["pcb_cost"] < po["single"]["pcb_cost"] and r["method"] == "V-cut"
    assert all(50 <= l["boards"] <= 200 for l in po["layouts"])
    q2 = quote.full_quote(spec, {"quantity": 50, "assembly": False, "panel": {"nx": r["nx"], "ny": r["ny"]}})
    assert q2["panel"]["boards_per_panel"] == r["nx"] * r["ny"] and q2["pcb"]["subtotal"] == r["pcb_cost"]
    assert panel.layout(20, 15, 2, 2, True)["width_mm"] == 42.0


def test_print_estimate():
    e = enclosure.print_estimate(30_000, {"price_per_kg": 20, "rate_cm3_h": 15})
    assert e["grams"] == pytest.approx(30 * 1.24 * 1.03, abs=0.1)
    assert e["hours"] == pytest.approx(2.3, abs=0.01)
    assert e["filament_cost"] == pytest.approx(e["grams"] / 1000 * 20, abs=0.01)


def test_snap_lid_validates():
    ctx = {"board": {"width_mm": 40, "height_mm": 30, "thickness_mm": 1.6, "corner_radius_mm": 0},
           "parts": [], "mounting_holes": []}
    errors, _ = enclosure.validate({"lid": {"type": "snap"}}, ctx)
    assert not any("lid.type" in e for e in errors)
    errors, _ = enclosure.validate({"lid": {"type": "glue"}}, ctx)
    assert any("lid.type" in e for e in errors)


def test_turn_cost():
    usage = {"input": 1_000_000, "output": 100_000, "cache_read": 2_000_000}
    assert turn_cost("claude-opus-5-5", usage, {"provider": "anthropic"}) == pytest.approx(4 + 2 + 0.4)
    assert turn_cost("claude-unknown", usage, {"provider": "anthropic"}) is None
    local = {"provider": "openai", "openai_base_url": "http://localhost:8080/v1"}
    assert turn_cost("qwen", usage, local) == 0.0
    paid = {"provider": "openai", "openai_base_url": "https://api.x.ai/v1", "openai_price_in": 3, "openai_price_out": 15}
    assert turn_cost("grok", {"input": 1_000_000, "output": 0}, paid) == 3.0


def test_unreachable_ai_server_says_why():
    """A dropped connection (a forwarder timing out) reads differently from nothing listening at all."""
    import socket
    import threading

    from wholeboard.ai.providers import AIError, OpenAISession

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    dropping = f"http://127.0.0.1:{srv.getsockname()[1]}/v1"

    def drop():
        while True:
            c, _ = srv.accept()
            c.recv(65536)
            c.close()
    threading.Thread(target=drop, daemon=True).start()
    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    closed = f"http://127.0.0.1:{free.getsockname()[1]}/v1"
    free.close()

    said = {}
    for url in (dropping, closed):
        session = OpenAISession({"openai_base_url": url, "openai_model": "m"}, "system", [])
        session.client = session.client.with_options(max_retries=0)
        session.add_user("hi")
        with pytest.raises(AIError) as e:
            session.step()
        said[url] = str(e.value)
    assert "dropped the connection" in said[dropping] and "forwarder" in said[dropping]
    assert said[closed].startswith("Nothing is answering at")
    srv.close()
