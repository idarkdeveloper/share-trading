"""Notes on each trade (trading_agent/trade_notes.py): why it was taken, how it ended, the worst dip while it was held. Fakes only."""
from datetime import datetime, timedelta, timezone

from trading_agent.trade_notes import exit_note, index_recs, why_taken, worst_fall

IST = timezone(timedelta(hours=5, minutes=30))
BUY_AT = datetime(2026, 11, 4, 11, 0, tzinfo=IST)


def rec(action, ticker, at, headline="Pelosi bought it", **kw):
    return {"action": action, "ticker": ticker, "at": at.astimezone(timezone.utc).isoformat(timespec="seconds"), "headline": headline, **kw}


def buy(**kw):
    return {"symbol": "ABC", "when": BUY_AT, "live": False, "limit": None, "source": None, **kw}


def test_the_newest_buy_recommendation_before_the_buy_is_the_reason():
    recs = index_recs([rec("buy", "ABC", BUY_AT - timedelta(days=5), "old one"), rec("buy", "ABC", BUY_AT - timedelta(days=2), "Promoter bought ABC",
                                                                                      rationale="  Two\nlines  ", confidence="high", investor="A B"),
                       rec("buy", "ABC", BUY_AT + timedelta(hours=1), "after the buy")])
    w = why_taken(buy(), recs)
    assert w["kind"] == "recommendation" and w["text"] == "Recommended 2 Nov: Promoter bought ABC"
    assert w["rationale"] == "Two lines" and w["confidence"] == "high" and w["investor"] == "A B"


def test_only_buy_recommendations_for_the_same_stock_inside_the_window_count():
    recs = index_recs([rec("sell", "ABC", BUY_AT - timedelta(days=1)), rec("watch", "ABC", BUY_AT - timedelta(days=1)),
                       rec("buy", "XYZ", BUY_AT - timedelta(days=1)), rec("buy", "ABC", BUY_AT - timedelta(days=11)),
                       {"action": "buy", "ticker": "ABC", "at": "not a time"}])
    w = why_taken(buy(), recs)
    assert w["kind"] == "own" and "10 days" in w["text"]
    # the day before the window ends still counts, and a Yahoo suffix or lower case does not matter
    near = index_recs([rec("BUY", "abc.ns", BUY_AT - timedelta(days=10))])
    assert why_taken(buy(), near)["kind"] == "recommendation"


def test_without_a_recommendation_the_order_itself_says_how_it_was_placed():
    none = index_recs([])
    assert why_taken(buy(limit=250.0), none) == {"kind": "limit", "text": "Your limit order at 250.00", "limit": 250.0}
    assert why_taken(buy(live=True, source="agent"), none) == {"kind": "agent", "text": "Placed by the agent"}
    assert why_taken(buy(live=True), none) == {"kind": "unknown", "text": "Reason not recorded"}
    assert why_taken(buy(when=None), none)["kind"] == "own"
    # a recommendation still wins over how the order was placed
    assert why_taken(buy(live=True, source="agent"), index_recs([rec("buy", "ABC", BUY_AT - timedelta(hours=3))]))["kind"] == "recommendation"


def test_a_long_headline_is_clipped():
    w = why_taken(buy(), index_recs([rec("buy", "ABC", BUY_AT - timedelta(hours=1), "x " * 200)]))
    assert len(w["text"]) < 190 and w["text"].endswith("…")


def test_how_a_sale_ended():
    assert exit_note({"stop_hit": True, "stop_type": "trailing", "stop_level": 95.1}) == {"kind": "stop", "text": "Trailing stop-loss at 95.10", "stop_type": "trailing", "level": 95.1}
    assert exit_note({"stop_hit": True, "stop_type": "fixed"})["text"] == "Fixed stop-loss"
    assert exit_note({"stop_hit": True, "stop_type": "percent", "stop_level": 90.0})["text"] == "Stop-loss below the buy price at 90.00"
    assert exit_note({"stop_hit": True, "stop_type": "odd"})["text"] == "Stop-loss"
    assert exit_note({"limit": 130.0})["text"] == "Your limit order at 130.00"
    assert exit_note({})["text"] == "Sold by hand"
    assert exit_note({"live": True, "source": "agent"})["text"] == "Sold by the agent"
    assert exit_note({"live": True})["text"] == "Reason not recorded"


def bar(day, low, close=None, high=None):
    return {"date": day, "low": low, "close": close if close is not None else low + 1.0, "high": high or low + 2.0}


BARS = [bar("2026-11-02", 98.0, 100.0), bar("2026-11-03", 97.0, 99.0), bar("2026-11-04", 96.0, 100.0), bar("2026-11-05", 94.0, 96.0),
        bar("2026-11-06", 90.0, 95.0), bar("2026-11-09", 93.0, 97.0), bar("2026-11-10", 99.0, 104.0)]


def test_worst_dip_is_the_lowest_daily_low_between_the_buy_and_the_sell():
    w = worst_fall(100.0, BUY_AT, BARS, end_at=datetime(2026, 11, 10, 14, 0, tzinfo=IST), end_px=102.0)
    assert w == {"ok": True, "px": 90.0, "pct": -10.0, "on": "2026-11-06", "never": False}


def test_the_buy_day_and_the_sell_day_lows_are_not_counted_only_the_two_fills():
    # the buy day's low (96) and the sell day's low (99, here lowered to 80) lie outside the days between, so only the fill prices count
    bars = [bar("2026-11-04", 50.0, 100.0), bar("2026-11-05", 70.0, 98.0), bar("2026-11-06", 60.0, 98.0)]
    w = worst_fall(100.0, BUY_AT, bars, end_at=datetime(2026, 11, 6, 15, 0, tzinfo=IST), end_px=97.0)
    assert w["px"] == 70.0 and w["on"] == "2026-11-05"
    same_day = worst_fall(100.0, BUY_AT, bars[:1], end_at=datetime(2026, 11, 4, 15, 0, tzinfo=IST), end_px=96.5)
    assert same_day == {"ok": True, "px": 96.5, "pct": -3.5, "on": "2026-11-04", "never": False}


def test_a_stock_that_never_traded_below_the_buy_price():
    bars = [bar("2026-11-04", 99.0, 100.0), bar("2026-11-05", 101.0, 103.0), bar("2026-11-06", 102.0, 105.0)]
    w = worst_fall(100.0, BUY_AT, bars, end_at=datetime(2026, 11, 6, 15, 0, tzinfo=IST), end_px=104.0)
    assert w == {"ok": True, "px": 100.0, "pct": 0.0, "on": None, "never": True}


def test_a_trade_still_open_runs_to_the_last_bar():
    w = worst_fall(100.0, BUY_AT, BARS)
    assert w["px"] == 90.0 and w["pct"] == -10.0 and w["on"] == "2026-11-06"
    bars = BARS + [bar("2026-11-11", 85.0, 88.0)]      # today's bar, so far
    assert worst_fall(100.0, BUY_AT, bars)["px"] == 85.0


def test_no_number_is_better_than_a_wrong_one():
    late = [b for b in BARS if b["date"] >= "2026-11-05"]
    assert worst_fall(100.0, BUY_AT, late) == {"ok": False, "why": "the price history does not reach back to the buy"}
    assert worst_fall(100.0, BUY_AT, [])["ok"] is False
    # a 1:1 bonus halves every old bar, so the buy price (200) no longer matches the bars
    rescaled = [bar("2026-11-04", 49.0, 50.0), bar("2026-11-05", 45.0, 48.0)]
    assert "split or bonus" in worst_fall(200.0, BUY_AT, rescaled)["why"]
    assert worst_fall(None, BUY_AT, BARS)["ok"] is False and worst_fall(0, BUY_AT, BARS)["ok"] is False
    assert worst_fall(100.0, None, BARS)["ok"] is False
    junk = [{"date": "2026-11-04", "low": None, "close": 100.0}, {"low": 90.0, "close": 90.0}, *BARS]
    assert worst_fall(100.0, BUY_AT, junk, end_at=datetime(2026, 11, 10, 14, 0, tzinfo=IST), end_px=102.0)["px"] == 90.0
