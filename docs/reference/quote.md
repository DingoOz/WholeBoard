# The quote

| Part of the quote | Where it comes from |
|---|---|
| Component prices, stock, basic/preferred/extended status | **Live**, from JLCPCB's parts library |
| PCB fabrication, assembly fees, shipping | **Estimated**, from [`wholeboard/jlc/pricing.json`](https://github.com/DingoOz/WholeBoard/blob/main/wholeboard/jlc/pricing.json) |
| Extra-cost features (small vias, vias in pads, bottom-side assembly) | From [`capabilities.json`](jlc-limits.md) and the board |
| Mouser and DigiKey | **Live** with API keys in *Settings → JLCPCB*; search links without them |
| OSH Park, Aisler | Worked out from their published prices ([Other fabs](jlc-limits.md#other-fabs)) |
| PCBWay | Promotional small-board price, marked as indicative; assembly is quoted by hand, so only a link |

JLCPCB does not publish a pricing formula, and its official API (api.jlcpcb.com) needs an approved developer
account. Update `pricing.json` when JLCPCB changes its prices, and confirm the final price on cart.jlcpcb.com with
the generated Gerber zip, BOM and CPL.

## Cheaper parts

Extended parts (a $3 feeder fee each) and parts that are short of stock are matched against drop-in
replacements: the same resistor, capacitor or LED value in the same package (with an equal or better voltage
rating and tolerance), or the same part number under a basic/preferred LCSC number. Tick the swaps to apply them.
The BOM and CPL are rewritten; the board does not change.

## Panels

For small boards, or when it is cheaper, the quote suggests a panel (columns × rows, V-cut or tab routing, 5 mm
rails) and compares the price. Choose a panel in the quote options to price it, and *Make the panel files* to draw
it with KiKit ([Make a panel](../how-to/panels.md)). Without panel files, JLCPCB builds it from the single-board
files when you pick *Panel by JLCPCB*.

## Parts JLCPCB cannot fit

Parts with no LCSC number, not found at JLCPCB, or short of stock are priced at Mouser and DigiKey by manufacturer
part number (`mpn` on the component), with stock and the best offer for the quantity. Keys:

- **Mouser**: a Search API key from mouser.com/api-hub.
- **DigiKey**: an app with the Product Information V4 API at developer.digikey.com (client ID and secret).

## Project report

A PDF of the whole board for a design review or your records, rebuilt after each quote and enclosure.
