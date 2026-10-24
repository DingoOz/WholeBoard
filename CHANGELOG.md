# Changelog

Notable changes to WholeBoard. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
WholeBoard has no releases yet, so changes are grouped by date.

## Unreleased

### Added

- Documentation site (MkDocs Material) with tutorials, how-to guides, generated reference pages, explanation,
  standards and sources, and decision records. Published to GitHub Pages.
- Clear the boards list without deleting any boards, and show the cleared boards again.
- Zoom the whole interface from the top bar (50–200 %, also Ctrl+Alt with + / − / 0).
- Drag handles to resize the board description and chat message boxes.
- Search for local models in Settings: finds Ollama, LM Studio, llama.cpp, Strata, vLLM, Jan and other
  OpenAI-compatible servers on the computer running WholeBoard, shows each chat model with its size, context
  and tool-calling support, and fills in the settings for the one picked.
- Search the LAN for model servers: checks every address in the subnet the computer is on (a subnet larger than a
  /22 is narrowed to the /24 around it), only within the private ranges.
- Roadmap: antenna keep-outs taken from each vendor's guidance, with separate plastic and metal clearances, then
  checked with a VNA in the real enclosure.

## 2026-10-22

### Added

- Interface profiles (`usb2_hs`, `usb2_fs`, `ethernet_100`, `rmii`, `qspi_flash`, `rf_50`) with length, skew,
  bus matching, via and stub limits, carried by the USB, Ethernet and RP2040 blocks.
- Mains safety: a `mains` section, fuse and isolation-barrier checks, IPC-2221 functional and IEC 62368-1
  reinforced spacing in the net classes and rules file, isolation slots under barrier parts, and a creepage check
  after DRC.
- Panels drawn with KiKit (V-cut or mouse-bite tabs, rails, tooling holes, fiducials), with panel Gerbers, BOM
  and CPL; fiducials on single boards.
- Mouser and DigiKey prices for parts JLCPCB cannot fit; Aisler boards, stencil and assembly; a PCBWay assembly
  link.
- An MCP server (`python -m wholeboard.mcp`) with simulate, power budget, impedance, board checks, quote, stock
  check and bring-up guide tools.
- The benchmark (`python -m wholeboard.bench`) with fifteen reference prompts.
- KiCad hand edits kept across rebuilds; a JLCPCB design-rules file; parts on both sides; fourteen more building
  blocks; IPC-2152 trace heating; heat inside the enclosure.

### Changed

- TraceMaker now routes to the `.kicad_dru` rules, not only the final DRC.
- Nets that look like mains, together with mains-type parts, stop the build until `mains` is declared.

## 2026-10-21

### Added

- The Sim Lab: the simulation on the 3D board, with current flow, LED glow, a heat map, risks and sliders, a tour
  and video recording.
- A guided tour of the interface.
- Building blocks, an expert model, power and impedance checks, bring-up guide, case fitting, other fabs and the
  project report.
