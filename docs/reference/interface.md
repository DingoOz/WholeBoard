# The interface

## Boards

The list on the left shows each board with its render, how long ago it changed and its price. A search box appears
once there are six or more. *Clear* hides every board from the list without deleting anything; *Show N cleared
boards* brings them back.

A board is named from its design (the designer gives each one a short title). Double-click the name above the
chat, or press **✎**, to rename it: **Enter** saves, **Esc** cancels, and an empty name goes back to the
designer's.

On a narrow window the list folds away behind **☰**.

## The step bar

Each step shows a green dot when it is done, amber when it finished with warnings, red on an error, and a spinner
while it runs. Hover a step for its full result. The last step, *Summary*, gathers the finished board on one page:
the render, price, order button, a tile per check and the designer's list of things to check before ordering. The
ticks on that list are kept in your browser for each revision.

A finished board opens on its summary.

## Live view

*Live view* in the board's header adds a strip above the steps with the schematic, the board and the 3D render
side by side. Each one changes as soon as a newer one is made: the board shows the footprints, then the placed
parts with their air wires, then TraceMaker routing it live, then the finished copper and pour. A spinner marks
the one being worked on. During a rebuild, views the build hasn't reached yet are dimmed and marked *from the last
build*. Click a view's name to open its step. Drag the strip's bottom-right corner to make it taller. WholeBoard
remembers whether it is on, in this browser.

## Chat

Drag the divider between the chat and the steps to resize them; double-click it to go back to the default width.
*Hide chat* gives the steps the whole width. Answered questions fold down to the question and your answer.

## The ⋯ menu

| Item | |
|---|---|
| Board outline from DXF… | [Fit a board to a case](../how-to/case-fit.md) |
| Edit design JSON… | Change the design by hand and rebuild without the AI |
| Publish to GitHub… | [Put the latest build in a repository on your GitHub](../how-to/publish-github.md) |
| Open folder | The board's folder in your file manager |
| Delete board… | Deletes the board and all its files, after asking |

## While you work elsewhere

The tab's title and icon change when a board needs an answer (**?**), finishes (**✓**), stops on an error (**!**)
or is still building (**…**). Tick *Tell me when a board needs an answer or finishes* in *Settings → Appearance*
for a desktop notification as well.

## Keyboard shortcuts

Press **?** to see these in WholeBoard.

| Keys | |
|---|---|
| ← → | Previous or next build step |
| S | Summary of the board |
| / | Write to the designer |
| C | Hide or show the chat |
| L | Live view: schematic, board and 3D side by side |
| B | Search your boards |
| N | New board |
| Ctrl+Alt + / − / 0 | Zoom in, out, back to 100 % |
| Esc | Close a dialog or menu |
| ? | The list of shortcuts |

Shortcuts are ignored while you type in a box. In the step bar, **Home** and **End** go to the first and last
step.
