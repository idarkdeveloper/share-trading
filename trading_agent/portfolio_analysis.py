"""Portfolio analysis: your Groww holdings broken down by asset class, market cap and sector (display only).

Pure functions on the holdings rows the dashboard already holds (``symbol``, ``kind``, ``value``, ``invested``) plus two
public lookups: the NSE industry of each NIFTY 500 stock, and which of NSE's three market-cap lists a stock is in.

* **Asset allocation:** Stocks and Debt (an instrument the instrument list marks as a bond).
* **Market cap:** Large cap is the NIFTY 100, Mid cap the NIFTY Midcap 150, Small cap the NIFTY Smallcap 250 and anything
  else is "Other". That is close to AMFI's top 100 / 101-250 / 251+ split but not the same list.
* **Sector:** NSE's industry from the NIFTY 500 list; a stock outside it is "Others".

Market cap and sector cover stocks only (a bond has neither). A holding without a price counts at what you paid, and is
listed in ``unpriced``. Every row carries both the current value and the amount invested, and the share of each, so the
page can switch between them. Nothing here places an order or touches Groww.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

CAP_LISTS = (("large", "NIFTY100"), ("mid", "NIFTYMIDCAP150"), ("small", "NIFTYSMALLCAP250"))
CAP_NAMES = {"large": "Large cap", "mid": "Mid cap", "small": "Small cap"}
OTHER_CAP = "Other"
OTHER_SECTOR = "Others"


def build_caps(lists: Mapping[str, Iterable[str]]) -> dict[str, str]:
    """{SYMBOL: "large" | "mid" | "small"} from the three member lists, the larger list winning when a symbol is in two."""
    out: dict[str, str] = {}
    for key, _name in CAP_LISTS:
        for sym in lists.get(key) or ():
            out.setdefault(str(sym).strip().upper(), key)
    return out


def asset_of(h: dict[str, Any]) -> str:
    return "Debt" if h.get("kind") == "bond" else "Stocks"


def cap_of(symbol: str, caps: dict[str, str]) -> str:
    return CAP_NAMES.get(caps.get(str(symbol).upper(), ""), OTHER_CAP)


def sector_of(symbol: str, sectors: dict[str, str]) -> str:
    return sectors.get(str(symbol).upper()) or OTHER_SECTOR


def _current(h: dict[str, Any]) -> float:
    v = h.get("value")
    return float(v) if v is not None else float(h.get("invested") or 0.0)


def group(rows: list[dict[str, Any]], key: Any, *, order: tuple[str, ...] = (), last: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """[{name, count, current, invested, pl, share_current, share_invested}]. ``order`` names come first, in that order
    (even with nothing in them); the rest follow by current value, and the names in ``last`` come at the very end."""
    by: dict[str, dict[str, float]] = {n: {"count": 0, "current": 0.0, "invested": 0.0} for n in order}
    for h in rows:
        g = by.setdefault(key(h), {"count": 0, "current": 0.0, "invested": 0.0})
        g["count"] += 1
        g["current"] += _current(h)
        g["invested"] += float(h.get("invested") or 0.0)
    tot_c = sum(g["current"] for g in by.values())
    tot_i = sum(g["invested"] for g in by.values())
    names = list(order) + sorted((n for n in by if n not in order and n not in last), key=lambda n: -by[n]["current"]) \
        + [n for n in last if n in by and n not in order]
    return [{"name": n, "count": int(by[n]["count"]), "current": round(by[n]["current"], 2),
             "invested": round(by[n]["invested"], 2), "pl": round(by[n]["current"] - by[n]["invested"], 2),
             "share_current": by[n]["current"] / tot_c if tot_c else 0.0,
             "share_invested": by[n]["invested"] / tot_i if tot_i else 0.0} for n in names]


def analyse(holdings: list[dict[str, Any]], *, caps: dict[str, str], sectors: dict[str, str]) -> dict[str, Any]:
    """The three breakdowns of ``holdings`` (see the module doc)."""
    stocks = [h for h in holdings if asset_of(h) == "Stocks"]
    asset = group(holdings, asset_of, order=("Stocks",))
    asset = [r for r in asset if r["name"] == "Stocks" or r["count"]]            # Debt only when there is some
    cap = group(stocks, lambda h: cap_of(h["symbol"], caps), order=tuple(CAP_NAMES.values()))
    cap = [r for r in cap if r["name"] != OTHER_CAP or r["count"]]
    sector = group(stocks, lambda h: sector_of(h["symbol"], sectors), last=(OTHER_SECTOR,))
    return {"asset": asset, "cap": cap, "sector": sector,
            "totals": {"current": round(sum(_current(h) for h in holdings), 2),
                       "invested": round(sum(float(h.get("invested") or 0.0) for h in holdings), 2)},
            "holdings": len(holdings), "stocks": len(stocks),
            "unpriced": [str(h["symbol"]) for h in holdings if h.get("value") is None],
            "caps_known": bool(caps), "sectors_known": bool(sectors)}
