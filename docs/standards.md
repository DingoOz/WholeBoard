# Standards and sources

Every engineering figure and outside price WholeBoard relies on, where it comes from, where it lives in the code,
and when it was last checked. When a source changes, update the code and the date here.

!!! note "Checking a figure"
    The tables in [Mains requirements](reference/mains.md), [Interface profiles](reference/profiles.md) and
    [JLCPCB limits](reference/jlc-limits.md) are generated from the code, so they always show what WholeBoard
    actually uses. This page says where those numbers came from.

## Electrical safety

| Figure | Source | In the code | Checked |
|---|---|---|---|
| Rated impulse voltage for the mains voltage and overvoltage category | IEC 60664-1, table F.1 | `mains.IMPULSE` | 2026-10 |
| Clearance for an impulse voltage (inhomogeneous field, pollution degree 2) | IEC 60664-1, table F.2 | `mains.CLEAR_IMPULSE` | 2026-10 |
| Reinforced clearance: the clearance for the next impulse step up (and never less than IPC-2221 B2) | IEC 60664-1, IEC 62368-1 | `mains.requirements` | 2026-10 |
| Creepage by working voltage, pollution degree 2, material group III | IEC 60664-1, table F.4 | `mains.CREEP` | 2026-10 |
| Reinforced creepage: twice basic (and never less than reinforced clearance) | IEC 60664-1, IEC 62368-1 | `mains.requirements` | 2026-10 |
| A slot counts when at least 1 mm wide (the dimension X for pollution degree 2); also JLCPCB's narrowest routed slot | IEC 62368-1 | `mains.SLOT_W` | 2026-10 |
| Spacing between conductors by peak voltage (functional) | IPC-2221, table 6-1, columns B1 (inner) and B2 (outer, uncoated) | `mains.IPC_B1`, `mains.IPC_B2` | 2026-10 |

FR-4 is taken as material group IIIb (CTI 100–175), and the creepage table's next row up is used rather than
interpolating. Both choices are conservative.

## Board physics

| Figure | Source | In the code | Checked |
|---|---|---|---|
| Track temperature rise | IPC-2152 (2009), baseline chart: a curve fit for a track alone in a 1.6 mm board | `checks._area_2152` | 2026-10 |
| Track temperature rise, older method | IPC-2221, the external and internal conductor charts | `checks.trace_rise_c` | 2026-10 |
| Copper resistivity, 1.72 × 10⁻⁸ Ω·m at 20 °C | Annealed copper | `checks.CU_RHO` | – |
| Copper thickness: 35 µm outer, 17.5 µm inner | JLCPCB standard stack-up | `checks.CU_THICK_MM` | 2026-10 |
| Microstrip and coupled-microstrip impedance | IPC-2141A formulas | `impedance.microstrip`, `impedance.diff_microstrip` | 2026-10 |
| Stack-ups JLC04161H-7628 and JLC06161H-2116 | JLCPCB impedance stack-up pages | `impedance.STACKUPS` | 2026-10 |

## Interface limits

| Interface | Basis for the limits | In the code |
|---|---|---|
| USB 2.0 high speed, full speed | USB 2.0 specification §7.1; USB-IF and chip makers' layout guides (90 Ω ±15 %) | `impedance.PROFILES` |
| 100BASE-TX | IEEE 802.3 clause 25 (100 Ω); PHY layout guides, e.g. Microchip LAN8720A | `impedance.PROFILES` |
| RMII | RMII specification 1.2 (50 MHz reference clock); PHY layout guides | `impedance.PROFILES` |
| QSPI flash | Raspberry Pi *Hardware design with RP2040* | `impedance.PROFILES` |
| 50 Ω RF feed | Module makers' antenna layout guides (Espressif, Nordic) | `impedance.PROFILES` |

Length and skew limits are layout-guide rules of thumb, chosen on the safe side. They are not compliance tests.

## Manufacturers and suppliers

| What | Source | In the code | Checked |
|---|---|---|---|
| JLCPCB manufacturing limits | <https://jlcpcb.com/capabilities/pcb-capabilities>, <https://jlcpcb.com/help/article/via-in-pad> | `jlc/capabilities.json` | 2026-10-22 |
| JLCPCB board, assembly and shipping prices | JLCPCB help articles and the quote page (no formula is published) | `jlc/pricing.json` | 2026-10-20 |
| JLCPCB part prices and stock | Live, JLCPCB's parts library | `jlc/parts.py` | live |
| JLCPCB CPL rotations by package | The offsets community tools apply (kicad-jlcpcb-tools, JLC2KiCad) | `kicad/cli.CPL_ROTATIONS` | 2026-10 |
| OSH Park | Published price per square inch | `jlc/fabs.OSHPARK` | 2026-10 |
| Aisler boards, stencil and assembly | Aisler community forum, "Our simple pricing" (2023) | `jlc/fabs.AISLER*` | **2023 figures**, may be out of date |
| PCBWay | Promotional small-board price; assembly quoted by hand | `jlc/fabs.pcbway` | 2026-10 |
| Euro to US dollar | Fixed at 1.10 | `jlc/fabs.EUR_USD` | 2026-10 |
| Mouser | Search API v1, part-number search | `jlc/suppliers.mouser` | not yet tested with a key |
| DigiKey | Product Information API v4, OAuth client credentials | `jlc/suppliers.digikey` | not yet tested with a key |

## Thermal

| Figure | Source | In the code |
|---|---|---|
| Natural convection from the board and the box's faces | Simplified laminar correlations for still air, h = k (ΔT / L)^¼ with k = 1.32 facing up, 1.42 vertical, 0.59 facing down | `boxheat._h_conv` |
| Flow through vents | Buoyancy-driven (stack) flow through orifices, discharge coefficient 0.6; two-way exchange through openings at one height | `boxheat._vent_flow` |
| Radiation | Linearised Stefan–Boltzmann | `boxheat._h_rad` |
| Wall conductivity and emissivity by material | Typical datasheet values | `boxheat.MATERIALS` |
