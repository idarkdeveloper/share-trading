"""Groww's Dividend Report (PDF) and Stocks P&L (xlsx), read by hand: synthetic files only."""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

import pytest

from trading_agent import groww_reports as gr

DIV_TEXT = """Client name: Test Person Client code: 0000000000 PAN: AB******1C Bank Account: SOME BANK (******0000)
Equity dividends from 01-04-2025 to 05-11-2026
Company Name ISIN Ex Date Number of Shares Dividend per Share Net Dividend Amount
ALPHA INDUSTRIES LTD. INE000A01011 28-01-2026 91 Rs. 4.5 Rs. 409.50
BETA & SONS LIMITED INE000B01012 22-05-2026 2 Rs. 38.0 Rs. 76.00
GAMMA SERV LT INE000C01013 14-10-2026 1,000 Rs. 12.5 Rs. 12,500.00
Company Name ISIN Ex Date Number of Shares Dividend per Share Net Dividend Amount
Total dividend amount Rs. 12,985.50
"""


def make_xlsx(sheets: dict[str, list[list]], *, shared: bool = True) -> bytes:
    """A minimal .xlsx: strings in the shared-strings table (or inline), numbers as numbers."""
    strings: list[str] = []

    def cell(ref: str, v) -> str:
        if v is None:
            return ""
        if isinstance(v, (int, float)):
            return f'<c r="{ref}"><v>{v}</v></c>'
        if shared:
            strings.append(str(v))
            return f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(v))}</t></is></c>'

    def col(i: int) -> str:
        return chr(65 + i)
    sheet_xml, wb_sheets, rels = {}, [], []
    for n, (name, rows) in enumerate(sheets.items(), start=1):
        body = "".join(f'<row r="{r}">' + "".join(cell(f"{col(c)}{r}", v) for c, v in enumerate(row)) + "</row>"
                       for r, row in enumerate(rows, start=1))
        sheet_xml[f"xl/worksheets/sheet{n}.xml"] = ('<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                                                    f"<sheetData>{body}</sheetData></worksheet>")
        wb_sheets.append(f'<sheet name="{escape(name)}" sheetId="{n}" r:id="rId{n}"/>')
        rels.append(f'<Relationship Id="rId{n}" Target="worksheets/sheet{n}.xml"/>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + "".join(wb_sheets) + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(rels) + "</Relationships>")
        if shared:
            z.writestr("xl/sharedStrings.xml", '<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                       + "".join(f"<si><t>{escape(s)}</t></si>" for s in strings) + "</sst>")
        for path, xml in sheet_xml.items():
            z.writestr(path, xml)
    return buf.getvalue()


LOT_HEAD = ["Stock name", "ISIN", "Quantity", "Buy date", "Buy price", "Buy value", "Sell date", "Sell price", "Sell value", "Realised P&L", "Remark"]
PNL_ROWS = [
    ["Name", "Test Person"], ["Unique Client Code", "0000000000"], ["P&L Statement for stocks from 01-04-2025 to 05-11-2026"],
    ["Summary"], ["P&L"], ["Realised P&L", 1210.0], ["Unrealised P&L", 200.0], ["Charges"],
    ["Brokerage", 20.0], ["STT", 15.5], ["DP Charges", 0.0], ["Total GST", 3.6], ["Total", 39.1], [],
    ["Realised trades"], [], LOT_HEAD,
    ["ALPHA INDUSTRIES LTD.", "INE000A01011", 10.0, "01-01-2025", 100.0, 1000.0, "02-01-2026", 110.0, 1100.0, 100.0, ""],
    ["ALPHA INDUSTRIES LTD.", "INE000A01011", 5.0, "10-03-2025", 100.0, 500.0, "10-03-2025", 101.0, 505.0, 5.0, "Intraday trade"],
    ["BETA & SONS LIMITED", "INE000B01012", 4.0, "01-06-2025", 250.0, 1000.0, "01-10-2025", 530.0 * 4 / 4 * 1.0, 1105.0, 105.0, ""],
    [], ["Unrealised trades"], [],
    ["Stock name", "ISIN", "Quantity", "Buy date", "Buy price", "Buy value", "Closing date", "Closing price", "Closing value", "Unrealised P&L", "Remark"],
    ["GAMMA SERV LT", "INE000C01013", 20.0, "01-07-2025", 100.0, 2000.0, "05-11-2026", 110.0, 2200.0, 200.0, ""],
    [], ["Disclaimer: "], ["This report is provided at your request."],
]


def test_read_xlsx_reads_shared_and_inline_strings_numbers_and_gaps():
    for shared in (True, False):
        sheets = gr.read_xlsx(make_xlsx({"Trade Level": [["a", None, 3.5], [], ["b & c", 7]], "Other": [["x"]]}, shared=shared))
        assert sheets["Trade Level"] == [["a", None, 3.5], [], ["b & c", 7.0]] and sheets["Other"] == [["x"]]


def test_read_xlsx_refuses_what_is_not_a_spreadsheet_or_looks_hostile():
    with pytest.raises(gr.ReportError, match="not an .xlsx"):
        gr.read_xlsx(b"hello")
    evil = io.BytesIO()
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("xl/workbook.xml", '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><workbook/>')
        z.writestr("xl/_rels/workbook.xml.rels", "<Relationships/>")
    with pytest.raises(gr.ReportError, match="unexpected structure"):
        gr.read_xlsx(evil.getvalue())


def test_dividend_rows_are_parsed_and_checked_against_the_reports_own_total():
    d = gr.parse_dividend_text(DIV_TEXT)
    assert [r["company"] for r in d["rows"]] == ["ALPHA INDUSTRIES LTD.", "BETA & SONS LIMITED", "GAMMA SERV LT"]
    assert d["rows"][0] == {"company": "ALPHA INDUSTRIES LTD.", "isin": "INE000A01011", "ex_date": "2026-01-28", "shares": 91.0,
                            "per_share": 4.5, "amount": 409.5}
    assert d["rows"][2]["shares"] == 1000.0 and d["rows"][2]["amount"] == 12500.0                # thousands separators
    assert d["total"] == 12985.5 and d["check"] is True and (d["from"], d["to"]) == ("2025-04-01", "2026-11-05")
    off = gr.parse_dividend_text(DIV_TEXT.replace("12,985.50", "12,900.00"))
    assert off["check"] is False and off["total"] == 12900.0                                       # said, not hidden
    assert gr.parse_dividend_text(DIV_TEXT.split("Total dividend")[0])["check"] is None
    assert "Test Person" not in str(d) and "0000000000" not in str(d)                             # the header is never kept
    with pytest.raises(gr.ReportError, match="no dividend rows"):
        gr.parse_dividend_text("Hello\nWorld")


def test_pnl_workbook_gives_the_summary_charges_and_every_lot():
    p = gr.parse_pnl_xlsx(make_xlsx({"Trade Level": PNL_ROWS, "Scrip Level": [["Realised"]]}))
    assert (p["from"], p["to"]) == ("2025-04-01", "2026-11-05") and p["realised_pnl"] == 1210.0 and p["unrealised_pnl"] == 200.0
    assert p["charges"] == {"Brokerage": 20.0, "STT": 15.5, "DP Charges": 0.0, "Total GST": 3.6} and p["charges_total"] == 39.1
    assert [(l["name"], l["qty"], l["buy_date"], l["end_date"], l["intraday"]) for l in p["realised"]] == [
        ("ALPHA INDUSTRIES LTD.", 10.0, "2025-01-01", "2026-01-02", False),
        ("ALPHA INDUSTRIES LTD.", 5.0, "2025-03-10", "2025-03-10", True),
        ("BETA & SONS LIMITED", 4.0, "2025-06-01", "2025-10-01", False)]
    (held,) = p["unrealised"]
    assert held["name"] == "GAMMA SERV LT" and held["buy_value"] == 2000.0 and held["end_value"] == 2200.0 and held["realised"] is False
    assert "Test Person" not in str(p) and "0000000000" not in str(p)


def test_a_workbook_without_the_trade_level_sheet_or_trades_is_refused():
    with pytest.raises(gr.ReportError, match="Trade Level"):
        gr.parse_pnl_xlsx(make_xlsx({"Sheet1": [["x"]]}))
    with pytest.raises(gr.ReportError, match="no trades"):
        gr.parse_pnl_xlsx(make_xlsx({"Trade Level": [["Realised P&L", 1.0]]}))


def test_parse_report_tells_the_two_apart_by_what_they_start_with(monkeypatch):
    monkeypatch.setattr(gr, "parse_dividend_pdf", lambda data: gr.parse_dividend_text(DIV_TEXT))
    kind, d = gr.parse_report(b"%PDF-1.7 whatever")
    assert kind == "dividends" and d["total"] == 12985.5
    kind, p = gr.parse_report(make_xlsx({"Trade Level": PNL_ROWS}))
    assert kind == "pnl" and len(p["realised"]) == 3
    with pytest.raises(gr.ReportError, match="Dividend Report"):
        gr.parse_report(b"just text")
    with pytest.raises(gr.ReportError, match="too large"):
        gr.parse_report(b"%PDF-" + b"0" * gr.MAX_REPORT_BYTES)


def test_a_damaged_pdf_is_a_report_error_not_a_crash():
    with pytest.raises(gr.ReportError, match="could not read this PDF"):
        gr.parse_dividend_pdf(b"%PDF-1.4 this is not really a pdf")


def test_saved_reports_keep_only_rows_and_survive_a_broken_file(tmp_path):
    assert gr.load(tmp_path) == {}
    gr.save(tmp_path, "dividends", gr.parse_dividend_text(DIV_TEXT))
    gr.save(tmp_path, "pnl", gr.parse_pnl_xlsx(make_xlsx({"Trade Level": PNL_ROWS})))
    data = gr.load(tmp_path)
    assert set(data) == {"dividends", "pnl"} and data["dividends"]["imported_at"] and len(data["pnl"]["realised"]) == 3
    gr.path_for(tmp_path).write_text("{broken", encoding="utf-8")
    assert gr.load(tmp_path) == {}
    gr.path_for(tmp_path).write_text("[1]", encoding="utf-8")
    assert gr.load(tmp_path) == {}
