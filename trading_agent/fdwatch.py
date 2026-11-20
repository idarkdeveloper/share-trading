"""Open files of this process: how many, against the limit, and what they are.

A service that runs out of file handles ("OSError: [Errno 24] Too many open files") can no longer open its own state file, so
every tick of the watch loop fails until something frees a handle, and by then it cannot even look at what holds them (listing
the handles needs one). So this looks early: ``Monitor.tick`` counts the handles once per tick, and from half the limit it logs
what they are (sockets by TCP state and remote address, pipes, files by folder), from 70% it tries one garbage collection and
sends one alert a day. ``raise_limit`` lifts the soft limit (systemd's default is 1024) towards the hard one when a service
starts. Linux only (``/proc``); anywhere else every function answers "unknown" and does nothing. Read-only: it never closes
anything itself and never raises into its caller.
"""
from __future__ import annotations

import gc
import ipaddress
import logging
import os
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from .timezones import IST

log = logging.getLogger(__name__)

WANT_SOFT = 65536        # the soft limit a service asks for at start (never above the hard limit)
WARN_SHARE = 0.5         # from this share of the limit: log what the handles are
ALERT_SHARE = 0.7        # from this share: try a garbage collection and send one alert a day
LOG_EVERY_S = 1800.0     # a warning at most this often (unless the count grows by a fifth)
COLLECT_EVERY_S = 600.0
TOP = 8
TCP_STATES = {"01": "ESTABLISHED", "02": "SYN_SENT", "03": "SYN_RECV", "04": "FIN_WAIT1", "05": "FIN_WAIT2", "06": "TIME_WAIT",
              "07": "CLOSE", "08": "CLOSE_WAIT", "09": "LAST_ACK", "0A": "LISTEN", "0B": "CLOSING"}


def limits() -> tuple[int | None, int | None]:
    """(soft, hard) limit of open files; None for "no limit" or unknown."""
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except (ImportError, OSError, ValueError):
        return None, None
    inf = resource.RLIM_INFINITY
    return (None if soft == inf else soft), (None if hard == inf else hard)


def raise_limit(want: int = WANT_SOFT) -> tuple[int | None, int | None]:
    """Lift the soft limit to ``want``, or to the hard limit when that is lower. Returns (before, after); (None, None) when it
    cannot be read or there is no limit. A refusal leaves the limit as it was."""
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except (ImportError, OSError, ValueError):
        return None, None
    inf = resource.RLIM_INFINITY
    if soft == inf:
        return None, None
    target = want if hard == inf else min(want, hard)
    if target <= soft:
        return soft, soft
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    except (ValueError, OSError):
        return soft, soft
    return soft, target


def _fd_dir(proc: Path | str) -> Path:
    return Path(proc) / "self" / "fd"


def count(proc: Path | str = "/proc") -> int | None:
    """How many handles this process holds now (the listing's own handle included), or None when it cannot be read."""
    try:
        return len(os.listdir(_fd_dir(proc)))
    except OSError:
        return None


def _addr(hexaddr: str) -> str:
    """'0100007F:1F90' (as /proc/net/tcp writes it) -> '127.0.0.1:8080'; the IPv6 form too."""
    ip_hex, port_hex = hexaddr.split(":")
    raw = bytes.fromhex(ip_hex)
    if len(raw) == 4:
        ip: Any = ipaddress.IPv4Address(raw[::-1])
    else:
        ip = ipaddress.IPv6Address(b"".join(raw[i:i + 4][::-1] for i in range(0, 16, 4)))
        ip = ip.ipv4_mapped or ip
    return f"{ip}:{int(port_hex, 16)}"


def _tcp_table(proc: Path | str) -> dict[str, tuple[str, str]]:
    """{socket inode: (TCP state, remote address)} from /proc/net/tcp and tcp6; empty when unreadable."""
    out: dict[str, tuple[str, str]] = {}
    for name in ("tcp", "tcp6"):
        try:
            lines = (Path(proc) / "net" / name).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            p = line.split()
            try:
                out[p[9]] = (TCP_STATES.get(p[3].upper(), p[3]), _addr(p[2]))
            except (IndexError, ValueError):
                continue
    return out


def _kind(target: str, tcp: dict[str, tuple[str, str]]) -> str:
    if target.startswith("socket:["):
        hit = tcp.get(target[8:-1])
        return f"socket {hit[0]} to {hit[1]}" if hit else "socket (not TCP)"
    if target.startswith("pipe:"):
        return "pipe"
    if target.startswith("anon_inode:"):
        return target
    if target.startswith("/"):
        path = target.removesuffix(" (deleted)")
        folder = "/".join([x for x in os.path.dirname(path).split("/") if x][-2:])
        return f"file {folder}/*{os.path.splitext(path)[1]}"
    return "other"


def breakdown(proc: Path | str = "/proc", top: int = TOP) -> list[tuple[str, int]]:
    """The handles by kind, most numerous first: [("socket CLOSE_WAIT to 1.2.3.4:443", 600), ("pipe", 4), ...]."""
    tcp = _tcp_table(proc)
    kinds: Counter[str] = Counter()
    base = _fd_dir(proc)
    try:
        names = os.listdir(base)
    except OSError:
        return []
    for name in names:
        try:
            kinds[_kind(os.readlink(base / name), tcp)] += 1
        except OSError:
            continue   # the handle closed meanwhile (or is the listing's own)
    return sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0]))[:top]   # most numerous first; a tie by name, so the order is stable


def summary(proc: Path | str = "/proc", top: int = TOP) -> dict[str, Any] | None:
    """{open, soft, share, kinds} now, or None when the count cannot be read."""
    n = count(proc)
    if n is None:
        return None
    soft, _hard = limits()
    return {"open": n, "soft": soft, "share": round(n / soft, 3) if soft else None, "kinds": breakdown(proc, top)}


def short(s: dict[str, Any] | None) -> str:
    """One line for a log, an alert or a failure ping."""
    if not s:
        return "open files unknown"
    of = f" of {s['soft']}" if s.get("soft") else ""
    kinds = ", ".join(f"{k} x{n}" for k, n in s.get("kinds") or [])
    return f"open files {s['open']}{of}" + (f": {kinds}" if kinds else "")


class Monitor:
    """Counts the open files once per call to ``tick`` (a directory listing, well under a millisecond) and speaks up as they climb.
    ``alert(subject, body)`` is sent at most once a day. Never raises."""

    def __init__(self, alert: Callable[[str, str], None] | None = None, *, proc: Path | str = "/proc",
                 clock: Callable[[], float] = time.monotonic, today: Callable[[], date] | None = None,
                 warn_share: float = WARN_SHARE, alert_share: float = ALERT_SHARE):
        self._alert, self._proc, self._clock = alert, proc, clock
        self._today = today or (lambda: datetime.now(IST).date())
        self._warn, self._alert_at = warn_share, alert_share
        self._logged = -1e9
        self._collected = -1e9
        self._peak = 0
        self._alerted: date | None = None
        self.last: dict[str, Any] | None = None   # the latest count, and the latest breakdown seen (see ``last_summary``)
        self.last_summary: str | None = None      # what the handles were the last time it spoke up

    def tick(self) -> dict[str, Any] | None:
        try:
            return self._tick()
        except Exception:  # noqa: BLE001 - a monitor never stops the loop it watches
            log.debug("open-file check failed", exc_info=True)
            return None

    def _tick(self) -> dict[str, Any] | None:
        n = count(self._proc)
        soft, _hard = limits()
        if n is None or not soft:
            return None
        share = n / soft
        self.last = {"open": n, "soft": soft, "share": round(share, 3)}
        if share < self._warn:
            self._peak = n
            return self.last
        now = self._clock()
        if share >= self._alert_at and now - self._collected >= COLLECT_EVERY_S:
            self._collected = now
            gc.collect()   # handles held only by unreachable objects (a cycle) close now
            again = count(self._proc)
            if again is not None and again < n:
                log.warning("open files: a garbage collection freed %d of %d", n - again, n)
                n, share = again, again / soft
                self.last = {"open": n, "soft": soft, "share": round(share, 3)}
        if now - self._logged >= LOG_EVERY_S or n > self._peak * 1.2:
            self._logged, self._peak = now, max(self._peak, n)
            s = summary(self._proc)
            self.last_summary = short(s)
            log.warning("%s (%.0f%% of the limit)", self.last_summary, share * 100)
        if share >= self._alert_at and self._alert is not None and self._alerted != self._today():
            self._alerted = self._today()
            body = (self.last_summary or short(summary(self._proc))) + "\n\nThe watch stops working when this reaches the limit " \
                   "(OSError: Too many open files). Restarting the service frees them; the lines above name what holds them."
            try:
                self._alert(f"Open files at {share * 100:.0f}% of the limit", body)
            except Exception:  # noqa: BLE001
                log.warning("the open-files alert could not be sent")
        return self.last


def failure_note(monitor: Monitor | None, proc: Path | str = "/proc") -> str:
    """What to add to a "Too many open files" failure ping: a fresh breakdown when one can still be taken, else the last one the
    monitor saw (listing the handles needs a handle, which is exactly what is missing)."""
    try:
        gc.collect()
        s = summary(proc)
        if s and s.get("kinds"):
            return short(s)
    except Exception:  # noqa: BLE001
        pass
    return (monitor.last_summary if monitor is not None else None) or "open files unknown"
