# Bugs found while designing the 10-port PoE++ switch (board 20261027-015953-38a2)

Noted 2026-10-27 for later correction. Each entry says where, what happens, and how it was seen.

## 1. Test-point placer ignores 180° and 270° rotations of fixed parts

`wholeboard/design.py`, `_place_test_points`: a fixed part's courtyard box is only adjusted for rotations of
90° (and that adjustment just swaps x and y, without the sign flip a real rotation needs). A part at 180° keeps
its unrotated box, so test pads are placed on top of it.

Seen: J1 (Phoenix MKDS-3 terminal) at rotation 180 → `J1 overlaps TP5` from `simulate_design`. Its real
courtyard was x 13.9..25.0, the placer thought 18.96..30.1.

Fix: rotate the four courtyard corners by the placement angle (as the board builder does) and take the
bounding box.

## 2. Simulator's LED-drive guess treats GND and supply rails as LED nets

`wholeboard/sim.py`, the loop that fills `led_pins` (around line 780): an LED's anode or cathode net
is recorded even when it is a rail (`+3V3`, `GND`). Then any IC pin with a resistor to that rail is "driven
high/low to light its LED". Pull-ups, pull-downs, current-set and current-sense resistors all count.

Seen: `U10 pin 127 (ISET) driven low`, `U10 pin 96 (RESET_N) driven high`, `U20 pin 2/6/9/13 (SEN1..)
driven low`. These come from the 6.04 k ISET resistor, the reset pull-up and the 0.2 Ω sense resistors. The
status LED's cathode is GND and the port LEDs' anodes are +3V3.

Fix: skip rail and ground nets (`self.is_power(net)` or `net == self.ground`) when building `led_pins`.

## 3. TPS563201 block has no `sim.vref`, so its output is simulated at 3.46 V, not 3.3 V

`wholeboard/blocks.py`, `buck_tps563201_*`: U1 has no `"sim": {"vref": 0.768}`. The simulator falls back to
0.8 V, which turns the 33.2 k / 10 k divider into 3.46 V (and the 5 V variant into 5.19 V).

Seen: `U2: feedback reference assumed 0.8 V (set sim.vref)` /
`switching regulator simulated as an ideal 3.46 V supply`.

Fix: add `"sim": {"vref": 0.768}` to the block's U1.

## 4. A regulator the simulator can't recognise leaves its rail floating at nonsense voltages

`wholeboard/sim.py`, `_m_converter`: pins are found only by symbol pin names. A part with no KiCad symbol
(here LM5164, U1) is "switching regulator pins not recognised; left out", even with `sim.vout` / `sim.vref` set.
Its output rail (+12 V) then has loads but no source, and ngspice settles it at about −1002 V. Every part on that
rail is then reported as overstressed (`C13 sees 1001.8 V but is rated 50 V`, `R6 dissipates 1798 mW`, ...),
which buries the real cause.

Fix ideas: (a) when a converter can't be modelled, report "+12V has no source in the simulation (U1 left out)"
and skip rating checks on that rail; (b) let `sim` name the pins for parts without a symbol (for example
`"sim": {"pins_map": {"VIN": "2", "FB": "5", "GND": "1", "EN": "3"}}`), or accept `sim.vout` plus a `sim.in` and
`sim.out` net.

## 5. Placement timeout is reported as "did not finish (exit code -9)"

`wholeboard/router.py`, `place`: when the routable placement pass overruns its limit (`place_time_s × 3 + 120`
s), `_stream` kills it. The log then says `Routability-checked placement did not finish (exit code -9)`, which
reads like a crash or out-of-memory, not a timeout (this machine has 184 GB free).

Fix: report "timed out after N s" when the kill came from the timer.

## 6. TraceMaker: shortest-first variants stall after about 50 connections on a large board

With a 240 × 140 mm, 4-layer board at a 0.1 mm pitch (448 nets, 2129 pads), variants 0, 1, 3 and 4 each routed
exactly 50 connections and then used the rest of their 120 s. The stuck report marks all 1070 remaining
connections, including 0.4 mm hops between neighbouring GND pins, as `not_attempted`. One connection may be
eating the whole budget; a per-connection cap would stop that. Report:
`~/WholeBoardProjects/20261027-015953-38a2-*/rev1/poe_gbe_switch_10p-3-routed.stuck.json`.

## 7. The board link handed to Claude Code only works on the machine running WholeBoard

`wholeboard/mcp.py`, `new_board` / `open_board`: the link handed out is `http://127.0.0.1:8770/#<id>` (from
`_base()`, unless `WHOLEBOARD_URL` is set), so pasting it on another device on the LAN or Tailscale fails even
though the server listens on 0.0.0.0:8770. (The router live view is fine: the web UI rebuilds its link from
`location.hostname` with the viewer token.)

Fix: when WholeBoard is in LAN mode, hand out the LAN address (or a configurable public base URL) alongside the
local one.

## 8. Routing failure costs a second full routing run

`wholeboard/pipeline.py`: when the first route is incomplete it runs an ECO placement and routes again with the
same time limit. With `route_time_s` raised for a large board, a failing build takes about twice as long as the
limit suggests. Worth logging up front, or skipping the retry when the first pass ran out of time without
trying most connections (`not_attempted`).

Seen again on rev2: the first pass routed 1184/1194 in 7216 s. The ECO pass then reported `0 parts near 9 failed
connections, 0 legal moves` and `0 footprints moved`, but the retry still removed all 8428 unlocked tracks and
vias and started another full 7200 s route of the same placement. When the ECO moves nothing, the retry could keep
the routed board and only rip up the area round the 10 failures (or be skipped).

## 9. (Feature gap) `find_passive` can't filter by voltage rating

`find_passive` rejects values like `100nF 100V` or `1nF 2kV`. PoE and 54 V designs need voltage-rated
ceramics, so they fall back to `search_parts` keyword searches. A `volts` (minimum rating) argument would help.

## 10. Custom rules in the `.kicad_dru` turn off TraceMaker's GPU fields and per-class cache

**Fixed 2026-10-27** on TraceMaker branch `cache-safe-custom-rules` (D139, not merged): exact mode now only for clearance
rules that name nets and for net-aware physical_hole_clearance rules; cached via codes probe a via.

TraceMaker `src/drc/rule_engine.cpp` (around line 489) sets `needs_exact_` for any rule with an unreadable condition
and for any constraint other than `disallow` (or a net-blind `physical_hole_clearance`). The router then runs with
`use_cache = false` (`router.cpp:349`), and `use_field` requires `use_cache` (`router.cpp:1568`), so no cost-to-go
field is built, on the GPU or the CPU. WholeBoard writes its JLCPCB rules (`wholeboard/jlc/capabilities.json`) into
every board's `.kicad_dru`, and they are `track_width`, `clearance`, `hole_to_hole` and similar, so every
WholeBoard board routes this way. One of them, `"JLCPCB: Hole to hole"`, also uses `A.Net != B.Net`, which
TraceMaker can't evaluate (`condition uses Net ... rule not applied`).

Seen: rev2 of this board, 8 variants × 7200 s, every variant logged `fields 0 GPU + 0 CPU` and about 4×10⁹
exact legality checks. The P100 and V100 held a 256 / 308 MiB context at 0 % use the whole time. The NE555 board
(20261026-204243-6881) shows the same. Older boards (no saved routed `.kicad_dru` to compare) did log CPU fields.

Fix ideas: most of these JLCPCB limits are looser than the board's own net-class rules (0.09 mm clearance against
0.15 mm), so they never bind; TraceMaker could fold a rule into the per-class cache when it is a plain minimum
with no positional condition, and keep exact mode for rules that can actually bind. Teach the condition parser
`A.Net` / `B.Net`. (The older boards' CPU fields with 0 GPU fallbacks mean those runs had no GPU device selected, `gpu_device = -1`;
now the first card is picked up (both get a context from device listing), so fixing the cache would put fields on the GPU.)

Measured 2026-10-27 on the rev2 placed board, 300 s per run, same machine: with WholeBoard's `.kicad_dru` the
eight variants routed 116, 116, 221, 116, 116, 1079, 116 and 92 connections (no fields, 1189 commits). Without it
they routed 829, 984, 1050, 963, 820, 1098, 990 and 403 (7059 GPU fields, 0 fallbacks, 8117 commits). The best
variant gains little, but every other variant is 4–9× further along. (Dropping the file also drops limits that
can bind, such as 0.5 mm hole-to-hole and 0.3 mm to the edge, so the fix must keep those, not delete the file.)

## 11. WholeBoard's JLCPCB clearance rule lowered every board's clearance to the board minimum

`wholeboard/kicad/dru.py` wrote `(rule "JLCPCB: Copper clearance" (condition "A.Type != 'Zone' && B.Type != 'Zone'")
(constraint clearance (min 0.09mm)))`. In KiCad (and TraceMaker) a custom clearance rule replaces the net-class
clearance, it is not a floor. So this board's 0.15 mm classes were routed and DRC-checked at 0.127 mm (the
`min_clearance` board minimum) for everything but zones. It also made six of TraceMaker's eight variants crawl
(see 13).

**Fixed 2026-10-27** (uncommitted): the rule is no longer written; `min_clearance` in the design settings (never below
JLCPCB's) is the floor. `tests/test_fab.py` checks no clearance constraint is written.

## 12. TraceMaker's router ignores custom `hole_to_hole`, `hole_clearance` and `edge_clearance` rules

`src/route/obstacles.cpp` (`holes_edges_state`, `fixed_code`, `fixed_via_code`) uses only the board minimums
(`r_.minimums.hole_clearance`, `hole_to_hole`, `copper_edge_clearance`). WholeBoard's rules ask for 0.254 mm hole to
copper (board minimum 0.2) and 0.5 mm hole to hole between nets (board minimum 0.25), so the router can place vias the
KiCad DRC then flags. Doc 05 line 137 lists these among the rules the router honours. (The 0.5 mm rule also uses
`A.Net`, which TraceMaker can't read, so TraceMaker's own DRC skips it too.)

Fix: read the custom minimums through `RuleEngine::hole_clearance` / `hole_to_hole` / `edge_clearance` (unconditional
ones can simply raise the minimums the cache uses); teach the condition parser `Net`. Workaround: put the stricter
values in the project's design settings minimums.

## 13. TraceMaker variants stall when a rule's clearance is below the net class's

Measured with D139's engine (records in TraceMaker `bench/records/d139/`): a non-zone clearance rule of 0.09 mm
(effective 0.127 mm) under 0.15 mm classes left variants 0, 1, 3, 4, 6 and 7 at about 116 connections after 300 s;
without the rule they reach 880–1000. Checking the condition costs nothing measurable; the smaller clearance does,
most when zones keep the class's 0.15 mm. Probably something sized from the class clearance (lattice margin,
escapes, diff-pair geometry) disagreeing with the rule engine. Open.

## 14. Cancel does not stop a running TraceMaker route

`wholeboard/router.py`, `_stream`: the cancel event is only checked when the child prints a line. `tracemaker route`
prints nothing for the whole run, so `POST /api/projects/<id>/cancel` returned ok and the router kept going (seen
2026-10-27: still running 2+ minutes after cancel; stopped with `kill`). Fix: poll the event on a timer (or a
thread waiting on it) and kill the process from there, as the timeout timer already does.

## 15. TraceMaker's per-class caches have no memory bound; WholeBoard made one class per impedance pair

TraceMaker `src/route/router.cpp`, `cache_for`: each (net class, track width) gets three whole-board arrays
(`margin`, `tight`, `via`; about 120 MB at 240 x 140 mm, 4 layers, 0.1 mm), per variant. WholeBoard's
`impedance.netclasses` wrote one class per impedance group, so this board had 83 classes. Once D139 turned the cache
on, the eight variants reached 181 GB of 184 GB after 17 minutes of routing (seen 2026-10-27 07:15; the router was
killed by hand before the OOM killer acted, and Claude Code's background shells were reaped for memory).

**WholeBoard side fixed 2026-10-27** (uncommitted): groups with the same impedance, width, gap and kind share one
class (80 pairs → 1 class here); `tests/test_fab.py` covers it. TraceMaker side open: share caches between classes
with identical values, bound the total, or size the arrays to the routed windows.

## 16. (Feature gap) Nothing checks the circuit again once the traces are laid out

`wholeboard/pipeline.py`: ngspice runs once, on the schematic netlist before placement (line 198), with ideal wires
and switching regulators as ideal supplies. After routing, `checks.run` reuses that simulation's per-net currents
for trace heating, voltage drop and decoupling, and `impedance.check` measures pair widths and lengths. Nothing
re-simulates with the routed copper, and nothing looks at layout-dependent behaviour of converters or fast buses.
Ideas, cheapest first:

- **Post-layout re-simulation.** Put each power net's routed copper resistance (already computed by
  `checks._graph_drop`) into the ngspice netlist as series resistors between the source and each load pin, and
  simulate again: rail voltages at the real pins, not at an ideal node. On this board: do the 1.22 V core rails
  hold at the far KSZ9897 pins?
- **Converter hot-loop check.** For each switching regulator block, the area (and rough inductance) of the loop
  from input capacitor to switch node to ground return, warned above a limit from the datasheet's layout guidance.
- **Bus length matching.** Match lengths across a whole bus such as RGMII (TX_CTL/TXD0-3/TXC, RX side the same),
  not only within a pair; `impedance.check` already measures lengths.
- **High-speed spacing.** A spacing rule between fast traces (3W or similar), as a stand-in for crosstalk.
- **Return path.** Warn when a fast or critical trace crosses a void or split in its reference plane; TraceMaker's
  router already knows where the plane voids are (critical nets over voids, issue #24).

Not proposed: field-solver signal- or power-integrity simulation (openEMS and the like). It is slow, needs IBIS
models that JLCPCB parts rarely have, and is hard to run unattended; boards that need it belong in a dedicated tool.
