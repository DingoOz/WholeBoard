# 6. Board prices from editable tables, not scraping

**Status:** accepted, October 2026

## Context

JLCPCB publishes part prices through its parts library, but not a formula for boards, assembly or shipping, and
its official API needs an approved developer account. Scraping the quote page would break whenever the page
changed and is against the spirit of their terms. Other fabs are similar: OSH Park and Aisler publish formulas,
PCBWay quotes per order.

## Decision

- Part prices and stock are always live.
- Board, assembly and shipping prices come from `wholeboard/jlc/pricing.json`, calibrated against JLCPCB's
  public price pages, with a `last_checked` date and sources. The quote calls them estimates.
- Other fabs are calculated only where a formula is published (OSH Park, Aisler). PCBWay gets an indicative price
  and a link, not an invented number.
- Mouser and DigiKey use their official APIs with the user's own keys, and fall back to search links.

## Consequences

- Quotes are close but not exact; the user confirms the price in JLCPCB's cart.
- Someone has to update the tables when prices change. [Standards and sources](../standards.md) lists each one
  with its date.
