# 2. Functional mains spacing only between declared mains nets

**Status:** accepted, October 2026

## Context

There are two kinds of mains spacing: functional (between live and neutral, IPC-2221) and reinforced (from the
mains side to everything a person can touch, IEC 62368-1). The first version applied the functional spacing to
every net on the mains side, including low-energy nets such as the resistor feed to an optocoupler's LED.

On the first real board this boxed in the optocoupler: its pins are 2.54 mm apart, and with 2.5 mm required
between every pair of mains-side nets, TraceMaker could not get a track out of it.

## Decision

Functional spacing applies only between nets the design lists in `mains.nets`: the ones that carry the full mains
voltage (live, neutral, live after the fuse, a rectified bus). Pad-to-pad spacing within one part is the part's own
business and is not checked. Reinforced spacing still applies between the whole mains side and everything else.

## Consequences

- Designs must list every net at full mains voltage; the AI is told so.
- A mains-side net that is not listed is still kept 3 mm / 5 mm from the low-voltage side, which is the spacing that
  protects people.
- The KiCad rule is conditioned on both items being in the `Mains` net class and not both pads.
