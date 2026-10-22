"""Prices from Mouser and DigiKey for the parts JLCPCB cannot fit: no LCSC number, missing, or short of stock.

With API keys in Settings (Suppliers) WholeBoard asks Mouser's Search API and DigiKey's Product Information API
(v4) for stock and price breaks by manufacturer part number. Without keys it gives search links, so the parts can
still be found by hand. Keys: Mouser from mouser.com/api-hub (Search API), DigiKey from developer.digikey.com
(an app with the Product Information V4 API; client ID and secret).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

MOUSER = "https://api.mouser.com/api/v1/search/partnumber"
DIGIKEY_TOKEN = "https://api.digikey.com/v1/oauth2/token"
DIGIKEY_PRODUCT = "https://api.digikey.com/products/v4/search/{mpn}/productdetails"
_token: dict = {}


def _http(url: str, params: dict | None = None, body: dict | None = None, form: dict | None = None,
          headers: dict | None = None) -> tuple[int, dict]:
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data, h = None, {"Accept": "application/json", **(headers or {})}
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    elif form is not None:
        data, h["Content-Type"] = urllib.parse.urlencode(form).encode(), "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=h, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return 404, {}
        detail = e.read().decode(errors="ignore")[:200]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from None


def links(mpn: str) -> dict:
    q = urllib.parse.quote(mpn)
    return {"mouser": f"https://www.mouser.com/c/?q={q}", "digikey": f"https://www.digikey.com/en/products/result?keywords={q}"}


def _money(v) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    s = "".join(ch for ch in str(v or "") if ch.isdigit() or ch in ".,").replace(",", ".")
    if s.count(".") > 1:                       # 1.234,56 or 1,234.56
        head, _, tail = s.rpartition(".")
        s = head.replace(".", "") + "." + tail
    try:
        return float(s)
    except ValueError:
        return None


def _unit(breaks: list[tuple[int, float]], qty: int) -> float | None:
    price = None
    for q, p in sorted(breaks):
        if qty >= q or price is None:
            price = p
    return price


def mouser(mpn: str, key: str, qty: int) -> dict | None:
    _, data = _http(MOUSER, {"apiKey": key}, {"SearchByPartRequest": {"mouserPartNumber": mpn, "partSearchOptions": "Exact"}})
    errs = data.get("Errors") or []
    if errs:
        raise RuntimeError(errs[0].get("Message") or str(errs[0]))
    parts = (data.get("SearchResults") or {}).get("Parts") or []
    parts = [p for p in parts if str(p.get("ManufacturerPartNumber", "")).upper() == mpn.upper()] or parts
    if not parts:
        return None
    p = parts[0]
    breaks = [(int(b.get("Quantity") or 1), _money(b.get("Price"))) for b in p.get("PriceBreaks") or []]
    breaks = [(q, v) for q, v in breaks if v is not None]
    stock = p.get("AvailabilityInStock")
    try:
        stock = int(stock) if stock is not None else int(str(p.get("Availability") or "0").split()[0].replace(",", ""))
    except ValueError:
        stock = 0
    unit = _unit(breaks, qty)
    return {"supplier": "Mouser", "sku": p.get("MouserPartNumber"), "mpn": p.get("ManufacturerPartNumber"),
            "maker": p.get("Manufacturer"), "stock": stock, "unit": unit,
            "total": round(unit * qty, 2) if unit is not None else None,
            "currency": (p.get("PriceBreaks") or [{}])[0].get("Currency", "USD"), "url": p.get("ProductDetailUrl")}


def _digikey_token(cid: str, secret: str) -> str:
    t = _token.get(cid)
    if t and t[1] > time.time() + 30:
        return t[0]
    _, d = _http(DIGIKEY_TOKEN, form={"client_id": cid, "client_secret": secret, "grant_type": "client_credentials"})
    _token[cid] = (d["access_token"], time.time() + float(d.get("expires_in") or 600))
    return d["access_token"]


def digikey(mpn: str, cid: str, secret: str, qty: int, currency: str = "USD") -> dict | None:
    tok = _digikey_token(cid, secret)
    status, data = _http(DIGIKEY_PRODUCT.format(mpn=urllib.parse.quote(mpn, safe="")),
                         headers={"Authorization": f"Bearer {tok}", "X-DIGIKEY-Client-Id": cid,
                                  "X-DIGIKEY-Locale-Currency": currency, "X-DIGIKEY-Locale-Site": "US"})
    if status == 404:
        return None
    p = data.get("Product") or {}
    best = None
    for v in p.get("ProductVariations") or []:
        breaks = [(int(b.get("BreakQuantity") or 1), _money(b.get("UnitPrice"))) for b in v.get("StandardPricing") or []]
        breaks = [(q, x) for q, x in breaks if x is not None]
        unit = _unit(breaks, qty)
        if unit is not None and (best is None or unit < best[0]) and qty >= int(v.get("MinimumOrderQuantity") or 1):
            best = (unit, v.get("DigiKeyProductNumber"))
    if best is None and p.get("UnitPrice") is not None:
        best = (_money(p["UnitPrice"]), None)
    unit = best[0] if best else None
    return {"supplier": "DigiKey", "sku": best[1] if best else None, "mpn": p.get("ManufacturerProductNumber") or mpn,
            "maker": (p.get("Manufacturer") or {}).get("Name"), "stock": int(p.get("QuantityAvailable") or 0),
            "unit": unit, "total": round(unit * qty, 2) if unit is not None else None, "currency": currency,
            "url": p.get("ProductUrl")}


def wanted(spec: dict, quote: dict) -> list[dict]:
    """The parts to price elsewhere: no LCSC number, not found at JLCPCB, or short of stock."""
    comps = {c["ref"]: c for c in spec.get("components") or []}
    n = int(((quote.get("assembly") or {}).get("boards")) or quote.get("quantity") or 1)
    out = []
    for line in (quote.get("components") or {}).get("lines") or []:
        if line.get("status") == "ok":
            continue
        mpn = line.get("mpn") or next((comps[r].get("mpn") for r in line["refs"] if comps.get(r, {}).get("mpn")), None)
        out.append({"refs": line["refs"], "value": line.get("value"), "mpn": mpn, "why": line.get("status"),
                    "qty": int(line.get("per_board") or len(line["refs"])) * n})
    return out


def prices(spec: dict, quote: dict, cfg: dict, log=lambda *_: None) -> dict:
    """Mouser and DigiKey offers for each wanted part, cheapest first."""
    s = cfg.get("suppliers") or {}
    mkey, cid, secret = s.get("mouser_api_key"), s.get("digikey_client_id"), s.get("digikey_client_secret")
    rows = wanted(spec, quote)
    if not rows:
        return {"parts": [], "keys": bool(mkey or (cid and secret))}

    def one(row):
        row = {**row, "offers": [], "errors": []}
        if not row["mpn"]:
            row["errors"].append("no manufacturer part number: add \"mpn\" to the part")
            return row
        row["links"] = links(row["mpn"])
        for name, fn in (("Mouser", (lambda: mouser(row["mpn"], mkey, row["qty"])) if mkey else None),
                         ("DigiKey", (lambda: digikey(row["mpn"], cid, secret, row["qty"])) if cid and secret else None)):
            if not fn:
                continue
            try:
                o = fn()
                if o:
                    row["offers"].append(o)
            except Exception as e:  # noqa: BLE001
                row["errors"].append(f"{name}: {e}")
        row["offers"].sort(key=lambda o: (o["stock"] < row["qty"], o["unit"] if o["unit"] is not None else 1e9))
        return row
    with ThreadPoolExecutor(max_workers=4) as ex:
        out = list(ex.map(one, rows))
    for r in out:
        for e in r["errors"]:
            log(f"{', '.join(r['refs'])}: {e}")
    best = [r["offers"][0]["total"] for r in out if r["offers"] and r["offers"][0]["total"] is not None]
    return {"parts": out, "keys": bool(mkey or (cid and secret)), "total": round(sum(best), 2) if best else None}
