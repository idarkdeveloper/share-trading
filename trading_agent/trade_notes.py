"""Notes on each trade in the order history: why it was taken, the worst dip while it was held, and how it ended.

Display only. Nothing here places an order, reads Groww or feeds a rule; the page shows the notes beside each trade in the
Trades view of Order history.

* **Why it was taken** comes from what the app recorded: the newest buy recommendation for the stock in the ``WHY_WINDOW_DAYS``
  days before the buy, else the order's own source (the agent, a limit order you set) or "your own order". A recommendation
  that came before a buy is a likely reason, not proof of one.
* **How it ended** comes from the sell order: a stop-loss (which kind, at what level), your limit order, the agent, or by hand.
  A real Groww sale made by a GTT stop is not marked as such in the order list, so it reads "reason not recorded".
* **The worst dip** is how far below your average buy price the stock traded while you held it, from each day's lowest price
  in the days between the buy and the sell, plus the two fill prices themselves. Moves inside the buy day and the sell day are
  not counted beyond those two prices (a daily bar does not say whether its low came before or after your fill), so a real dip
  can be a little deeper than shown, never shallower. History that does not reach back to the buy, or that a split or bonus
  has rescaled, gives no number rather than a wrong one.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .timezones import IST

WHY_WINDOW_DAYS = 10        # a recommendation this long before the buy (or less) is offered as the reason
HEADLINE_CHARS = 160
RATIONALE_CHARS = 240
MISMATCH = 0.20             # a fill price this far from that day's bar means the bars were rescaled (split or bonus)
STOP_NAMES = {"trailing": "Trailing stop-loss", "fixed": "Fixed stop-loss", "percent": "Stop-loss below the buy price"}


def bare(symbol: Any) -> str:
    """A symbol without the exchange suffix Yahoo adds, upper case."""
    s = str(symbol or "").strip().upper()
    return s[:-3] if s.endswith(".NS") else s


def _at(v: Any) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(v))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _day(v: Any) -> str | None:
    """The IST calendar day of a timestamp, as YYYY-MM-DD."""
    d = _at(v)
    return d.astimezone(IST).date().isoformat() if d else None


def _label(v: Any) -> str:
    d = _at(v)
    if d is None:
        return ""
    d = d.astimezone(IST)
    return f"{d.day} {d.strftime('%b')}"


def _clip(text: Any, n: int) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def index_recs(recs: list[dict[str, Any]] | None) -> dict[str, list[tuple[datetime, dict[str, Any]]]]:
    """The buy recommendations by stock, oldest first, so a trade finds its reason without reading every recommendation."""
    out: dict[str, list[tuple[datetime, dict[str, Any]]]] = {}
    for r in recs or []:
        if str(r.get("action") or "").lower() != "buy":
            continue
        at = _at(r.get("at"))
        if at is not None:
            out.setdefault(bare(r.get("ticker")), []).append((at, r))
    for rows in out.values():
        rows.sort(key=lambda x: x[0])
    return out


def why_taken(buy: dict[str, Any], by_ticker: dict[str, list[tuple[datetime, dict[str, Any]]]]) -> dict[str, Any]:
    """Why the buy happened, from a normalised fill (``trades.normalise``) and ``index_recs``: {kind, text, ...}."""
    when = buy.get("when")
    best: tuple[datetime, dict[str, Any]] | None = None
    if isinstance(when, datetime):
        for at, r in by_ticker.get(bare(buy.get("symbol")), []):
            if at <= when and when - at <= timedelta(days=WHY_WINDOW_DAYS):
                best = (at, r)   # oldest first, so the last one that fits is the newest before the buy
    if best is not None:
        at, r = best
        head = _clip(r.get("headline"), HEADLINE_CHARS)
        return {"kind": "recommendation", "text": f"Recommended {_label(at.isoformat())}" + (f": {head}" if head else ""),
                "at": at.isoformat(timespec="seconds"), "rationale": _clip(r.get("rationale"), RATIONALE_CHARS) or None,
                "confidence": r.get("confidence") or None, "investor": r.get("investor") or None}
    if buy.get("live") and buy.get("source") == "agent":
        return {"kind": "agent", "text": "Placed by the agent"}
    if buy.get("limit"):
        return {"kind": "limit", "text": f"Your limit order at {buy['limit']:,.2f}", "limit": buy["limit"]}
    if buy.get("live"):
        return {"kind": "unknown", "text": "Reason not recorded"}
    return {"kind": "own", "text": f"Your own order (no buy recommendation in the {WHY_WINDOW_DAYS} days before)"}


def exit_note(sell: dict[str, Any]) -> dict[str, Any]:
    """How the sale happened, from a normalised sell fill: {kind, text, ...}."""
    if sell.get("stop_hit"):
        kind, level = sell.get("stop_type"), sell.get("stop_level")
        name = STOP_NAMES.get(str(kind), "Stop-loss")
        return {"kind": "stop", "text": name + (f" at {level:,.2f}" if level else ""), "stop_type": kind, "level": level}
    if sell.get("limit"):
        return {"kind": "limit", "text": f"Your limit order at {sell['limit']:,.2f}", "limit": sell["limit"]}
    if sell.get("live"):
        if sell.get("source") == "agent":
            return {"kind": "agent", "text": "Sold by the agent"}
        return {"kind": "unknown", "text": "Reason not recorded"}
    return {"kind": "manual", "text": "Sold by hand"}


def worst_fall(buy_px: Any, buy_at: Any, bars: list[dict[str, Any]], *, end_at: Any = None, end_px: Any = None) -> dict[str, Any]:
    """How far below ``buy_px`` the stock traded while it was held. ``bars`` are daily OHLC bars (``date`` an IST day, ``low``,
    ``close``); ``end_at``/``end_px`` are the sale (a trade still open has neither and runs to the last bar). Returns
    {ok: True, px, pct, on, never} (``pct`` is 0 and ``never`` True when it did not trade below the buy price), or
    {ok: False, why} when no honest number can be given (see the module note)."""
    try:
        buy = float(buy_px)
    except (TypeError, ValueError):
        return {"ok": False, "why": "no buy price"}
    buy_day = _day(buy_at)
    if buy <= 0 or buy_day is None:
        return {"ok": False, "why": "bought before this history starts"}
    rows = sorted((b for b in bars or [] if b.get("date") and b.get("low") is not None and b.get("close")), key=lambda b: str(b["date"]))
    if not rows or str(rows[0]["date"]) > buy_day:
        return {"ok": False, "why": "the price history does not reach back to the buy"}
    ref = [b for b in rows if str(b["date"]) <= buy_day][-1]
    if abs(buy / float(ref["close"]) - 1.0) > MISMATCH:
        return {"ok": False, "why": "prices were rescaled by a split or bonus, so they no longer match the buy"}
    end_day = _day(end_at) if end_at else None
    cands: list[tuple[float, str]] = [(buy, buy_day)]
    try:
        if end_day and end_px and float(end_px) > 0:
            cands.append((float(end_px), end_day))
    except (TypeError, ValueError):
        pass
    for b in rows:
        day = str(b["date"])
        if day <= buy_day:
            continue
        if end_day and day >= end_day:
            break
        cands.append((float(b["low"]), day))
    px, on = min(cands, key=lambda c: c[0])   # a tie keeps the earliest, which is the buy itself
    never = px >= buy - 1e-9
    return {"ok": True, "px": round(px, 2), "pct": 0.0 if never else round((px / buy - 1.0) * 100.0, 2), "on": None if never else on, "never": never}
