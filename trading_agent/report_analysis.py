"""What the imported Groww reports say (see groww_reports.py): dividends by quarter, realised profit by holding period, and
XIRR against index funds. Pure functions; the only outside input is a price series per benchmark, which the caller supplies.

* **Dividends:** summed by the calendar quarter of the ex-date, the way Groww's own screen groups them.
* **Realised profit:** split by how long each lot was held. Same-day lots are "Intraday"; a lot sold more than a year after it
  was bought is "Over a year"; the rest are "Under a year". The split is information, not tax advice: how Indian tax treats
  each part depends on things these reports do not say.
* **XIRR:** the annualised money-weighted return of every lot in the report (a buy is money out on its buy date; a sale is money
  in on its sell date; shares still held are counted at the report's closing price on its date; dividends are money in on their
  ex-date). It is before the charges the report lists. The benchmark line puts the *same* buys and sales into an index fund on
  the same days and holds what is left to the report date, so the two are directly comparable.
"""

from __future__ import annotations

from datetime import date
from typing import Any

QUARTERS = {1: "Jan – Mar", 2: "Apr – Jun", 3: "Jul – Sep", 4: "Oct – Dec"}
BENCHMARKS = (("NIFTYBEES", "Nifty 50"), ("MID150BEES", "Nifty Midcap 150"), ("HDFCSML250", "Nifty Smallcap 250"))
TOP_STOCKS = 8


# --------------------------------------------------------------------------- #
# dividends
# --------------------------------------------------------------------------- #
def dividends_summary(div: dict[str, Any]) -> dict[str, Any]:
    rows = div.get("rows") or []
    by_q: dict[tuple[int, int], float] = {}
    by_stock: dict[str, dict[str, Any]] = {}
    for r in rows:
        d = date.fromisoformat(r["ex_date"])
        key = (d.year, (d.month - 1) // 3 + 1)
        by_q[key] = by_q.get(key, 0.0) + float(r["amount"])
        s = by_stock.setdefault(r["isin"] or r["company"], {"company": r["company"], "isin": r["isin"], "amount": 0.0, "count": 0})
        s["amount"] += float(r["amount"])
        s["count"] += 1
    quarters: list[dict[str, Any]] = []
    if by_q:
        y, q = min(by_q)
        last = max(by_q)
        while (y, q) <= last:
            quarters.append({"year": y, "quarter": q, "label": QUARTERS[q], "amount": round(by_q.get((y, q), 0.0), 2)})
            y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    stocks = sorted(by_stock.values(), key=lambda s: -s["amount"])
    return {"total": round(sum(float(r["amount"]) for r in rows), 2), "count": len(rows), "from": div.get("from"),
            "to": div.get("to"), "quarters": quarters,
            "by_stock": [{**s, "amount": round(s["amount"], 2)} for s in stocks[:TOP_STOCKS]], "stocks": len(stocks),
            "imported_at": div.get("imported_at")}


# --------------------------------------------------------------------------- #
# realised profit and charges
# --------------------------------------------------------------------------- #
def _anniversary(d: date) -> date:
    try:
        return d.replace(year=d.year + 1)
    except ValueError:   # 29 Feb
        return date(d.year + 1, 2, 28)


def holding_bucket(lot: dict[str, Any]) -> str:
    buy, sell = date.fromisoformat(lot["buy_date"]), date.fromisoformat(lot["end_date"])
    if lot.get("intraday") or buy == sell:
        return "Intraday"
    return "Over a year" if sell > _anniversary(buy) else "Under a year"


def pnl_summary(pnl: dict[str, Any]) -> dict[str, Any]:
    split: dict[str, dict[str, float]] = {k: {"pnl": 0.0, "lots": 0} for k in ("Over a year", "Under a year", "Intraday")}
    for lot in pnl.get("realised") or []:
        b = split[holding_bucket(lot)]
        b["pnl"] += float(lot["pnl"])
        b["lots"] += 1
    charges = sorted(((k, v) for k, v in (pnl.get("charges") or {}).items() if v), key=lambda kv: -kv[1])
    unrealised = pnl.get("unrealised") or []
    return {"from": pnl.get("from"), "to": pnl.get("to"), "realised": pnl.get("realised_pnl"),
            "unrealised": pnl.get("unrealised_pnl"), "charges_total": pnl.get("charges_total"),
            "charges": [{"name": k, "amount": v} for k, v in charges],
            "split": [{"name": k, "pnl": round(v["pnl"], 2), "lots": int(v["lots"])} for k, v in split.items()],
            "held_lots": len(unrealised), "held_cost": round(sum(float(l["buy_value"]) for l in unrealised), 2),
            "held_value": round(sum(float(l["end_value"]) for l in unrealised), 2), "imported_at": pnl.get("imported_at")}


# --------------------------------------------------------------------------- #
# XIRR
# --------------------------------------------------------------------------- #
def xirr(flows: list[tuple[date, float]]) -> float | None:
    """The annual rate that makes the dated flows sum to zero (days / 365), or None when there is no such rate: it needs money
    out and money in, on more than one day. Found by bisection, so it cannot run away the way Newton's method can."""
    flows = sorted(flows)
    if len(flows) < 2 or flows[0][0] == flows[-1][0]:
        return None
    if not (any(a < 0 for _, a in flows) and any(a > 0 for _, a in flows)):
        return None
    t0 = flows[0][0]

    def f(r: float) -> float:
        return sum(a / (1.0 + r) ** ((d - t0).days / 365.0) for d, a in flows)
    lo, hi = -0.9999, 1.0
    while f(lo) * f(hi) > 0 and hi < 1e6:
        hi *= 4.0
    if f(lo) * f(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if f(lo) * f(mid) <= 0:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-10:
            break
    return (lo + hi) / 2.0


def lot_flows(pnl: dict[str, Any]) -> tuple[list[tuple[date, float]], list[tuple[date, float]], date | None]:
    """(flows that happened, shares still held as money in at the end, the report date). Buys are negative, sales positive."""
    done: list[tuple[date, float]] = []
    held: list[tuple[date, float]] = []
    end: date | None = date.fromisoformat(pnl["to"]) if pnl.get("to") else None
    for lot in pnl.get("realised") or []:
        done.append((date.fromisoformat(lot["buy_date"]), -float(lot["buy_value"])))
        done.append((date.fromisoformat(lot["end_date"]), float(lot["end_value"])))
    for lot in pnl.get("unrealised") or []:
        done.append((date.fromisoformat(lot["buy_date"]), -float(lot["buy_value"])))
        e = date.fromisoformat(lot["end_date"])
        held.append((e, float(lot["end_value"])))
        end = max(end, e) if end else e
    return done, held, end


def _price_at(series: dict[str, float], day: date) -> float | None:
    s = day.isoformat()
    dates = [d for d in series if d <= s]
    return series[max(dates)] if dates else None


def benchmark_xirr(done: list[tuple[date, float]], series: dict[str, float], end: date) -> float | None:
    """XIRR of putting the same buys and sales into an index fund (price series ``series``: date -> close) and holding the
    rest to ``end``. Dividends are not copied: the fund's own are in its dividend-adjusted price."""
    units = 0.0
    for d, a in sorted(done):
        px = _price_at(series, d)
        if not px:
            return None
        units += -a / px
    last = _price_at(series, end)
    if not last:
        return None
    return xirr([*done, (end, units * last)])


def returns_summary(pnl: dict[str, Any], div: dict[str, Any] | None, series: dict[str, dict[str, float]]) -> dict[str, Any]:
    """The portfolio's XIRR over the report's period and each benchmark's for the same money. ``series`` is
    {fund symbol: {date: close}}; a fund with no prices is left out."""
    done, held, end = lot_flows(pnl)
    if not done or end is None:
        return {"available": False}
    start = min(d for d, _ in done)
    divs = [(date.fromisoformat(r["ex_date"]), float(r["amount"])) for r in ((div or {}).get("rows") or [])
            if start <= date.fromisoformat(r["ex_date"]) <= end]
    mine = xirr(done + held + divs)
    out: list[dict[str, Any]] = []
    for sym, label in BENCHMARKS:
        b = benchmark_xirr(done, series.get(sym) or {}, end) if series.get(sym) else None
        if b is not None:
            out.append({"symbol": sym, "label": label, "xirr": b, "gap": None if mine is None else mine - b})
    bought = sum(-a for _, a in done if a < 0)
    return {"available": mine is not None, "xirr": mine, "from": start.isoformat(), "to": end.isoformat(),
            "days": (end - start).days, "bought": round(bought, 2), "dividends": round(sum(a for _, a in divs), 2),
            "with_dividends": bool(divs), "benchmarks": out}
