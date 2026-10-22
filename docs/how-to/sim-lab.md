# Explore the simulation in the Sim Lab

Once the board is routed, the **Simulation** step opens the Sim Lab: the finished board in 3D (drag to turn,
wheel to zoom, right-drag to pan) with the ngspice results drawn on it.

## What it shows

- **Current flow.** Dashes run along each track from the supply to the parts and back on ground, brighter and
  faster where more current flows.
- **LED glow.** Each LED lights in its own colour as brightly as its simulated current; RGB LEDs mix their
  channels.
- **Heat map.** The board and every part are coloured by temperature ([how it is worked out](../explanation/thermal.md)).
- **Risks.** Simulation problems, board-check findings, power-budget notes, LEDs over their current and the
  hottest parts are listed beside the board.

## Try a change

1. Click a risk to fly to the parts it names and get the sliders that change it.
2. Move a slider. Resistor values, supply voltages, IC load currents and the air temperature re-run ngspice in
   under a second; cooling (still air to a fan) and copper weight change the heat map at once.
3. To keep a change, ask the designer in the chat. The sliders never change the design.

## Put the board in its box

Choose the printed enclosure, your stock case, or (before one is designed) the box WholeBoard would suggest.
Boards with an enclosure start in it. The box is drawn see-through and coloured by its surface temperatures, and
the circuit is re-simulated in what the board sees inside: warmer air that barely moves, plus the cooler walls it
radiates to. Every rating check, part temperature and the heat map then reflect the box.

The controls choose:

- the vents: as designed, sealed, lid slots, side slots, or both;
- the wall material: PLA, PETG, ABS, ASA, PC, bare or painted aluminium;
- how the box stands: on a desk, or on feet or hanging.

The readout gives the inside air, the lid, wall and base temperatures and the air flowing through the vents.

## Tour and video

*Play tour* flies through the board: current, LEDs, heat, the board in its box with and without vents, each risk
and a what-if. *Record video* saves the same tour as a 1280 × 720 MP4 with the project's files. You can also ask
the designer in the chat for a video demo.
