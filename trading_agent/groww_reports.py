"""Groww's own reports, imported by hand: the Dividend Report (PDF) and the Stocks P&L statement (xlsx).

Groww's API gives the app only today's orders and the current holdings, so dividends, XIRR and realised profit cannot be
rebuilt from it. The two reports a client downloads from Groww hold what is missing:

* **Dividend Report (PDF):** one row per dividend, with the ex-date, the number of shares, the dividend per share and the net
  amount.
* **Stocks P&L (xlsx), sheet "Trade Level":** a summary (realised and unrealised profit, every charge), every **realised** lot
  with its buy date and sell date, and every **unrealised** lot (the shares still held) with its buy date and the closing
  price on the report date. Together the lots are the complete cash flow of the period, which is what XIRR needs.

Only the table rows are kept (names, ISINs, dates, amounts). The client code, PAN and bank account in the reports' headers are
never read into the saved file. The spreadsheet is read with the standard library (a zip of XML; no new dependency) and the
PDF with pypdf, already a dependency. Everything here is display only: nothing places an order or touches Groww.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

REPORTS_FILE = "groww_reports.json"
MAX_REPORT_BYTES = 3 * 1024 * 1024        # a decoded report; they are tens of kilobytes
_MAX_XML_BYTES = 20 * 1024 * 1024

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
       "p": "http://schemas.openxmlformats.org/package/2006/relationships"}


class ReportError(ValueError):
    """A report that is not what it should be (said to the person importing it)."""


# --------------------------------------------------------------------------- #
# the spreadsheet: a zip of XML
# --------------------------------------------------------------------------- #
def _col(ref: str) -> int:
    """The 0-based column of a cell reference such as "C12"."""
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def _xml(z: zipfile.ZipFile, name: str) -> ET.Element:
    info = z.getinfo(name)
    if info.file_size > _MAX_XML_BYTES:
        raise ReportError("the spreadsheet is too large")
    raw = z.read(name)
    if b"<!DOCTYPE" in raw[:4096] or b"<!ENTITY" in raw[:4096]:   # no entity tricks in a file we did not write
        raise ReportError("the spreadsheet has an unexpected structure")
    return ET.fromstring(raw)


def read_xlsx(data: bytes) -> dict[str, list[list[Any]]]:
    """{sheet name: rows}, each row a list of cell values (str, float or None), from the bytes of an .xlsx file."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ReportError("this is not an .xlsx file") from None
    with z:
        if sum(i.file_size for i in z.infolist()) > 10 * _MAX_XML_BYTES:
            raise ReportError("the spreadsheet is too large")
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in _xml(z, "xl/sharedStrings.xml").findall("m:si", _NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
        rels = {r.get("Id"): r.get("Target") or "" for r in _xml(z, "xl/_rels/workbook.xml.rels").findall("p:Relationship", _NS)}
        out: dict[str, list[list[Any]]] = {}
        for sh in _xml(z, "xl/workbook.xml").findall("m:sheets/m:sheet", _NS):
            target = rels.get(sh.get(f"{{{_NS['r']}}}id") or "", "")
            path = target.lstrip("/") if target.startswith("/") else "xl/" + target
            if path not in z.namelist():
                continue
            rows: list[list[Any]] = []
            for row in _xml(z, path).findall("m:sheetData/m:row", _NS):
                cells: dict[int, Any] = {}
                for c in row.findall("m:c", _NS):
                    kind, v = c.get("t"), c.find("m:v", _NS)
                    if kind == "inlineStr":
                        val: Any = "".join(t.text or "" for t in c.iter(f"{{{_NS['m']}}}t"))
                    elif v is None or v.text is None:
                        continue
                    elif kind == "s":
                        val = shared[int(v.text)] if int(v.text) < len(shared) else ""
                    elif kind in ("str", "e"):
                        val = v.text
                    elif kind == "b":
                        val = v.text == "1"
                    else:
                        try:
                            val = float(v.text)
                        except ValueError:
                            val = v.text
                    cells[_col(c.get("r") or "A1")] = val
                rows.append([cells.get(i) for i in range(max(cells) + 1)] if cells else [])
            out[sh.get("name") or f"sheet{len(out) + 1}"] = rows
    return out


# --------------------------------------------------------------------------- #
# small readers
# --------------------------------------------------------------------------- #
def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"(?i)rs\.?|₹|,|\s", "", str(v))
    try:
        return float(s)
    except ValueError:
        return None


def _iso(v: Any) -> str | None:
    """DD-MM-YYYY (Groww's format) or YYYY-MM-DD as an ISO date; None when it is neither."""
    s = str(v or "").strip()
    m = re.fullmatch(r"(\d{2})[-/](\d{2})[-/](\d{4})", s)
    try:
        if m:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return date.fromisoformat(s).isoformat()
    except ValueError:
        return None
    return None


_ISIN = r"IN[EF][0-9A-Z]{8}[0-9]"
_DIV_ROW = re.compile(rf"^(?P<name>.+?)\s+(?P<isin>{_ISIN})\s+(?P<ex>\d{{2}}-\d{{2}}-\d{{4}})\s+(?P<shares>[\d,]+(?:\.\d+)?)\s+"
                      r"Rs\.?\s*(?P<dps>[\d,]+(?:\.\d+)?)\s+Rs\.?\s*(?P<amt>[\d,]+(?:\.\d+)?)$")
_DIV_TOTAL = re.compile(r"Total dividend amount\s+Rs\.?\s*([\d,]+(?:\.\d+)?)", re.I)
_DIV_RANGE = re.compile(r"dividends from (\d{2}-\d{2}-\d{4}) to (\d{2}-\d{2}-\d{4})", re.I)


def parse_dividend_text(text: str) -> dict[str, Any]:
    """The Dividend Report's text (one row per line) as {"rows", "total", "from", "to", "check"}. ``check`` says whether the
    rows add up to the report's own total (None when it prints none)."""
    rows: list[dict[str, Any]] = []
    total: float | None = None
    rng: tuple[str | None, str | None] = (None, None)
    for line in text.splitlines():
        line = line.strip()
        m = _DIV_ROW.match(line)
        if m:
            ex, shares, dps, amt = _iso(m["ex"]), _num(m["shares"]), _num(m["dps"]), _num(m["amt"])
            if ex and shares is not None and amt is not None:
                rows.append({"company": m["name"].strip(), "isin": m["isin"], "ex_date": ex, "shares": shares,
                             "per_share": dps, "amount": amt})
            continue
        t = _DIV_TOTAL.search(line)
        if t:
            total = _num(t.group(1))
        r = _DIV_RANGE.search(line)
        if r:
            rng = (_iso(r.group(1)), _iso(r.group(2)))
    if not rows and total is None:
        raise ReportError("no dividend rows found: is this Groww's Dividend Report (PDF)?")
    s = round(sum(r["amount"] for r in rows), 2)
    return {"rows": rows, "total": s if total is None else total, "from": rng[0], "to": rng[1],
            "check": None if total is None else abs(s - total) < 0.01}


def parse_dividend_pdf(data: bytes) -> dict[str, Any]:
    try:
        from pypdf import PdfReader
        text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)
    except ImportError:
        raise ReportError("reading a PDF needs the pypdf package (pip install -r requirements.txt)") from None
    except Exception as e:  # noqa: BLE001 - a damaged or unreadable PDF
        raise ReportError(f"could not read this PDF ({type(e).__name__})") from None
    return parse_dividend_text(text)


_LOT_HEAD = ["stock name", "isin", "quantity", "buy date", "buy price", "buy value"]


def _find(rows: list[list[Any]], label: str) -> int | None:
    for i, r in enumerate(rows):
        if r and isinstance(r[0], str) and r[0].strip().lower().startswith(label.lower()):
            return i
    return None


def _lots(rows: list[list[Any]], start: int, *, realised: bool) -> list[dict[str, Any]]:
    """The lot rows under the header at ``start``, up to the first blank row."""
    out: list[dict[str, Any]] = []
    for r in rows[start + 1:]:
        if not r or not r[0] or not isinstance(r[0], str) or len(r) < 10:
            break
        qty, buy_d, buy_v = _num(r[2]), _iso(r[3]), _num(r[5])
        end_d, end_v, pnl = _iso(r[6]), _num(r[8]), _num(r[9])
        if qty is None or buy_d is None or buy_v is None or end_d is None or end_v is None:
            continue
        out.append({"name": r[0].strip(), "isin": str(r[1] or "").strip(), "qty": qty, "buy_date": buy_d, "buy_value": buy_v,
                    "end_date": end_d, "end_value": end_v, "pnl": pnl if pnl is not None else round(end_v - buy_v, 2),
                    "intraday": "intraday" in str(r[10] if len(r) > 10 else "").lower(), "realised": realised})
    return out


def parse_pnl_rows(sheets: dict[str, list[list[Any]]]) -> dict[str, Any]:
    """The Stocks P&L workbook (as read by ``read_xlsx``): summary, charges and every realised and unrealised lot."""
    rows = sheets.get("Trade Level")
    if rows is None:
        raise ReportError('no "Trade Level" sheet: is this Groww\'s Stocks P&L report (xlsx)?')

    def val(label: str) -> float | None:
        i = _find(rows, label)
        return _num(rows[i][1]) if i is not None and len(rows[i]) > 1 else None
    head = next((str(r[0]) for r in rows if r and isinstance(r[0], str) and r[0].lower().startswith("p&l statement")), "")
    dates = re.findall(r"\d{2}-\d{2}-\d{4}", head)
    charges: dict[str, float] = {}
    c0 = _find(rows, "Charges")
    if c0 is not None:
        for r in rows[c0 + 1:]:
            if not r or not isinstance(r[0], str) or _num(r[1] if len(r) > 1 else None) is None:
                break
            charges[r[0].strip()] = round(_num(r[1]) or 0.0, 2)
    total_charges = charges.pop("Total", None)
    r0, u0 = _find(rows, "Realised trades"), _find(rows, "Unrealised trades")
    realised = _lots(rows, r0 + 2, realised=True) if r0 is not None else []
    unrealised = _lots(rows, u0 + 2, realised=False) if u0 is not None else []
    if not realised and not unrealised:
        raise ReportError("no trades found in the P&L report")
    return {"from": _iso(dates[0]) if dates else None, "to": _iso(dates[1]) if len(dates) > 1 else None,
            "realised_pnl": val("Realised P&L"), "unrealised_pnl": val("Unrealised P&L"),
            "charges": charges, "charges_total": total_charges if total_charges is not None else round(sum(charges.values()), 2),
            "realised": realised, "unrealised": unrealised}


def parse_pnl_xlsx(data: bytes) -> dict[str, Any]:
    return parse_pnl_rows(read_xlsx(data))


def parse_report(data: bytes) -> tuple[str, dict[str, Any]]:
    """("dividends" | "pnl", parsed) from a report's bytes, told apart by what they start with."""
    if len(data) > MAX_REPORT_BYTES:
        raise ReportError("this file is too large to be one of Groww's reports")
    if data[:5] == b"%PDF-":
        return "dividends", parse_dividend_pdf(data)
    if data[:2] == b"PK":
        return "pnl", parse_pnl_xlsx(data)
    raise ReportError("send Groww's Dividend Report (PDF) or Stocks P&L report (xlsx)")


# --------------------------------------------------------------------------- #
# keeping them
# --------------------------------------------------------------------------- #
def path_for(state_dir: Path) -> Path:
    return Path(state_dir) / REPORTS_FILE


def load(state_dir: Path) -> dict[str, Any]:
    """{"dividends": {...}, "pnl": {...}} as saved (each with ``imported_at``); empty when nothing is saved or it is unreadable."""
    try:
        data = json.loads(path_for(state_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(state_dir: Path, kind: str, parsed: dict[str, Any]) -> None:
    from .state import atomic_write
    data = load(state_dir)
    data[kind] = {**parsed, "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    atomic_write(path_for(state_dir), json.dumps(data))
