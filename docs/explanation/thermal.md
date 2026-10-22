# Heat on the board and in the box

WholeBoard has two heat models that work together.

## On the board: a plate in the browser

The Sim Lab's heat map treats the board as a thin 2D plate. Each part's dissipation, from the simulation, goes
into the plate under the part; it spreads through the FR-4 and the copper (pours, tracks, pads) and is lost to
the air from both faces. It is solved in the browser, so cooling and copper weight change the map at once.

It is good for spotting hot spots and comparing changes. It is not a substitute for a thermal camera.

## In the box: a network of heat paths

Put the board in a box and the air around it is warmer and barely moves. `wholeboard/boxheat.py` models the box as
a small network, with a node for the board, the inside air, and the inside and outside of the lid, the side walls
and the base:

| Path | How |
|---|---|
| Board → air | Weak natural convection, held back by the box |
| Board → walls | Radiation straight to the inside of each wall |
| Air → walls | Natural convection over each wall's inside surface |
| Through the walls | Conduction: a little for printed plastic, nothing for aluminium. Heat also spreads round the box from one face to the next, which makes a metal box one temperature |
| Walls → room | Natural convection and radiation from each outside face; a base on a desk loses little |
| Air → room | Through the openings, by the chimney effect: warm air leaves by lid slots, cooler air comes in low down (side vents, the gaps round connectors). With openings at one height only, air swaps through them both ways, much more slowly |

The board sees a mix of the inside air and the walls. The network gives the temperature that mix amounts to and
how strongly the board loses heat to it; the plate solver and the circuit simulation then use those in place of
open air. So every rating check and part temperature reflects the box.

It is good to a few degrees for small boxes in still air. It is not CFD.

Wall materials: PLA, PETG, ABS, ASA, PC, bare or painted aluminium. Vents: as designed, sealed, lid slots, side
slots, or both. The box can stand on a desk or on feet.
