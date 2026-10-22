# Exchange designs with Altium

## WholeBoard to Altium

WholeBoard's output is an ordinary KiCad project (in `exports/` as a zip, or the `revN` folder).

1. In Altium Designer, choose *File → Import Wizard → KiCad Design Files*.
2. Choose the `.kicad_pro`.

The importer is an Altium extension; install it from *Extensions and Updates* if the wizard does not list KiCad.
Schematic, board, net classes and rules come across. Check the design rules and polygon pours after the import,
since the two tools treat them differently.

## Altium to WholeBoard

1. In KiCad, use *File → Import → Non-KiCad Project…*, which reads `.PrjPcb`, `.SchDoc` and `.PcbDoc`.
2. Save it as a KiCad project.
3. In WholeBoard, use [Import KiCad project…](import-kicad.md).

Footprints from Altium libraries arrive as project-specific footprints, which WholeBoard asks to replace with
standard KiCad ones.

!!! note
    These menu paths have not been checked against every Altium version. Corrections are welcome.
