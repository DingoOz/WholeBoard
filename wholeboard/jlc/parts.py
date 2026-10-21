"""Look up and search JLCPCB/LCSC parts: stock, library type and price breaks.

Primary source: the parts search JLCPCB's own website uses (no login needed).
Fallback for single-part lookups: the community jlcsearch.tscircuit.com mirror.
Results are cached for a day under ~/.wholeboard.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request

from .. import config

JLC_SEARCH = "https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList"
JLCSEARCH = "https://jlcsearch.tscircuit.com"
CACHE_FILE = config.APP_DIR / "parts-cache.json"
CACHE_TTL = 24 * 3600
UA = {"User-Agent": "WholeBoard/0.1 (+KiCad workbench)", "Accept": "application/json"}

_lock = threading.Lock()
_cache: dict | None = None


def _load_cache() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CACHE_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            _cache = {}
    return _cache


def _cached(key: str):
    with _lock:
        hit = _load_cache().get(key)
        if hit and time.time() - hit["t"] < CACHE_TTL:
            return hit["v"]
    return None


def _store(key: str, value) -> None:
    with _lock:
        c = _load_cache()
        c[key] = {"t": time.time(), "v": value}
        try:
            config.APP_DIR.mkdir(parents=True, exist_ok=True)
            CACHE_FILE.write_text(json.dumps(c))
        except OSError:
            pass


def _post_json(url: str, body: dict, timeout: float = 20) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={**UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _get_json(url: str, timeout: float = 20) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _from_jlc(item: dict) -> dict:
    lib = item.get("componentLibraryType") or ""
    preferred = bool(item.get("preferredComponentFlag"))
    kind = "basic" if lib == "base" else ("preferred" if preferred else "extended")
    prices = sorted(({"min": p.get("startNumber", 1), "price": p.get("productPrice")}
                     for p in item.get("componentPrices") or [] if p.get("productPrice") is not None),
                    key=lambda p: p["min"])
    return {
        "lcsc": item.get("componentCode"),
        "mpn": item.get("componentModelEn"),
        "manufacturer": item.get("componentBrandEn"),
        "package": item.get("componentSpecificationEn"),
        "description": item.get("describe") or item.get("erpComponentName"),
        "category": item.get("componentTypeEn"),
        "stock": item.get("stockCount") or 0,
        "library_type": kind,
        "prices": prices,
        "min_order": max(int(item.get("leastPatchNumber") or 1), int(item.get("minPurchaseNum") or 1)),
        "datasheet": item.get("dataManualUrl"),
        "url": item.get("lcscGoodsUrl"),
        "source": "jlcpcb.com",
    }


def _from_jlcsearch(item: dict) -> dict:
    prices = []
    raw = item.get("price")
    if isinstance(raw, str):
        for part in raw.split(","):
            m = re.match(r"(\d+)-\d*:([\d.]+)", part.strip())
            if m:
                prices.append({"min": int(m.group(1)), "price": float(m.group(2))})
    elif isinstance(raw, (int, float)):
        prices.append({"min": 1, "price": float(raw)})
    kind = "basic" if item.get("is_basic") else ("preferred" if item.get("is_preferred") else "extended")
    return {
        "lcsc": f"C{item.get('lcsc')}", "mpn": item.get("mfr"), "manufacturer": None,
        "package": item.get("package"), "description": item.get("description"),
        "category": item.get("category"), "stock": item.get("stock") or 0,
        "library_type": kind, "prices": prices, "min_order": 1, "datasheet": None,
        "url": f"https://jlcpcb.com/partdetail/C{item.get('lcsc')}", "source": "jlcsearch",
    }


def _jlc_query(keyword: str, size: int = 50) -> list[dict]:
    data = _post_json(JLC_SEARCH, {"keyword": keyword, "currentPage": 1, "pageSize": size,
                                   "searchSource": "search"})
    return [_from_jlc(i) for i in (((data.get("data") or {}).get("componentPageInfo") or {})
                                   .get("list") or [])]


def search(keyword: str, limit: int = 10, in_stock: bool = True) -> list[dict]:
    """Search JLCPCB's parts library by MPN, LCSC number or keywords.

    JLCPCB's search matches part numbers best and gives up on long phrases, so when a
    query finds nothing in stock it is retried with fewer words."""
    keyword = keyword.strip()
    key = f"s2:{keyword.lower()}:{limit}:{in_stock}"
    hit = _cached(key)
    if hit is not None:
        return hit
    words = keyword.split()
    variants = [" ".join(words[:n]) for n in range(len(words), 0, -1)]
    if len(words) > 2:
        variants.insert(1, " ".join(words[-2:]))
    found: dict[str, dict] = {}
    for v in variants[:5]:
        for item in _jlc_query(v):
            if (item["stock"] > 0 or not in_stock) and item["lcsc"] not in found:
                found[item["lcsc"]] = item
        if len(found) >= limit:
            break
    items = list(found.values())
    rank = {"basic": 0, "preferred": 1, "extended": 2}
    items.sort(key=lambda i: (rank.get(i["library_type"], 3), -min(i["stock"], 100000)))
    items = items[:limit]
    _store(key, items)
    return items


def lookup(lcsc: str) -> dict | None:
    """Details for one LCSC part number such as 'C6186'."""
    code = lcsc.strip().upper()
    if not re.fullmatch(r"C\d+", code):
        return None
    hit = _cached(f"p:{code}")
    if hit is not None:
        return hit
    part = None
    try:
        data = _post_json(JLC_SEARCH, {"keyword": code, "currentPage": 1, "pageSize": 5})
        for i in ((data.get("data") or {}).get("componentPageInfo") or {}).get("list") or []:
            if i.get("componentCode") == code:
                part = _from_jlc(i)
                break
    except Exception:  # noqa: BLE001 - fall back to the mirror
        part = None
    if part is None:
        try:
            data = _get_json(f"{JLCSEARCH}/components/list.json?search={urllib.parse.quote(code)}")
            for i in data.get("components") or []:
                if f"C{i.get('lcsc')}" == code:
                    part = _from_jlcsearch(i)
                    break
        except Exception:  # noqa: BLE001
            part = None
    if part:
        _store(f"p:{code}", part)
    return part


def unit_price(part: dict, qty: int) -> float | None:
    best = None
    for p in part.get("prices") or []:
        if qty >= p["min"]:
            best = p["price"]
    if best is None and part.get("prices"):
        best = part["prices"][0]["price"]
    return best


_SI = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6, "R": 1.0}


def parse_value(text: str) -> float | None:
    """'10k' -> 10000, '4k7' -> 4700, '100nF' -> 1e-7, '0R' -> 0, '2.2uF' -> 2.2e-6."""
    s = text.strip().replace("Ω", "").replace("ohm", "").replace("F", "").replace(" ", "")
    m = re.fullmatch(r"(\d+)([pnuµmkKMR])(\d+)", s)
    if m:
        return float(f"{m.group(1)}.{m.group(3)}") * _SI[m.group(2)]
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([pnuµmkKMR]?)", s)
    if not m:
        return None
    return float(m.group(1)) * (_SI[m.group(2)] if m.group(2) else 1.0)


def find_passive(kind: str, value: str, package: str = "0603", limit: int = 5) -> list[dict]:
    """In-stock resistors, capacitors or LEDs by value (or LED colour) and package.

    Candidates come from the jlcsearch mirror's category filters; stock and price are then
    confirmed live at JLCPCB."""
    kind = kind.lower().rstrip("s")
    if kind == "led":
        url = f"{JLCSEARCH}/leds/list.json?package={urllib.parse.quote(package)}&color={urllib.parse.quote(value.lower())}"
        key = "leds"
    elif kind in ("resistor", "capacitor"):
        v = parse_value(value)
        if v is None:
            raise ValueError(f"cannot read the value {value!r}; write it like 10k, 4.7uF or 100nF")
        param = "resistance" if kind == "resistor" else "capacitance"
        url = f"{JLCSEARCH}/{kind}s/list.json?{param}={v:.12g}&package={urllib.parse.quote(package)}"
        key = f"{kind}s"
    else:
        raise ValueError("kind must be resistor, capacitor or led")
    cache_key = f"fp:{url}:{limit}"
    hit = _cached(cache_key)
    if hit is not None:
        return hit
    data = _get_json(url)
    cands = data.get(key) or []
    cands.sort(key=lambda i: (not i.get("is_basic"), not i.get("is_preferred")))
    codes = [f"C{i['lcsc']}" for i in cands[:14]]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=7) as ex:
        live = [p for p in ex.map(lambda c: _safe(lookup, c), codes) if p and p["stock"] > 0]
    rank = {"basic": 0, "preferred": 1, "extended": 2}
    live.sort(key=lambda i: (rank.get(i["library_type"], 3), -i["stock"]))
    live = live[:limit]
    _store(cache_key, live)
    return live


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:  # noqa: BLE001
        return None
