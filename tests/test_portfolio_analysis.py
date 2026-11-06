"""Portfolio analysis: your holdings by asset class, market cap and sector (display only)."""

from __future__ import annotations

import pytest

from trading_agent import portfolio_analysis as pa

CAPS = pa.build_caps({"large": ["AAA", "BBB", "dup"], "mid": ["CCC", "DUP"], "small": ["EEE"]})
SECTORS = {"AAA": "Information Technology", "BBB": "Information Technology", "CCC": "Metals & Mining",
           "EEE": "FMCG", "FFF": "Financial Services"}


def h(sym, value, invested, kind="equity"):
    return {"symbol": sym, "kind": kind, "value": value, "invested": invested}


HOLDINGS = [h("AAA", 600.0, 500.0), h("BBB", 300.0, 400.0), h("CCC", 150.0, 100.0), h("EEE", 50.0, 60.0),
            h("ZZZ", 100.0, 90.0), h("BOND", 1001.0, 1000.0, kind="bond")]


def test_caps_come_from_three_lists_and_the_larger_list_wins():
    assert CAPS["DUP"] == "large" and CAPS["CCC"] == "mid" and CAPS["EEE"] == "small"      # symbols are upper-cased
    assert pa.cap_of("aaa", CAPS) == "Large cap" and pa.cap_of("CCC", CAPS) == "Mid cap"
    assert pa.cap_of("EEE", CAPS) == "Small cap" and pa.cap_of("NOPE", CAPS) == "Other"
    assert pa.sector_of("aaa", SECTORS) == "Information Technology" and pa.sector_of("NOPE", SECTORS) == "Others"


def test_asset_allocation_splits_stocks_from_debt_and_shows_both_amounts():
    r = pa.analyse(HOLDINGS, caps=CAPS, sectors=SECTORS)
    assets = {x["name"]: x for x in r["asset"]}
    assert list(assets) == ["Stocks", "Debt"]
    assert assets["Stocks"]["count"] == 5 and assets["Stocks"]["current"] == 1200.0 and assets["Stocks"]["invested"] == 1150.0
    assert assets["Debt"]["count"] == 1 and assets["Debt"]["current"] == 1001.0 and assets["Debt"]["pl"] == 1.0
    assert assets["Stocks"]["share_current"] + assets["Debt"]["share_current"] == pytest.approx(1.0)
    assert assets["Debt"]["share_invested"] == pytest.approx(1000.0 / 2150.0)             # the other basis is its own share
    assert r["totals"] == {"current": 2201.0, "invested": 2150.0} and r["holdings"] == 6 and r["stocks"] == 5


def test_a_portfolio_with_no_debt_has_no_debt_row():
    r = pa.analyse([h("AAA", 10.0, 10.0)], caps=CAPS, sectors=SECTORS)
    assert [x["name"] for x in r["asset"]] == ["Stocks"]


def test_market_cap_covers_stocks_only_keeps_all_three_classes_and_adds_other_only_when_used():
    r = pa.analyse(HOLDINGS, caps=CAPS, sectors=SECTORS)
    cap = {x["name"]: x for x in r["cap"]}
    assert list(cap) == ["Large cap", "Mid cap", "Small cap", "Other"]
    assert cap["Large cap"]["count"] == 2 and cap["Large cap"]["current"] == 900.0
    assert cap["Mid cap"]["count"] == 1 and cap["Small cap"]["current"] == 50.0 and cap["Other"]["current"] == 100.0
    assert sum(x["share_current"] for x in r["cap"]) == pytest.approx(1.0)                  # the bond is not in it
    only = pa.analyse([h("AAA", 10.0, 10.0)], caps=CAPS, sectors=SECTORS)
    assert [x["name"] for x in only["cap"]] == ["Large cap", "Mid cap", "Small cap"]
    zero = {x["name"]: x for x in only["cap"]}["Mid cap"]
    assert zero["count"] == 0 and zero["current"] == 0.0 and zero["share_current"] == 0.0   # shown, like Groww does


def test_sectors_are_sorted_by_current_value_with_others_last():
    r = pa.analyse(HOLDINGS, caps=CAPS, sectors=SECTORS)
    assert [x["name"] for x in r["sector"]] == ["Information Technology", "Metals & Mining", "FMCG", "Others"]
    it = r["sector"][0]
    assert it["count"] == 2 and it["current"] == 900.0 and it["invested"] == 900.0 and it["pl"] == 0.0
    assert r["sector"][-1]["current"] == 100.0                                              # ZZZ is not in the NIFTY 500 list


def test_an_unpriced_holding_counts_at_what_was_paid_and_is_listed():
    r = pa.analyse([h("AAA", None, 500.0), h("BBB", 300.0, 400.0)], caps=CAPS, sectors=SECTORS)
    assert r["unpriced"] == ["AAA"] and r["totals"]["current"] == 800.0
    assert r["sector"][0]["current"] == 800.0 and r["sector"][0]["pl"] == -100.0


def test_missing_lists_are_flagged_and_everything_falls_into_other():
    r = pa.analyse(HOLDINGS, caps={}, sectors={})
    assert r["caps_known"] is False and r["sectors_known"] is False
    assert [x["name"] for x in r["cap"] if x["count"]] == ["Other"] and [x["name"] for x in r["sector"]] == ["Others"]


def test_an_empty_portfolio_does_not_divide_by_zero():
    r = pa.analyse([], caps=CAPS, sectors=SECTORS)
    assert r["totals"] == {"current": 0.0, "invested": 0.0} and r["asset"][0]["share_current"] == 0.0
    assert r["sector"] == []
