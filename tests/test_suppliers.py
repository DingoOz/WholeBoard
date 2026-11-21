from wholeboard.jlc import fabs, suppliers


def _quote():
    return {"quantity": 5, "assembly": {"boards": 2, "type": "economic", "subtotal": 20.0, "bottom_parts": []},
            "components": {"lines": [
                {"refs": ["R1", "R2"], "value": "10k", "lcsc": "C25804", "per_board": 2, "status": "ok", "mpn": "0603WAF1002T5E"},
                {"refs": ["U1"], "value": "TPS1", "lcsc": None, "per_board": 1, "status": "no LCSC number"},
                {"refs": ["U2"], "value": "X", "lcsc": "C1", "per_board": 1, "status": "short stock", "mpn": "ABC-1"}]},
            "pcb": {"subtotal": 4.0}, "shipping": {"subtotal": 10.0}}


def test_wanted_parts_and_links():
    spec = {"components": [{"ref": "U1", "mpn": "TPS62133RGTR"}, {"ref": "U2"}]}
    rows = suppliers.wanted(spec, _quote())
    assert [r["mpn"] for r in rows] == ["TPS62133RGTR", "ABC-1"] and rows[0]["qty"] == 2
    res = suppliers.prices(spec, _quote(), {"suppliers": {}})
    assert not res["keys"] and res["parts"][0]["links"]["mouser"].endswith("TPS62133RGTR") and res["parts"][0]["offers"] == []


def test_price_parsing():
    assert suppliers._money("$1.234") == 1.234 and suppliers._money("1.234,56 €") == 1234.56
    assert suppliers._unit([(1, 0.5), (10, 0.4), (100, 0.3)], 25) == 0.4
    assert suppliers._unit([(10, 0.4)], 2) == 0.4        # below the first break: the first break's price


def test_aisler_and_pcbway_assembly():
    rows = fabs.compare({"board": {"width_mm": 50, "height_mm": 40, "layers": 2}}, _quote())
    a = next(r for r in rows if r["fab"] == "Aisler")
    assert a["boards"] == 5 and a["total"] == round((12 + 20 * 0.067 * 5) * fabs.EUR_USD, 2)
    small = 75 + 0.38 * 4 * 2 + 5 + 20 * 0.095
    assert a["assembly_total"] == round(small * fabs.EUR_USD, 2)
    assert any(r["fab"] == "PCBWay assembly" and r["kind"] == "quote" for r in rows)
    assert rows[0]["assembly_total"] == 20.0


def _fake_jlcsearch(monkeypatch):
    """find_passive against a fixed candidate list: no network, no cache."""
    from wholeboard.jlc import parts
    urls = []
    cands = [{"lcsc": 1, "voltage_rating": 16, "description": "100nF 16V X7R 0603"},
             {"lcsc": 2, "voltage_rating": 100, "description": "100nF 100V X7R 0603"},
             {"lcsc": 3, "voltage_rating": None, "description": "100nF 250V X7R 0603"},     # rating only in the text
             {"lcsc": 4, "voltage_rating": None, "description": ""},                        # unknown: never passes
             {"lcsc": 5, "voltage_rating": 2000, "description": "1nF 2kV C0G 1206"}]
    monkeypatch.setattr(parts, "_cached", lambda key: None)
    monkeypatch.setattr(parts, "_store", lambda key, value: None)
    monkeypatch.setattr(parts, "_get_json", lambda url, timeout=20: urls.append(url) or {"capacitors": cands})
    monkeypatch.setattr(parts, "lookup", lambda code, fresh=False: {"lcsc": code, "stock": 100, "library_type": "basic"})
    return parts, urls


def test_find_passive_filters_by_voltage_rating(monkeypatch):
    """Issue #12: a minimum rating, as an argument or written into the value."""
    parts, urls = _fake_jlcsearch(monkeypatch)
    codes = lambda rows: sorted(r["lcsc"] for r in rows)  # noqa: E731
    assert codes(parts.find_passive("capacitor", "100nF", volts=50, limit=10)) == ["C2", "C3", "C5"]
    assert codes(parts.find_passive("capacitor", "100nF 100V", limit=10)) == ["C2", "C3", "C5"]
    assert "capacitance=1e-07" in urls[-1]                    # the rating is taken out of the value searched for
    assert codes(parts.find_passive("capacitor", "1nF 2kV", limit=10)) == ["C5"]
    assert codes(parts.find_passive("capacitor", "100nF 25V", volts=200, limit=10)) == ["C3", "C5"]   # the higher wins
    assert len(parts.find_passive("capacitor", "100nF", limit=10)) == 5                              # no rating: all


def test_rated_volts_reads_values_and_descriptions():
    from wholeboard.jlc.parts import _rated_volts
    assert _rated_volts("100nF 100V") == 100 and _rated_volts("1nF 2kV") == 2000 and _rated_volts("10uF 6.3V") == 6.3
    assert _rated_volts("100nF") is None and _rated_volts(None) is None
