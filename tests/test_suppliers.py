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
