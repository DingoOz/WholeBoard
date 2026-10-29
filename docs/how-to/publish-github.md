# Publish a board to GitHub

WholeBoard can put a finished board in a new repository on your GitHub. The repository has the KiCad project, the
files for JLCPCB, the renders and schematic, and the enclosure and firmware when the board has them. It also has
a README with the render, your request, the designer's notes, the parts list and how to order.

## Connect your GitHub account

WholeBoard publishes as the GitHub account on the computer it runs on. Do one of these:

- **Use the GitHub CLI.** Run `gh auth login` once on that computer. WholeBoard uses its sign-in, and stores no
  token of its own.
- **Use a token.** Create a [personal access token](https://github.com/settings/tokens) and paste it into
  *Settings → GitHub*. A fine-grained token needs *Administration* and *Contents* read and write on all
  repositories, so it can create them. A classic token needs the `repo` scope.

*Settings → GitHub* shows which account WholeBoard found. When both are set up, the token in Settings is used.

## Publish

1. Open the board and choose **⋯ → Publish to GitHub…**.
2. Check the repository name and description. The name comes from the board's title.
3. Choose **Private** (the default) or **Public**. WholeBoard asks again before it makes a board public, because
   the README includes your original request.
4. Open *What goes in it* to see each file, and *README* to read the README before it goes up.
5. Click **Create repository**. WholeBoard posts the repository's link in the board's chat when it's done.

## Publish a new revision

After you change and rebuild the board, choose **⋯ → Publish to GitHub…** again. The dialog shows where the
board is published. **Publish revision N** adds one commit to the same repository, with the files of the latest
build. Files the new build no longer has are removed. To publish to a new repository instead, change the name.

## What goes in the repository

| Folder | |
|---|---|
| `README.md` | The render, your request, the designer's notes, the parts list with LCSC links, and how to order |
| `kicad/` | The KiCad project, schematic and board: open the `.kicad_pro` in KiCad |
| `fab/` | The Gerber zip, BOM and CPL for JLCPCB, and the interactive assembly page |
| `images/` | The 3D renders and the schematic as SVG |
| `enclosure/` | The enclosure's STL and STEP files and its FreeCAD model, if the board has one |
| `firmware/` | The starter firmware (PlatformIO), if the board has a microcontroller |
| `docs/` | The bring-up guide, and the PDF report if you made one |
| `design.json` | The design as WholeBoard describes it, to import and rebuild the board |

The chat with the designer, the intermediate boards from each step and WholeBoard's own files stay on your
computer.
