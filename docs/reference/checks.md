# Checks

Everything WholeBoard checks before you order, in the order the build runs them. A check marked **stops** sends the
result back to the designer to fix; the others are warnings shown with the build.

| Check | When | What | Stops |
|---|---|---|---|
| Design check | Before drawing | Footprints and symbols exist; pad names exist; every pad is in a net or `no_connect`; parts are in stock; block ports are connected | yes |
| Mains design | Before drawing | A fuse on the mains input; an isolation barrier when `isolated`; nets that look like mains with mains parts must be declared | yes |
| Fiducials | Before drawing | Parts at 0.5 mm pitch or finer with no fiducials and no panel | warning |
| ERC | After the schematic | KiCad's electrical rules check. Findings that only reflect how WholeBoard draws schematics are filtered out | warning |
| Simulation | After the schematic | ngspice: supplies, ratings (resistor power, LED current, capacitor voltage, regulator heat and dropout), parts outside their supply range, failed `expect` entries | yes |
| Placement | After placement | Every part fits on the board | yes |
| Routing | After routing | Every connection routed | yes |
| DRC | After the copper pour | KiCad's DRC with the board's net classes and `.kicad_dru` ([JLCPCB limits](jlc-limits.md), impedance, mains) | yes |
| Mains spacing | After DRC | Clearance and creepage measured across the barrier, round slots ([Mains requirements](mains.md)) | yes |
| Trace heating | After DRC | Temperature rise of each track for the simulated current, IPC-2152 (or IPC-2221 with `rules.trace_heating: "ipc2221"`) | warning |
| Voltage drop | After DRC | Drop along each power net for the simulated current | warning |
| Decoupling | After DRC | Decoupling capacitors too far from their IC's supply pin | warning |
| Interface routing | After DRC | Length, skew within and between pairs, bus matching, vias and stubs against [interface profiles](profiles.md) | warning |
| CPL rotations | Fab outputs | JLCPCB's rotation for each package (SOT-23, SOIC, QFN …); a component's `cpl` entry overrides it | – |
| Visual review | After a clean build | The AI looks at the renders for parts off the board, blocked connectors, hidden LEDs. Models that cannot see images skip it; *Settings → AI → Vision* turns it off | – |
| Stock | Quote, and *Check stock* | Live JLCPCB stock for the boards to be assembled, with replacements | warning |
| Enclosure | After the enclosure build | Collisions between parts and the box; heat in the box | yes (collisions) |
