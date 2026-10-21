"""A small S-expression reader and writer for KiCad files.

Atoms are kept as `Sym` (bare words) or `str` (quoted strings) so a file can be
written back with the same quoting. Numbers stay as `Sym` text so they round-trip
exactly; use `num()` to read one.
"""

from __future__ import annotations

import re
from typing import Iterator, Union


class Sym(str):
    """A bare (unquoted) atom such as a keyword or a number."""

    __slots__ = ()


Node = Union[list, str, Sym]

_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)
_ESC = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


def _unescape(s: str) -> str:
    if "\\" not in s:
        return s
    return re.sub(r"\\(.)", lambda m: _ESC.get(m.group(1), "\\" + m.group(1)), s)


def parse(text: str) -> list:
    """Parse the first S-expression in `text`."""
    stack: list[list] = []
    root = None
    pos = 0
    n = len(text)
    while pos < n:
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip() == "":
                break
            raise ValueError(f"bad S-expression near offset {pos}: {text[pos:pos + 40]!r}")
        pos = m.end()
        if m.group(1):
            new: list = []
            if stack:
                stack[-1].append(new)
            stack.append(new)
        elif m.group(2):
            if not stack:
                raise ValueError("unbalanced ')'")
            done = stack.pop()
            if not stack:
                root = done
                break
        elif m.group(3) is not None:
            stack[-1].append(_unescape(m.group(3)))
        else:
            stack[-1].append(Sym(m.group(4)))
    if root is None:
        raise ValueError("no complete S-expression found")
    return root


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def dumps(node: Node, indent: int = 0) -> str:
    """Write a node in KiCad's style: one list per line when it holds lists."""
    if isinstance(node, Sym):
        return str(node)
    if isinstance(node, str):
        return _quote(node)
    if not any(isinstance(c, list) for c in node):
        return "(" + " ".join(dumps(c) for c in node) + ")"
    pad = "\t" * (indent + 1)
    head = []
    rest = []
    for c in node:
        (rest if (rest or isinstance(c, list)) else head).append(c)
    out = "(" + " ".join(dumps(c) for c in head)
    for c in rest:
        out += "\n" + pad + dumps(c, indent + 1)
    return out + "\n" + "\t" * indent + ")"


def tag(node: Node) -> str | None:
    if isinstance(node, list) and node and isinstance(node[0], Sym):
        return str(node[0])
    return None


def find(node: list, name: str) -> list | None:
    for c in node:
        if tag(c) == name:
            return c
    return None


def find_all(node: list, name: str) -> Iterator[list]:
    for c in node:
        if tag(c) == name:
            yield c


def walk(node: Node) -> Iterator[list]:
    if isinstance(node, list):
        yield node
        for c in node:
            yield from walk(c)


def num(x: Node, default: float = 0.0) -> float:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def fmt(x: float) -> Sym:
    """Format a number the way KiCad does: no trailing zeros, at most 6 decimals."""
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    if s in ("-0", ""):
        s = "0"
    return Sym(s)


def S(*items) -> list:
    """Build a list node, turning bare Python numbers into `Sym` atoms."""
    out = []
    for it in items:
        if isinstance(it, bool):
            out.append(Sym("yes" if it else "no"))
        elif isinstance(it, (int, float)):
            out.append(fmt(float(it)))
        else:
            out.append(it)
    return out
