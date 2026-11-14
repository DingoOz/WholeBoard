# Changelog

Notable changes to WholeBoard. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
WholeBoard has no releases yet, so changes are grouped by date.

## Unreleased

### Added

- Antenna placement checks, from each antenna vendor's own numbers (Espressif, Raytac; a labelled generic figure
  where the vendor gives none, with the source of every figure shown): the antenna end at a board edge, no copper
  under it, no tall or metal parts in its keep-out or in front of it, ground under the module, and the distance to
  the enclosure's walls, lid, floor and metal screws, with a metal case flagged. The printed enclosure moves the wall
  an antenna faces to give it room, and the 3D preview shows each keep-out. The design check warns about antenna
  modules not fixed at a board edge, and the designer places them antenna-out.
- Net tracing in the schematic: pick a net, label, power net, part or bus member (click it, type it, or follow a
  link from the design check or a finding), and its wires, labels and pins are highlighted on every sheet, with a
  minimap, arrows to places off screen, N/P to step through them, per-sheet counts and a checklist of traced nets.
  Imported KiCad projects show their own schematic on the Request step and trace nets there too.
- An `antenna_pi_match` block (0 Ω series part, two unfitted shunt pads) for chip antennas and antenna connectors.
- A VNA step in the bring-up guide for boards with an antenna, and a place to enter the measured resonance and S11,
  kept with the revision and printed in the report.
- Antenna simulation with openEMS in the Simulation step: the antenna, ground plane, nearby metal parts and the
  enclosure, bare and boxed, with both S11 curves and the radiation pattern (`doctor --install openems` builds a
  small Docker image when openEMS is not installed).
- The MCP tool `antenna_check`, and the VNA step in `bringup_guide`.
- Publish a board to GitHub (**⋯ → Publish to GitHub…**): a new repository, private by default, with the KiCad
  project, the files for JLCPCB, renders, enclosure, firmware and a README. Publishing again adds the latest
  build as a new commit. Uses the GitHub CLI's sign-in, or a token in *Settings → GitHub*.
- Design with Claude Code: the MCP server (`python -m wholeboard.mcp`) now has the designer's tools, so Claude Code
  can design a board on your Claude plan while WholeBoard builds it and shows each step live. It starts or opens a
  board, records your answers, and gets messages typed in WholeBoard's chat with its next step.
- *Live view* (in the board's header, or press L): the schematic, the board and the 3D render side by side above
  the steps, each replaced as soon as a newer one is made, with TraceMaker's live router in the board view while it
  routes.
- A *Design with Claude Code* screen (from the home page, Workbench setup and Settings → AI) with the steps to
  set it up: the line that adds WholeBoard to Claude Code for this computer, a button that runs it, and whether
  Claude Code is installed and has WholeBoard yet.
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
- The model search supports Strata 0.2.9 with DeepSeekV4.1-Flash-1017: it also checks Strata's other ports (8095,
  8090), shows the model as taking tools (Strata's own check reads DeepSeek's tool-call format as none) and says
  when a model reads images. DeepSeek's thinking stays in the history so each tool step keeps its reasoning, and
  a local server gets two hours per reply.
- Long conversations with an OpenAI-compatible model stay inside its context window: near 70 % of it, the older
  steps lose their thinking, long tool results and old specs (falling back to a list of the steps taken), and the
  current spec is given again. The window comes from the server (llama.cpp, Strata, vLLM, LM Studio) or *Settings
  → AI → Context window*; a server that says the prompt is too long gets a shortened one.
- A Summary step for a finished board: the render, price, order button, a tile per check, and the designer's
  things to check before ordering as a checklist. Finished boards open on it.
- Board titles: the designer names each board, and you can rename it. The boards list shows a render, how long
  ago it changed and the price, with a search box once there are six or more.
- A Parts table on the Parts step (JLCPCB number, library type, stock) and the request and answers on the
  Requirements step.
- Keyboard shortcuts (press `?`), a draggable divider between the chat and the steps, and *Hide chat*.
- The tab title and icon show when a board needs an answer, finishes or fails, with optional desktop notifications.
- A narrow-window layout with the boards list in a drawer.
- A test that every colour theme keeps text at WCAG AA contrast.
- Roadmap: antenna keep-outs taken from each vendor's guidance, with separate plastic and metal clearances, then
  checked with a VNA in the real enclosure.

### Changed

- When the AI server can't be reached, the message says why: nothing is listening at that address, or something in
  between (such as a proxy or port forwarder) dropped the connection before the model replied.
- Fewer controls in the board header: the outline, design JSON, folder and delete moved to a **⋯** menu, the tool
  status folds into one *Workbench ready* chip, and the colour theme moved to *Settings → Appearance*.
- Steps that finish with warnings show amber. Answered questions fold down, and the designer's final notes fold
  into the chat with a link to the summary.
- Messages and confirmations appear inside the page, not as browser pop-ups.
- Mounting holes, test points, fiducials, net ties and solder jumpers are no longer listed as parts to buy.
- Darker muted and warning text in the light themes, and dark text on the accent colour in the dark ones, for
  contrast.
- Focus outlines for keyboard users, a skip link, labelled controls and less motion when the system asks for it.

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
