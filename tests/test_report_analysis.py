"""Dividends by quarter, realised profit by holding period and XIRR against index funds, from the imported reports."""

from __future__ import annotations

from datetime import date

import pytest

from trading_agent import report_analysis as ra
from tests.test_groww_reports import DIV_TEXT, PNL_ROWS, make_xlsx
from trading_agent import groww_reports as gr

DIV = gr.parse_dividend_text(DIV_TEXT)
PNL = gr.parse_pnl_xlsx(make_xlsx({"Trade Level": PNL_ROWS}))


def test_dividends_are_summed_by_the_calendar_quarter_of_the_ex_date_with_empty_quarters_filled_in():
    s = ra.dividends_summary(DIV)
    assert s["total"] == 12985.5 and s["count"] == 3 and s["stocks"] == 3 and (s["from"], s["to"]) == ("2025-04-01", "2026-11-05")
    assert [(q["year"], q["label"], q["amount"]) for q in s["quarters"]] == [
        (2026, "Jan – Mar", 409.5), (2026, "Apr – Jun", 76.0), (2026, "Jul – Sep", 0.0), (2026, "Oct – Dec", 12500.0)]
    assert [x["company"] for x in s["by_stock"]] == ["GAMMA SERV LT", "ALPHA INDUSTRIES LTD.", "BETA & SONS LIMITED"]
    over = ra.dividends_summary({"rows": [{"company": "A", "isin": "I", "ex_date": "2025-11-01", "amount": 1.0},
                                          {"company": "A", "isin": "I", "ex_date": "2026-02-01", "amount": 2.0}]})
    assert [(q["year"], q["quarter"]) for q in over["quarters"]] == [(2025, 4), (2026, 1)]      # across a year end
    assert ra.dividends_summary({"rows": []})["quarters"] == [] and ra.dividends_summary({})["total"] == 0.0


def test_holding_buckets_same_day_over_a_year_and_the_leap_day():
    lot = lambda b, s, intraday=False: {"buy_date": b, "end_date": s, "intraday": intraday}  # noqa: E731
    assert ra.holding_bucket(lot("2025-03-10", "2025-03-10")) == "Intraday"
    assert ra.holding_bucket(lot("2025-03-10", "2025-03-11", intraday=True)) == "Intraday"      # Groww's own remark wins
    assert ra.holding_bucket(lot("2025-01-01", "2026-01-01")) == "Under a year"                  # exactly 12 months is not more
    assert ra.holding_bucket(lot("2025-01-01", "2026-01-02")) == "Over a year"
    assert ra.holding_bucket(lot("2024-02-29", "2025-02-28")) == "Under a year"                  # the anniversary of 29 Feb
    assert ra.holding_bucket(lot("2024-02-29", "2025-03-01")) == "Over a year"


def test_realised_profit_is_split_and_the_charges_are_listed_largest_first():
    s = ra.pnl_summary(PNL)
    assert s["realised"] == 1210.0 and s["unrealised"] == 200.0 and s["charges_total"] == 39.1
    assert {x["name"]: (x["pnl"], x["lots"]) for x in s["split"]} == {"Over a year": (100.0, 1), "Under a year": (105.0, 1), "Intraday": (5.0, 1)}
    assert [c["name"] for c in s["charges"]] == ["Brokerage", "STT", "Total GST"]                # the zero charge is left out
    assert (s["held_lots"], s["held_cost"], s["held_value"]) == (1, 2000.0, 2200.0)


def test_xirr_known_answers():
    d0 = date(2025, 1, 1)
    assert ra.xirr([(d0, -1000.0), (date(2026, 1, 1), 1100.0)]) == pytest.approx(0.10, abs=1e-6)     # 365 days
    assert ra.xirr([(d0, -1000.0), (date(2026, 1, 1), 900.0)]) == pytest.approx(-0.10, abs=1e-6)
    r = ra.xirr([(d0, -1000.0), (date(2025, 7, 1), -1000.0), (date(2026, 1, 1), 2300.0)])
    assert r is not None and sum(a / (1 + r) ** ((d - d0).days / 365) for d, a in
                                 [(d0, -1000.0), (date(2025, 7, 1), -1000.0), (date(2026, 1, 1), 2300.0)]) == pytest.approx(0.0, abs=1e-6)
    assert ra.xirr([(d0, -100.0), (date(2025, 1, 31), 200.0)]) > 1000                                # doubling in a month is a huge annual rate
    assert ra.xirr([(d0, -100.0), (date(2025, 1, 2), 100000.0)]) is None                             # a 1000x day has no rate worth stating
    assert ra.xirr([]) is None and ra.xirr([(d0, -1.0)]) is None
    assert ra.xirr([(d0, -1.0), (date(2026, 1, 1), -1.0)]) is None                                    # money only out
    assert ra.xirr([(d0, -1.0), (d0, 2.0)]) is None                                                   # one day


FLAT = {"2024-12-01": 100.0, "2026-12-01": 100.0}


def series(start_price, end_price, first="2024-12-01", end="2026-11-05"):
    return {first: start_price, end: end_price}


def test_the_lot_flows_are_buys_out_sales_in_and_the_held_shares_at_their_closing_value():
    done, held, end = ra.lot_flows(PNL)
    assert end == date(2026, 11, 5) and held == [(date(2026, 11, 5), 2200.0)]
    assert sorted(done)[0] == (date(2025, 1, 1), -1000.0) and (date(2026, 1, 2), 1100.0) in done
    assert sum(a for _, a in done) == pytest.approx(-1000 + 1100 - 500 + 505 - 1000 + 1105 - 2000)


def test_the_benchmark_gets_the_same_money_on_the_same_days():
    done = [(date(2025, 1, 1), -1000.0), (date(2026, 1, 1), 1100.0)]
    # an index that is flat: you end up where you put the money in, so it returns 10% only if the sale is 10% up (it is not)
    assert ra.benchmark_xirr(done, series(100.0, 100.0), date(2026, 11, 5)) == pytest.approx(
        ra.xirr([(date(2025, 1, 1), -1000.0), (date(2026, 1, 1), 1100.0), (date(2026, 11, 5), -100.0)]), abs=1e-6)
    assert ra.benchmark_xirr(done, {"2026-02-01": 100.0}, date(2026, 11, 5)) is None             # no price at the first buy
    assert ra.benchmark_xirr(done, {}, date(2026, 11, 5)) is None


def test_returns_summary_compares_with_each_fund_that_has_prices_and_names_the_rest():
    prices = {"NIFTYBEES": {"2024-12-01": 100.0, "2025-01-01": 100.0, "2025-03-10": 100.0, "2025-06-01": 100.0, "2025-07-01": 100.0,
                            "2025-10-01": 100.0, "2026-01-02": 100.0, "2026-11-05": 100.0},
              "MID150BEES": {"2026-09-22": 100.0}}                                               # too short: starts after the first buy
    r = ra.returns_summary(PNL, DIV, prices)
    assert r["available"] and r["from"] == "2025-01-01" and r["to"] == "2026-11-05" and r["with_dividends"]
    assert r["bought"] == 4500.0 and r["dividends"] == 12985.5
    early = {"rows": DIV["rows"] + [{"company": "Z", "isin": "I", "ex_date": "2024-06-01", "amount": 999.0}]}
    assert ra.returns_summary(PNL, early, prices)["dividends"] == 12985.5                            # one before the first buy is not counted
    assert [b["symbol"] for b in r["benchmarks"]] == ["NIFTYBEES"]
    assert r["benchmarks"][0]["gap"] == pytest.approx(r["xirr"] - r["benchmarks"][0]["xirr"])
    without = ra.returns_summary(PNL, None, prices)
    assert without["with_dividends"] is False and without["xirr"] < r["xirr"]
    assert ra.returns_summary({"realised": [], "unrealised": []}, None, {}) == {"available": False}
