"""Open files of the process (trading_agent/fdwatch.py): count, name, warn early, lift the limit. A fake /proc and fake clocks; nothing real."""
from __future__ import annotations

import errno
import logging
import os
import resource
from datetime import date, timedelta

import pytest

from trading_agent import fdwatch
from trading_agent.watch import Watcher

# 104.16.1.1:443 and 2.2.2.2:80 as /proc/net/tcp writes them (the bytes of each address reversed)
CLOSE_WAIT = "  0: 0100007F:D2F4 01011068:01BB 08 00000000:00000000 00:00000000 00000000  1000        0 111 1 0000000000000000 100 0 0 10 0"
ESTABLISHED = "  1: 0100007F:D2F5 02020202:0050 01 00000000:00000000 00:00000000 00000000  1000        0 222 1 0000000000000000 100 0 0 10 0"
HEADER = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode"
V6_MAPPED = "  0: 00000000000000000000000001000000:D2F6 0000000000000000FFFF000004030201:01BB 08 00000000:00000000 00:00000000 00000000  1000        0 333 1 0000000000000000 100 0 0 10 0"


def make_proc(tmp_path, targets, tcp=(), tcp6=()):
    fd = tmp_path / "self" / "fd"
    fd.mkdir(parents=True)
    for n, t in targets.items():
        os.symlink(t, fd / str(n))   # a link may point at any text: that is how /proc names a socket or a pipe
    (tmp_path / "net").mkdir()
    (tmp_path / "net" / "tcp").write_text("\n".join([HEADER, *tcp]) + "\n")
    (tmp_path / "net" / "tcp6").write_text("\n".join([HEADER, *tcp6]) + "\n")
    return tmp_path


def test_the_handles_are_counted_and_named_by_kind(tmp_path):
    proc = make_proc(tmp_path, {0: "/dev/null", 1: "socket:[111]", 2: "socket:[111]", 3: "socket:[222]", 4: "socket:[999]", 5: "pipe:[55]", 6: "pipe:[56]",
                                7: "anon_inode:[eventpoll]", 8: "/opt/trading-agent/state/state.json", 9: "/opt/trading-agent/state/news/2026-11.jsonl",
                                10: "/opt/trading-agent/state/news/2026-10.jsonl", 11: "/opt/trading-agent/state/prices/archive.sqlite-wal (deleted)", 12: "socket:[111]"},
                     tcp=[CLOSE_WAIT, ESTABLISHED])
    assert fdwatch.count(proc) == 13
    got = dict(fdwatch.breakdown(proc, top=20))
    assert got["socket CLOSE_WAIT to 104.16.1.1:443"] == 3 and got["socket ESTABLISHED to 2.2.2.2:80"] == 1
    assert got["socket (not TCP)"] == 1 and got["pipe"] == 2 and got["anon_inode:[eventpoll]"] == 1
    assert got["file state/news/*.jsonl"] == 2 and got["file trading-agent/state/*.json"] == 1       # files by folder and kind
    assert got["file state/prices/*.sqlite-wal"] == 1 and got["file dev/*"] == 1                       # "(deleted)" is dropped
    assert fdwatch.breakdown(proc, top=1) == [("socket CLOSE_WAIT to 104.16.1.1:443", 3)]       # most numerous first, and bounded
    assert [k for k, n in fdwatch.breakdown(proc, top=20) if n == 2] == ["file state/news/*.jsonl", "pipe"]   # a tie goes by name


def test_an_ipv6_mapped_address_reads_as_ipv4_and_junk_lines_are_skipped(tmp_path):
    proc = make_proc(tmp_path, {3: "socket:[333]", 4: "socket:[444]"}, tcp=["garbage", "0: only three fields"], tcp6=[V6_MAPPED])
    got = dict(fdwatch.breakdown(proc))
    assert got["socket CLOSE_WAIT to 1.2.3.4:443"] == 1 and got["socket (not TCP)"] == 1


def test_nothing_to_read_is_unknown_not_an_error(tmp_path):
    assert fdwatch.count(tmp_path / "missing") is None and fdwatch.breakdown(tmp_path / "missing") == [] and fdwatch.summary(tmp_path / "missing") is None
    assert fdwatch.short(None) == "open files unknown"
    assert fdwatch.short({"open": 1019, "soft": 1024, "kinds": [("socket CLOSE_WAIT to 1.2.3.4:443", 900), ("pipe", 4)]}) == \
        "open files 1019 of 1024: socket CLOSE_WAIT to 1.2.3.4:443 x900, pipe x4"
    assert fdwatch.short({"open": 5, "soft": None, "kinds": []}) == "open files 5"


def test_the_soft_limit_is_lifted_towards_the_hard_one_and_never_above(monkeypatch):
    calls = []
    monkeypatch.setattr(resource, "setrlimit", lambda r, v: calls.append(v))
    monkeypatch.setattr(resource, "getrlimit", lambda r: (1024, 524288))
    assert fdwatch.raise_limit() == (1024, 65536) and calls == [(65536, 524288)]
    monkeypatch.setattr(resource, "getrlimit", lambda r: (1024, 4096))
    assert fdwatch.raise_limit() == (1024, 4096) and calls[-1] == (4096, 4096)
    calls.clear()
    monkeypatch.setattr(resource, "getrlimit", lambda r: (100000, 100000))
    assert fdwatch.raise_limit() == (100000, 100000) and calls == []                    # already high: untouched
    monkeypatch.setattr(resource, "getrlimit", lambda r: (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
    assert fdwatch.raise_limit() == (None, None) and fdwatch.limits() == (None, None)
    monkeypatch.setattr(resource, "getrlimit", lambda r: (1024, resource.RLIM_INFINITY))
    assert fdwatch.raise_limit() == (1024, 65536) and calls == [(65536, resource.RLIM_INFINITY)]
    monkeypatch.setattr(resource, "setrlimit", lambda r, v: (_ for _ in ()).throw(ValueError("not allowed")))
    assert fdwatch.raise_limit() == (1024, 1024)                                          # a refusal leaves it as it was


class Rig:
    """A monitor over fake counts, a fake clock and a fake day."""

    def __init__(self, monkeypatch, soft=1000):
        self.n, self.now, self.day, self.sent, self.collected = 100, 0.0, date(2026, 11, 20), [], []
        monkeypatch.setattr(fdwatch, "count", lambda proc="/proc": self.n)
        monkeypatch.setattr(fdwatch, "limits", lambda: (soft, soft))
        monkeypatch.setattr(fdwatch, "summary", lambda proc="/proc", top=8: {"open": self.n, "soft": soft, "kinds": [("socket CLOSE_WAIT to 1.2.3.4:443", self.n)]})
        monkeypatch.setattr(fdwatch.gc, "collect", lambda: self.collected.append(self.n) or 0)
        self.m = fdwatch.Monitor(alert=lambda subject, body: self.sent.append((subject, body)), clock=lambda: self.now, today=lambda: self.day)


def test_a_quiet_process_says_nothing(monkeypatch, caplog):
    r = Rig(monkeypatch)
    with caplog.at_level(logging.INFO, logger="trading_agent.fdwatch"):
        assert r.m.tick() == {"open": 100, "soft": 1000, "share": 0.1}
    assert caplog.records == [] and r.sent == [] and r.collected == []


def test_from_half_the_limit_it_names_the_handles_at_most_every_half_hour(monkeypatch, caplog):
    r = Rig(monkeypatch)
    r.n = 550
    with caplog.at_level(logging.INFO, logger="trading_agent.fdwatch"):
        r.m.tick()
        r.now += 60
        r.n = 560
        r.m.tick()                                  # a minute later and not much higher: quiet
        assert [x.levelname for x in caplog.records] == ["WARNING"] and "socket CLOSE_WAIT to 1.2.3.4:443 x550" in caplog.text
        r.now += 1800
        r.m.tick()                                  # half an hour later: once more
        assert len(caplog.records) == 2
        r.now += 10
        r.n = 690
        r.m.tick()                                  # a fifth higher at once: straight away
        assert len(caplog.records) == 3
    assert r.sent == [] and r.collected == [] and "x690" in r.m.last_summary


def test_from_seventy_percent_it_collects_garbage_once_and_alerts_once_a_day(monkeypatch, caplog):
    r = Rig(monkeypatch)
    r.n = 750
    with caplog.at_level(logging.INFO, logger="trading_agent.fdwatch"):
        r.m.tick()
        r.now += 60
        r.m.tick()
        r.now += 60
        r.m.tick()
    assert r.collected == [750]                                                         # one collection, not one a tick
    assert len(r.sent) == 1 and r.sent[0][0] == "Open files at 75% of the limit" and "Too many open files" in r.sent[0][1]
    assert "socket CLOSE_WAIT to 1.2.3.4:443 x750" in r.sent[0][1]
    r.day += timedelta(days=1)
    r.m.tick()
    assert len(r.sent) == 2                                                             # the next day it says so again
    r.now += 700
    r.m.tick()
    assert len(r.collected) == 2                                                        # and tries another collection after ten minutes


def test_a_collection_that_frees_handles_is_reported_and_may_clear_the_alert(monkeypatch, caplog):
    r = Rig(monkeypatch)
    r.n = 750
    monkeypatch.setattr(fdwatch.gc, "collect", lambda: setattr(r, "n", 300) or 0)
    with caplog.at_level(logging.INFO, logger="trading_agent.fdwatch"):
        out = r.m.tick()
    assert out["open"] == 300 and "freed 450 of 750" in caplog.text and r.sent == []


def test_the_monitor_never_raises_and_an_alert_that_fails_is_only_logged(monkeypatch, caplog):
    r = Rig(monkeypatch)
    r.n = 800
    m = fdwatch.Monitor(alert=lambda s, b: (_ for _ in ()).throw(RuntimeError("telegram down")), clock=lambda: r.now, today=lambda: r.day)
    with caplog.at_level(logging.INFO, logger="trading_agent.fdwatch"):
        assert m.tick()["open"] == 800
    assert "could not be sent" in caplog.text
    monkeypatch.setattr(fdwatch, "count", lambda proc="/proc": (_ for _ in ()).throw(RuntimeError("proc gone")))
    assert m.tick() is None
    monkeypatch.setattr(fdwatch, "count", lambda proc="/proc": None)                  # not Linux: nothing to watch
    assert m.tick() is None


def test_the_failure_note_falls_back_to_the_last_breakdown_when_the_handles_cannot_be_listed(monkeypatch, tmp_path):
    m = fdwatch.Monitor()
    m.last_summary = "open files 700 of 1024: socket CLOSE_WAIT to 1.2.3.4:443 x650"
    assert fdwatch.failure_note(m, tmp_path / "missing") == m.last_summary            # listing needs a handle: it is what is missing
    assert fdwatch.failure_note(None, tmp_path / "missing") == "open files unknown"
    proc = make_proc(tmp_path, {3: "socket:[111]"}, tcp=[CLOSE_WAIT])
    monkeypatch.setattr(fdwatch, "limits", lambda: (1024, 1024))
    assert fdwatch.failure_note(m, proc) == "open files 1 of 1024: socket CLOSE_WAIT to 104.16.1.1:443 x1"   # a fresh look wins when one is possible


def test_the_watch_loop_lifts_the_limit_counts_every_tick_and_says_what_held_the_handles(settings, monkeypatch):
    log: list = []
    monkeypatch.setattr(fdwatch, "raise_limit", lambda *a, **k: log.append("raise") or (1024, 65536))
    monkeypatch.setattr(fdwatch, "failure_note", lambda m, *a, **k: "open files 1019 of 1024: socket CLOSE_WAIT to 1.2.3.4:443 x900")

    class HB:
        def start(self, progress, *, stall_s, stop, **kw):
            pass

        def report_error(self, text):
            log.append(("error", text))
            w._stop.set()

        def stop(self):
            pass

    w = Watcher(settings, every=15, awake=None, heartbeat=HB())
    monkeypatch.setattr(w._fd, "tick", lambda: log.append("count"))

    def tick(force=False):
        raise OSError(errno.EMFILE, "Too many open files: 'state/state.json'")
    w.tick = tick
    w.run_forever()
    assert log[:2] == ["raise", "count"]
    kind, text = log[2]
    assert kind == "error" and text.startswith("OSError: [Errno 24] Too many open files: 'state/state.json' | open files 1019 of 1024: socket CLOSE_WAIT")
    assert w._last_error == text


def test_an_ordinary_tick_error_gets_no_open_files_note_and_the_alert_goes_through_the_notifier(settings, monkeypatch):
    sent, log = [], []

    class Notifier:
        def send(self, subject, body, *a, **k):
            sent.append((subject, body))

    class HB:
        def start(self, progress, *, stall_s, stop, **kw):
            pass

        def report_error(self, text):
            log.append(text)
            w._stop.set()

        def stop(self):
            pass

    w = Watcher(settings, every=15, awake=None, heartbeat=HB(), notifier=Notifier())
    monkeypatch.setattr(fdwatch, "failure_note", lambda *a, **k: pytest.fail("only a missing-handles error asks for the note"))

    def tick(force=False):
        raise OSError(errno.ENOENT, "No such file")
    w.tick = tick
    w.run_forever()
    assert log == ["FileNotFoundError: [Errno 2] No such file"]
    w._fd_alert("Open files at 75% of the limit", "body")
    assert sent == [("Open files at 75% of the limit", "body")]
    Watcher(settings, every=15, awake=None)._fd_alert("x", "y")                       # no notifier: nothing, and no error
