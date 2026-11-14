"""Solve one antenna model with openEMS (FDTD). Run on its own: python3 antenna_ems.py model.json out_dir

It imports nothing from WholeBoard, so it runs in any Python that has openEMS (a native install, or the Debian image
WholeBoard builds). The model (from wholeboard.antenna_sim.build_model) lists metal sheets and blocks, dielectric
blocks, the lumped feed port and the frequency range, in mm. Each variant ("board", "boxed") is solved separately;
results.json gets the return loss (S11) per variant and the radiation pattern of the last one at its resonance.
"""

import json
import math
import os
import sys
import time

EPS0 = 8.854187817e-12


def merge(lines: list, gap: float) -> list:
    """Mesh lines with none closer than gap: the FDTD time step shrinks with the smallest cell."""
    out = []
    for v in lines:
        if out and v - out[-1] < gap:
            continue
        out.append(v)
    if len(lines) > 1 and out[-1] != lines[-1]:
        out[-1] = lines[-1]                 # keep the outer boundary
    return out


def mesh_stats(model: dict, variant: dict) -> dict:
    """The mesh solve() would use, without solving: cells per axis and the smallest cell."""
    return solve(model, variant, "", False, dry=True)


def solve(model: dict, variant: dict, out_dir: str, pattern: bool, dry: bool = False) -> dict:
    import numpy as np
    for alias, kind in (("float", float), ("int", int), ("complex", complex)):   # older openEMS (Debian 12) uses them
        if alias not in np.__dict__:
            setattr(np, alias, kind)
    from CSXCAD import ContinuousStructure
    from openEMS import openEMS

    f0, fc = model["f0_hz"], model["fc_hz"]
    fdtd = openEMS(NrTS=int(model.get("max_steps", 40000)), EndCriteria=float(model.get("end_criteria", 1e-4)))
    fdtd.SetGaussExcite(f0, fc)
    fdtd.SetBoundaryCond(["MUR"] * 6)
    csx = ContinuousStructure()
    fdtd.SetCSX(csx)
    mesh = csx.GetGrid()
    mesh.SetDeltaUnit(1e-3)
    lines = {"x": set(), "y": set(), "z": set()}

    def edges(b0, b1, fine=False):
        for i, ax in enumerate("xyz"):
            lines[ax].add(round(b0[i], 3))
            lines[ax].add(round(b1[i], 3))
            if fine and abs(b1[i] - b0[i]) > 1e-6:
                n = max(1, int(abs(b1[i] - b0[i]) / model["fine_mm"]))
                for k in range(1, n):
                    lines[ax].add(round(b0[i] + (b1[i] - b0[i]) * k / n, 3))

    for i, d in enumerate(model["dielectrics"] + variant.get("dielectrics", [])):
        kappa = 2 * math.pi * f0 * EPS0 * d["eps"] * d.get("tand", 0.0)
        m = csx.AddMaterial(f"diel{i}", epsilon=d["eps"], kappa=kappa)
        m.AddBox(d["a"], d["b"], priority=d.get("priority", 1))
        edges(d["a"], d["b"])
    for i, s in enumerate(model["metal"] + variant.get("metal", [])):
        m = csx.AddMetal(f"metal{i}")
        m.AddBox(s["a"], s["b"], priority=s.get("priority", 10))
        edges(s["a"], s["b"], fine=s.get("fine", False))
    p = model["port"]
    port = fdtd.AddLumpedPort(1, p["ohms"], p["a"], p["b"], p["dir"], 1.0, priority=5)
    edges(p["a"], p["b"], fine=True)

    lo, hi = model["domain"]
    gap = model.get("min_cell_mm", model["fine_mm"] / 2)
    # resolve the board in z: the sheets on its faces and the feed need cells of their own on both sides
    th = model.get("board_mm", 1.6)
    lines["z"].update(round(v, 3) for v in (-2.0, -1.0, -0.5, th / 2, th + 0.5, th + 1.0, th + 2.0, th + 3.5))
    for i, ax in enumerate("xyz"):
        lines[ax].add(lo[i])
        lines[ax].add(hi[i])
        mesh.AddLine(ax, merge(sorted(lines[ax]), gap if ax != "z" else min(gap, 0.4)))
    mesh.SmoothMeshLines("all", model["coarse_mm"], 1.4)
    for ax in "xyz":                       # smoothing can add lines closer than the gap; the time step follows them
        mesh.SetLines(ax, merge(sorted(float(v) for v in mesh.GetLines(ax)), gap if ax != "z" else min(gap, 0.4)))
    if dry:
        ls = {ax: [float(v) for v in mesh.GetLines(ax)] for ax in "xyz"}
        return {"cells": [len(ls[ax]) for ax in "xyz"],
                "min_mm": {ax: round(min(b - a for a, b in zip(ls[ax], ls[ax][1:])), 3) for ax in "xyz"}}
    nf2ff = None
    if pattern:
        # inside the air margin: on the outer grid lines the MUR boundary has already absorbed the fields
        inset = [min(model.get("nf2ff_inset_mm", 10.0), (hi[i] - lo[i]) / 4) for i in range(3)]
        nf2ff = fdtd.CreateNF2FFBox(start=[lo[i] + inset[i] for i in range(3)], stop=[hi[i] - inset[i] for i in range(3)])

    path = os.path.join(out_dir, variant["name"])
    os.makedirs(path, exist_ok=True)
    t0 = time.time()
    fdtd.Run(path, cleanup=True, verbose=0)
    f = np.linspace(model["f_start_hz"], model["f_stop_hz"], 401)
    port.CalcPort(path, f)
    s11 = port.uf_ref / port.uf_inc
    s11_db = 20 * np.log10(np.maximum(np.abs(s11), 1e-9))
    k = int(np.argmin(s11_db))
    res = {"name": variant["name"], "title": variant["title"], "seconds": round(time.time() - t0, 1),
           "cells": [len(mesh.GetLines(ax)) for ax in "xyz"],
           "freq_mhz": [round(x / 1e6, 2) for x in f], "s11_db": [round(float(x), 2) for x in s11_db],
           "resonance_mhz": round(float(f[k]) / 1e6, 1), "s11_min_db": round(float(s11_db[k]), 2)}
    if nf2ff is not None:
        theta = np.arange(0.0, 181.0, 6.0)
        phi = np.arange(0.0, 361.0, 10.0)
        center = [c * 1e-3 for c in model.get("center", [0, 0, 0])]      # CalcNF2FF wants metres
        r = nf2ff.CalcNF2FF(path, float(f[k]), theta, phi, center=center)
        e = np.asarray(r.E_norm[0]) / np.max(r.E_norm[0])
        dmax = float(np.ravel(r.Dmax)[0])
        gain = 20 * np.log10(np.maximum(e, 1e-6)) + 10 * math.log10(max(dmax, 1e-9))
        res["pattern"] = {"theta_deg": theta.tolist(), "phi_deg": phi.tolist(),
                          "dbi": [[round(float(v), 2) for v in row] for row in gain],
                          "directivity_dbi": round(10 * math.log10(max(dmax, 1e-9)), 2)}
    return res


def main() -> None:
    model_file, out_dir = sys.argv[1], sys.argv[2]
    with open(model_file) as fh:
        model = json.load(fh)
    out = {"variants": [], "ok": True}
    for i, v in enumerate(model["variants"]):
        print(f"solving {v['name']}", flush=True)
        out["variants"].append(solve(model, v, out_dir, pattern=i == len(model["variants"]) - 1))
        with open(os.path.join(out_dir, "results.json"), "w") as fh:
            json.dump(out, fh)
    print("done", flush=True)


if __name__ == "__main__":
    main()
