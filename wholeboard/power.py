"""Power budget and heat: what each supply and regulator delivers, against what it can, from the simulation."""

from __future__ import annotations

from . import design, sim

HOT_C = {"ldo": 125, "converter": 125, "mosfet": 150, "bjt": 150, "diode": 150, "zener": 150, "led": 85,
         "resistor": 155}


def _pct(used: float, limit: float | None) -> float | None:
    return round(100 * used / limit, 1) if limit else None


def budget(spec: dict, res: dict) -> dict:
    """Supplies, regulators, loads per rail, battery life and the hottest parts. Needs a simulation that ran."""
    spec = design.normalise(spec)
    out: dict = {"supplies": [], "regulators": [], "rails": [], "thermal": [], "battery": [], "notes": []}
    if not res.get("ran"):
        return out
    notes = out["notes"]
    amb = float(res.get("ambient_c", 25))
    fp = {c["ref"]: c.get("footprint") or "" for c in spec["components"]}

    for s in res.get("supplies") or []:
        if s["kind"] == "signal":
            continue
        row = {k: s.get(k) for k in ("net", "kind", "volts", "current_ma", "peak_ma", "max_ma", "power_mw")}
        row["use_pct"] = _pct(s.get("peak_ma") or 0, s.get("max_ma"))
        out["supplies"].append(row)
        if row["use_pct"] and 80 <= row["use_pct"] <= 100:
            notes.append(f"{s['net']} ({s['kind']}) runs at {row['use_pct']:.0f}% of what it can supply at peak load")

    for r in res.get("parts") or []:
        if r["kind"] not in ("ldo", "converter"):
            continue
        row = {k: r.get(k) for k in ("ref", "value", "kind", "vin", "vout", "current_ma", "peak_ma", "max_ma",
                                     "power_mw", "temp_c")}
        row["use_pct"] = _pct(max(r.get("current_ma") or 0, r.get("peak_ma") or 0), r.get("max_ma"))
        if r["kind"] == "ldo" and r.get("power_mw"):
            theta = sim._theta(fp.get(r["ref"], ""))
            row["theta_c_w"] = theta
            pw = r["power_mw"] / 1000
            if pw > 0.01:
                # the thermal resistance that keeps the junction at 100 °C, a comfortable margin under 125 °C
                row["theta_needed_c_w"] = round((100 - amb) / pw)
                if row["theta_needed_c_w"] < theta:
                    notes.append(f"{r['ref']} ({r.get('value')}) dissipates {r['power_mw']:.0f} mW: its package on a "
                                 f"plain pad is about {theta:.0f} °C/W, so it needs ≤{row['theta_needed_c_w']} °C/W. "
                                 "Give it a large copper area with vias to an inner plane, lower the input "
                                 "voltage, or use a switching regulator")
            if r.get("vin") and r.get("vout") and r["vin"] > 0:
                row["efficiency_pct"] = round(100 * r["vout"] / r["vin"], 1)
        if row["use_pct"] and row["use_pct"] > 80:
            notes.append(f"{r['ref']} runs at {row['use_pct']:.0f}% of its {r.get('max_ma'):g} mA rating at peak load")
        out["regulators"].append(row)

    rails: dict[str, dict] = {}
    for rail in res.get("rails") or []:
        rails[rail["net"]] = {"net": rail["net"], "volts": rail["volts"], "loads": [], "typ_ma": 0.0, "peak_ma": 0.0}
    for ld in res.get("loads") or []:
        nets = [n for n in ld.get("nets") or [] if n in rails]
        for n in nets:
            share = 1 / len(nets)
            rails[n]["loads"].append({"ref": ld["ref"], "typ_ma": round(ld["typ_ma"] * share, 2),
                                      "peak_ma": round(ld["peak_ma"] * share, 2)})
            rails[n]["typ_ma"] += ld["typ_ma"] * share
            rails[n]["peak_ma"] += ld["peak_ma"] * share
    for r in res.get("parts") or []:
        if r["kind"] == "led" and r.get("current_ma"):
            out.setdefault("leds", []).append({"ref": r["ref"], "current_ma": round(r["current_ma"], 2)})
    for v in rails.values():
        if v["loads"]:
            v["typ_ma"], v["peak_ma"] = round(v["typ_ma"], 2), round(v["peak_ma"], 2)
            v["loads"].sort(key=lambda x: -x["peak_ma"])
            out["rails"].append(v)

    for r in res.get("parts") or []:
        if r.get("temp_c") is None or not r.get("power_mw"):
            continue
        limit = HOT_C.get(r["kind"], 125)
        out["thermal"].append({"ref": r["ref"], "value": r.get("value"), "kind": r["kind"],
                               "power_mw": round(r["power_mw"], 1), "temp_c": round(r["temp_c"], 1),
                               "limit_c": limit, "margin_c": round(limit - r["temp_c"], 1)})
    out["thermal"].sort(key=lambda x: x["margin_c"])
    out["thermal"] = out["thermal"][:10]

    prof = (spec.get("simulation") or {}).get("battery_profile") or {}
    for s in res.get("supplies") or []:
        if s["kind"] != "battery":
            continue
        src = next((x for x in (spec.get("simulation") or {}).get("sources") or [] if x.get("net") == s["net"]), {})
        cap = src.get("capacity_mah")
        if not cap and s.get("battery_hours") and s.get("current_ma"):
            cap = s["battery_hours"] * s["current_ma"]
        if not cap:
            continue
        row = {"net": s["net"], "capacity_mah": round(cap), "typ_ma": s["current_ma"], "peak_ma": s["peak_ma"],
               "hours_typical": round(cap / s["current_ma"], 1) if s["current_ma"] else None,
               "hours_peak": round(cap / s["peak_ma"], 1) if s.get("peak_ma") else None}
        if prof.get("active_pct") is not None:
            act = max(0.0, min(100.0, float(prof["active_pct"]))) / 100
            avg = act * s["current_ma"] + (1 - act) * float(prof.get("sleep_ma") or 0)
            row["profile"] = {"active_pct": act * 100, "sleep_ma": float(prof.get("sleep_ma") or 0),
                              "avg_ma": round(avg, 3), "hours": round(cap / avg, 1) if avg else None}
        out["battery"].append(row)
        if row["hours_typical"] is not None and row["hours_typical"] < 24 and not row.get("profile"):
            notes.append(f"The battery on {s['net']} lasts about {row['hours_typical']:g} h at the simulated "
                         f"{s['current_ma']:.0f} mA; add simulation.battery_profile if the board sleeps")
    return out


def brief(b: dict) -> dict:
    """The parts of the budget the AI needs."""
    return {"supplies": [{k: s[k] for k in ("net", "current_ma", "peak_ma", "max_ma", "use_pct")} for s in b["supplies"]],
            "regulators": [{k: r.get(k) for k in ("ref", "current_ma", "peak_ma", "max_ma", "use_pct", "power_mw",
                                                  "temp_c", "theta_needed_c_w")} for r in b["regulators"]],
            "battery": b["battery"], "hottest": b["thermal"][:3], "notes": b["notes"]}
