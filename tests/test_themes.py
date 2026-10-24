"""Every colour theme keeps its text readable: WCAG AA contrast (4.5:1) for the text the interface draws."""
import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "wholeboard" / "web"

# foreground token, background token: each pair is text the interface draws on that background
PAIRS = [("text", "bg"), ("text", "panel"), ("text", "panel-2"),
         ("muted", "bg"), ("muted", "panel"), ("muted", "panel-2"),
         ("on-accent", "accent"), ("accent", "panel"),
         ("danger", "panel"), ("warn", "panel"), ("ok", "panel"), ("info", "panel")]


def _tokens(block: str) -> dict:
    found = re.findall(r"--([\w-]+):\s*#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b", block)
    return {k: "#" + (v if len(v) == 6 else "".join(c * 2 for c in v)) for k, v in found}


def themes() -> dict:
    css = (WEB / "style.css").read_text()
    root = _tokens(re.search(r":root \{(.*?)\}", css, re.S).group(1))
    dark = _tokens(re.search(r':root\[data-theme="auto"\] \{(.*?)\}', css, re.S).group(1))
    out = {"auto (light)": root, "auto (dark)": {**root, **dark}}
    for name, body in re.findall(r':root\[data-theme="([\w-]+)"\] \{(.*?)\}', (WEB / "themes.css").read_text(), re.S):
        out[name] = {**root, **_tokens(body)}
    return out


def contrast(a: str, b: str) -> float:
    def lum(h: str) -> float:
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("name", list(themes()))
def test_theme_text_is_readable(name):
    t = themes()[name]
    low = [f"--{a} on --{b}: {contrast(t[a], t[b]):.2f}" for a, b in PAIRS if contrast(t[a], t[b]) < 4.5]
    assert not low, f"{name} has text below 4.5:1 contrast: " + "; ".join(low)


def test_every_theme_in_the_menu_has_colours():
    menu = set(re.findall(r'<option value="([\w-]+)"', (WEB / "index.html").read_text().split('id="themeSelect"')[1].split("</select>")[0]))
    assert menu - {"auto"} == set(themes()) - {"auto (light)", "auto (dark)"}
