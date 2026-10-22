# Order the board

1. **Check stock.** Press *Check stock* in the quote: it asks JLCPCB again for every part. Parts that are short,
   low, discontinued or not found are flagged with drop-in replacements.
2. **Apply cheaper parts** if you want them: tick the swaps in the quote. The BOM and CPL are rewritten; the board
   does not change.
3. **Look at the assembly preview.** The CPL is corrected for JLCPCB's part orientations, but check the preview
   before ordering.
4. **Upload to JLCPCB.** On <https://cart.jlcpcb.com>, upload the Gerber zip from the revision's `fab/` folder,
   then the BOM and CPL for assembly. Choose impedance control if the quote reminds you to.
5. **Confirm the price.** WholeBoard's board, assembly and shipping prices are estimates (see
   [The quote](../reference/quote.md)); JLCPCB's cart is the real one.

For parts JLCPCB cannot fit, the quote prices them at Mouser and DigiKey (add API keys in *Settings → JLCPCB*).
Fit them by hand, or send them to JLCPCB as consigned parts.

If you fit parts yourself, open the **assembly page** in the outputs: a self-contained HTML page with the board
from both sides and the BOM grouped by who fits each part (JLCPCB, by hand, not fitted). Click a line to highlight
its parts, and tick them off as you go.

When the boards arrive, follow the **bring-up guide** in the outputs (and in `exports/`): what to inspect, which
nets to check for shorts, a current-limited first power-up with the current to expect, and the rail voltages at
their test pads. `"board": {"test_points": true}` adds a test pad to every rail.
