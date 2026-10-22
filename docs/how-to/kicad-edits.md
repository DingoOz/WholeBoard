# Edit the board in KiCad

WholeBoard's output is an ordinary KiCad project, and changes you make to it are kept.

1. Open the board from the revision's folder (`~/WholeBoardProjects/<project>/revN/`) in KiCad.
2. Move parts, reroute or widen tracks, and save.
3. Ask the designer for a change, or press rebuild.

The next build notices:

- parts you moved keep their positions (they come back as placements marked `"from": "kicad"`);
- tracks and vias you edited are kept as locked copper (`fixed_copper`), and TraceMaker routes only what is left.

The chat says what was kept, and *History* marks the revision as edited in KiCad.

## Start the layout afresh

Set `"keep_kicad_edits": false` in the design (*Edit design JSON*), or ask the designer to start the layout again.
