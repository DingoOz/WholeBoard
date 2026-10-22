# Decisions

Architecture decision records: one short page per choice that is not obvious from the code, with what was
considered and why. They are numbered in the order they were made and are not edited afterwards; a later decision
that changes one says so and links back.

| # | Decision | Status |
|---|---|---|
| [1](0001-docs-generated-from-code.md) | Reference pages are generated from the code | Accepted |
| [2](0002-functional-spacing-between-declared-mains-nets.md) | Functional mains spacing only between declared mains nets | Accepted |
| [3](0003-surface-rule-for-creepage.md) | A surface clearance rule stands in for creepage | Accepted |
| [4](0004-rules-beside-every-board.md) | The project and rules files go beside every board TraceMaker sees | Accepted |
| [5](0005-kikit-settings.md) | KiKit settings: mill radius, V-cut layer, copy numbering | Accepted |
| [6](0006-price-tables-not-scraping.md) | Board prices from editable tables, not scraping | Accepted |
| [7](0007-worker-and-expert.md) | A worker model and an expert model | Accepted |

## Adding one

Copy the layout of an existing record into `docs/decisions/NNNN-short-title.md`, add it to this table and to
`nav` in `mkdocs.yml`. Keep it to a screen: context, decision, consequences.
