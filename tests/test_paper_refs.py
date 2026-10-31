"""The Nifty 50 and cash reference lines beside the paper accounts (display only)."""

from __future__ import annotations

import json

import pytest

from trading_agent import paper_refs as pr


class Prices:
    def __init__(self, bars=None, boom=False):
        self.bars, self.boom, self.calls = bars or [], boom, []

    def history(self, symbol, range_="2y"):
        self.calls.append((symbol, range_))
        if self.boom:
            raise RuntimeError("yahoo down")
        return self.bars


def bar(day, close, adj=None):
    return {"date": day, "close": close, "adj_close": adj if adj is not None else close}


def test_cash_rate_setting_is_a_number_between_0_and_25_else_the_default():
    assert pr.parse_cash_rate_pct("5.5") == 5.5 and pr.parse_cash_rate_pct(" 7 ") == 7.0 and pr.parse_cash_rate_pct("0") == 0.0
    for bad in (None, "", "abc", "-1", "26", "nan"):
        assert pr.parse_cash_rate_pct(bad) == pr.DEFAULT_CASH_RATE_PCT


def test_update_stores_the_dividend_adjusted_closes_merges_and_never_raises(tmp_path):
    msg = pr.update(tmp_path, Prices([bar("2026-10-19", 261.2), bar("2026-10-20", 262.0, adj=262.5)]))
    assert "2 day(s)" in msg and "2026-10-20" in msg
    assert pr.load(tmp_path) == {"2026-10-19": 261.2, "2026-10-20": 262.5}          # adj_close, as for every return
    pr.update(tmp_path, Prices([bar("2026-10-20", 262.0, adj=263.0), bar("2026-10-21", 259.57)]))
    assert pr.load(tmp_path) == {"2026-10-19": 261.2, "2026-10-20": 263.0, "2026-10-21": 259.57}   # merged, newer wins
    down = pr.update(tmp_path, Prices(boom=True))
    assert down.startswith("Paper references failed: RuntimeError") and len(pr.load(tmp_path)) == 3   # kept what it had
    assert "no NIFTYBEES prices" in pr.update(tmp_path, Prices([]))


def test_load_survives_a_missing_or_broken_file(tmp_path):
    assert pr.load(tmp_path) == {}
    pr.path_for(tmp_path).write_text("{not json", encoding="utf-8")
    assert pr.load(tmp_path) == {}
    pr.path_for(tmp_path).write_text(json.dumps({"NIFTYBEES": {"2026-10-19": 100, "2026-10-20": 0}}), encoding="utf-8")
    assert pr.load(tmp_path) == {"2026-10-19": 100.0}


SERIES = {"2026-10-16": 100.0, "2026-10-19": 110.0, "2026-10-20": 99.0, "2026-10-21": 104.5, "2026-10-22": 121.0}


def test_the_nifty_line_is_the_return_and_worst_fall_over_the_accounts_own_days():
    lines = pr.lines_for(SERIES, 6.0, start="2026-10-19", end="2026-10-22", strategy_return=0.25)
    nifty = next(x for x in lines if x["key"] == "nifty50")
    assert nifty["label"] == "Nifty 50 (NIFTYBEES)" and nifty["return"] == pytest.approx(121.0 / 110.0 - 1)
    assert nifty["gap"] == pytest.approx(0.25 - (121.0 / 110.0 - 1))
    assert nifty["max_drawdown"] == pytest.approx(99.0 / 110.0 - 1)                   # peak 110, trough 99
    # a start on a day with no close (a weekend) uses the last close before it; a rising path has no fall
    sat = pr.lines_for(SERIES, 6.0, start="2026-10-17", end="2026-10-19", strategy_return=0.0)
    assert next(x for x in sat if x["key"] == "nifty50")["return"] == pytest.approx(0.10)
    assert next(x for x in sat if x["key"] == "nifty50")["max_drawdown"] == 0.0


def test_cash_accrues_at_the_assumed_rate_in_calendar_days_and_never_falls():
    lines = pr.lines_for(SERIES, 6.0, start="2026-10-19", end="2026-10-22", strategy_return=0.01)
    cash = next(x for x in lines if x["key"] == "cash")
    assert cash["return"] == pytest.approx(1.06 ** (3 / 365) - 1) and cash["max_drawdown"] == 0.0
    assert cash["gap"] == pytest.approx(0.01 - cash["return"]) and "6% a year assumed" in cash["note"]
    zero = pr.lines_for(SERIES, 0.0, start="2026-10-19", end="2026-10-22", strategy_return=None)
    assert next(x for x in zero if x["key"] == "cash")["return"] == 0.0 and zero[-1]["gap"] is None


def test_without_nifty_prices_only_the_cash_line_is_given():
    lines = pr.lines_for({}, 6.0, start="2026-10-19", end="2026-10-22", strategy_return=0.0)
    assert [x["key"] for x in lines] == ["cash"]
    early = pr.lines_for({"2026-10-30": 100.0}, 6.0, start="2026-10-19", end="2026-10-22", strategy_return=0.0)
    assert [x["key"] for x in early] == ["cash"]                                          # no close on or before the start
    assert pr.lines_for(SERIES, 6.0, start="2026-10-22", end="2026-10-19", strategy_return=0.0)[-1]["return"] == 0.0
    assert pr.lines_for(SERIES, 6.0, start="oops", end="2026-10-19", strategy_return=0.0) == []
