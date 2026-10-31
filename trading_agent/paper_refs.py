"""Two reference lines beside every paper account: the Nifty 50 fund (NIFTYBEES) and cash.

Each account is judged against its own universe's index fund (MID150BEES for the Midcap 150): did the picks beat owning
that market? These lines answer two other questions, shown on the Paper accounts tab only:

* did the account beat the large-cap market (NIFTYBEES, dividends reinvested, price and charges of the fund itself)?
* did it beat holding cash? There is no market price for cash on Yahoo (Liquid BeES pays its return as extra units and its
  price stays at 1,000), so cash accrues at ``CASH_RATE_PCT`` a year (default 6.0), counted in calendar days.

Display only: no pass or fail rule, no order and no account's rules read them. The tab reads stored files and makes no
network call, so the server's after-close run stores the NIFTYBEES closes in ``state/paper_references.json`` (the last
year each time, so a new account is backfilled at once) and the page computes the lines from that file.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SYMBOL = "NIFTYBEES"
LABEL = "Nifty 50 (NIFTYBEES)"
CASH_LABEL = "Cash"
DEFAULT_CASH_RATE_PCT = 6.0
MAX_CASH_RATE_PCT = 25.0
KEEP_DAYS = 1500


def path_for(state_dir: Path) -> Path:
    return Path(state_dir) / "paper_references.json"


def parse_cash_rate_pct(value: object) -> float:
    """CASH_RATE_PCT as a number between 0 and 25; anything else is the default."""
    try:
        v = float(str(value).strip())
    except (TypeError, ValueError):
        return DEFAULT_CASH_RATE_PCT
    return v if 0.0 <= v <= MAX_CASH_RATE_PCT else DEFAULT_CASH_RATE_PCT


def load(state_dir: Path) -> dict[str, float]:
    """{ISO date: dividend-adjusted NIFTYBEES close}; empty when nothing is stored or the file is unreadable."""
    try:
        data = json.loads(path_for(state_dir).read_text(encoding="utf-8"))
        series = data.get(SYMBOL) or {}
        return {str(d): float(v) for d, v in series.items() if v}
    except (OSError, ValueError, AttributeError):
        return {}


def update(state_dir: Path, prices: Any) -> str:
    """Store the last year of NIFTYBEES closes (merged into what is already kept). Never raises."""
    try:
        have = load(state_dir)
        bars = prices.history(SYMBOL, "1y")
        got = {str(b["date"]): float(b.get("adj_close") or b["close"]) for b in bars if b.get("close")}
        if not got:
            return f"Paper references: no {SYMBOL} prices from Yahoo; kept {len(have)} stored day(s)."
        have.update(got)
        keep = dict(sorted(have.items())[-KEEP_DAYS:])
        from .state import atomic_write
        atomic_write(path_for(state_dir), json.dumps({SYMBOL: keep}))
        return f"Paper references: {SYMBOL} stored for {len(keep)} day(s), last {max(keep)}."
    except Exception as e:  # noqa: BLE001 - a missing reference line never stops the paper accounts
        log.warning("paper references failed: %s", e)
        return f"Paper references failed: {type(e).__name__}: {e}"


def _at(series: dict[str, float], day: str) -> float | None:
    """The close on ``day``, or on the last day before it that has one."""
    dates = [d for d in series if d <= day]
    return series[max(dates)] if dates else None


def _worst_fall(series: dict[str, float], start: str, end: str) -> float | None:
    """The deepest fall from a running peak over ``start``..``end`` (negative), None with nothing to measure."""
    base = _at(series, start)
    if base is None:
        return None
    path = [base] + [series[d] for d in sorted(series) if start < d <= end]
    peak, worst = path[0], 0.0
    for v in path:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1.0)
    return worst


def lines_for(series: dict[str, float], cash_rate_pct: float, *, start: str, end: str,
              strategy_return: float | None) -> list[dict[str, Any]]:
    """The reference lines for one account over its own days: [{key, label, note, return, gap, max_drawdown}].
    ``gap`` is the account's return minus the line's, in the same way as the index-fund gap on the card. The Nifty 50
    line is left out when its prices do not cover the account's days."""
    out: list[dict[str, Any]] = []
    a, b = _at(series, start), _at(series, end)
    if a and b and start <= end:
        ret = b / a - 1.0
        out.append({"key": "nifty50", "label": LABEL, "note": "dividends reinvested, before charges", "return": ret,
                    "gap": None if strategy_return is None else strategy_return - ret,
                    "max_drawdown": _worst_fall(series, start, end)})
    try:
        days = max(0, (date.fromisoformat(end) - date.fromisoformat(start)).days)
    except ValueError:
        return out
    cash = (1.0 + cash_rate_pct / 100.0) ** (days / 365.0) - 1.0
    out.append({"key": "cash", "label": CASH_LABEL, "note": f"{cash_rate_pct:g}% a year assumed (CASH_RATE_PCT)",
                "return": cash, "gap": None if strategy_return is None else strategy_return - cash,
                "max_drawdown": 0.0})
    return out
