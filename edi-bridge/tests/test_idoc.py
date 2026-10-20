"""SAP export: erp_orders.json -> ORDERS05 IDocs in XML."""

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime

import pytest
from conftest import SAMPLES
from edi_bridge import idoc
from edi_bridge.cli import main
from edi_bridge.envelope import ControlNumbers

WHEN = datetime(2026, 10, 19, 15, 30, 5)


def export(name: str, out, *options: str) -> list[ET.Element]:
    """inbound, then sap, into the same folder; returns the <IDOC> elements."""
    assert main(["inbound", str(SAMPLES / name), "--out", str(out)]) == 0
    assert main(["sap", str(out / "erp_orders.json"), "--out", str(out), *options]) == 0
    root = ET.parse(out / "idoc_orders.xml").getroot()
    assert root.tag == "ORDERS05"
    return root.findall("IDOC")


def segments(parent: ET.Element, name: str) -> dict[str, ET.Element]:
    """Repeated segments keyed by their qualifier (QUALF, IDDAT or PARVW)."""
    return {seg[0].text: seg for seg in parent.findall(name)}


def fields(seg: ET.Element) -> dict[str, str]:
    return {child.tag: child.text for child in seg if child.get("SEGMENT") is None}


# --- through the command line ------------------------------------------------------

def test_one_idoc_per_order(tmp_path):
    assert len(export("850_good.x12", tmp_path / "good")) == 1
    idocs = export("850_multi.x12", tmp_path / "multi")
    assert len(idocs) == 2 and all(i.get("BEGIN") == "1" for i in idocs)
    assert [i.find("E1EDK02/BELNR").text for i in idocs] == ["PO45003", "PO45004"]


def test_control_record(tmp_path):
    dc = export("850_good.x12", tmp_path)[0].find("EDI_DC40")
    assert dc.get("SEGMENT") == "1"
    values = fields(dc)
    assert re.fullmatch(r"\d{16}", values.pop("DOCNUM"))
    assert re.fullmatch(r"\d{8}", values.pop("CREDAT"))
    assert re.fullmatch(r"\d{6}", values.pop("CRETIM"))
    assert values == {"TABNAM": "EDI_DC40", "MANDT": "100", "DIRECT": "2",
                      "IDOCTYP": "ORDERS05", "MESTYP": "ORDERS",
                      "SNDPOR": idoc.SENDER_PORT, "SNDPRT": "LS", "SNDPRN": "SYTELINE",
                      "RCVPOR": idoc.RECEIVER_PORT, "RCVPRT": "LS", "RCVPRN": "SAPCLNT100"}


def test_control_record_options(tmp_path):
    dc = fields(export("850_good.x12", tmp_path, "--sender", "SLDEMO", "--receiver",
                       "S4HCLNT200", "--client", "200")[0].find("EDI_DC40"))
    assert (dc["SNDPRN"], dc["RCVPRN"], dc["MANDT"]) == ("SLDEMO", "S4HCLNT200", "200")


def test_docnum_keeps_increasing_between_runs(tmp_path):
    first = export("850_multi.x12", tmp_path)
    second = export("850_multi.x12", tmp_path)
    assert [int(i.find("EDI_DC40/DOCNUM").text) for i in first + second] == [1, 2, 3, 4]


def test_header_segments(tmp_path):
    doc = export("850_good.x12", tmp_path)[0]
    assert [seg.tag for seg in doc] == ["EDI_DC40", "E1EDK01", "E1EDK03", "E1EDK03", "E1EDKA1",
                                        "E1EDKA1", "E1EDK02", "E1EDP01", "E1EDP01"]
    assert fields(doc.find("E1EDK01")) == {"BELNR": "EDIPO45001"}
    assert fields(doc.find("E1EDK02")) == {"QUALF": "001", "BELNR": "PO45001"}
    dates = segments(doc, "E1EDK03")
    assert dates["012"].find("DATUM").text == "20261019"
    assert dates["002"].find("DATUM").text == "20261102"
    partners = segments(doc, "E1EDKA1")
    assert fields(partners["AG"]) == {"PARVW": "AG", "PARTN": "MNO-NORTH",
                                      "NAME1": "North Mobile Networks (synthetic)"}
    assert fields(partners["WE"]) == {"PARVW": "WE", "PARTN": "NDC01"}


def test_lines_carry_quantities_and_both_material_numbers(tmp_path):
    lines = export("850_good.x12", tmp_path)[0].findall("E1EDP01")
    assert [fields(line) for line in lines] == [
        {"POSEX": "1", "MENGE": "500", "MENEE": "PCE", "VPREI": "0.85"},
        {"POSEX": "2", "MENGE": "1000", "MENEE": "PCE", "VPREI": "0.40"}]
    assert [seg.tag for seg in lines[0]] == ["POSEX", "MENGE", "MENEE", "VPREI",
                                             "E1EDP20", "E1EDP19", "E1EDP19"]
    materials = [{q: seg.find("IDTNR").text for q, seg in segments(line, "E1EDP19").items()}
                 for line in lines]
    assert materials == [{"002": "UICC-TRIO-128K", "001": "MN-SIM-0128"},
                         {"002": "ESIM-PROF-A", "001": "MN-ESIM-A01"}]
    assert fields(lines[1].find("E1EDP20")) == {"WMENG": "1000", "EDATU": "20261102"}


def test_multi_keeps_each_orders_own_data(tmp_path):
    idocs = export("850_multi.x12", tmp_path)
    assert [i.find("E1EDK01/BELNR").text for i in idocs] == ["EDIPO45003", "EDIPO45004"]
    assert [segments(i, "E1EDKA1")["WE"].find("PARTN").text for i in idocs] == ["NDC01", "NLAB02"]
    assert [i.find("E1EDP01/E1EDP20/EDATU").text for i in idocs] == ["20261105", "20261112"]
    assert [i.find("E1EDP01/MENGE").text for i in idocs] == ["2000", "5000"]
    assert [segments(i.find("E1EDP01"), "E1EDP19")["001"].find("IDTNR").text
            for i in idocs] == ["MN-SIM-0128", "MN-ESIM-A01"]


def test_every_date_is_yyyymmdd(tmp_path):
    dates = [el.text for i in export("850_multi.x12", tmp_path)
             for el in i.iter() if el.tag in ("CREDAT", "DATUM", "EDATU")]
    assert len(dates) == 8 and all(re.fullmatch(r"20\d{6}", d) for d in dates)


def test_sap_prints_a_summary(tmp_path, capsys):
    export("850_multi.x12", tmp_path)
    out = capsys.readouterr().out.splitlines()[-1]
    assert out.startswith("2 ORDERS05 IDoc(s) for PO PO45003, PO45004 to SAPCLNT100")


# --- missing data and bad input ----------------------------------------------------

def test_missing_data_is_left_out_not_invented(tmp_path):
    order = {"co_num": "EDIPO9", "cust_num": "NEWCUST", "cust_po": "PO9",
             "order_date": "2026-10-19", "due_date": None, "ship_to": "",
             "lines": [{"co_line": 1, "item": "ESIM-PROF-A", "qty_ordered": "5",
                        "u_m": "EA", "price": None}]}
    text = idoc.make_idoc_xml([order], ControlNumbers(tmp_path / "c.json"), when=WHEN)
    doc = ET.fromstring(text).find("IDOC")
    assert doc.find("EDI_DC40/CRETIM").text == "153005"
    assert doc.find("E1EDK01/CURCY") is None                  # the ERP has no currency
    assert list(segments(doc, "E1EDK03")) == ["012"]          # no due date
    assert list(segments(doc, "E1EDKA1")) == ["AG"]           # no ship-to
    assert doc.find("E1EDKA1/NAME1") is None                  # no partners table given
    line = doc.find("E1EDP01")
    assert line.find("VPREI") is None
    assert fields(line.find("E1EDP20")) == {"WMENG": "5"}
    assert list(segments(line, "E1EDP19")) == ["002"]         # no customer part number
    assert "<NAME1 />" not in text and "></" not in text      # no empty tags


@pytest.mark.parametrize("content", ["[]", "{}", '[{"co_num": "X"}]', "not json"])
def test_sap_rejects_files_that_are_not_erp_orders(tmp_path, capsys, content):
    bad = tmp_path / "erp_orders.json"
    bad.write_text(content)
    assert main(["sap", str(bad), "--out", str(tmp_path)]) == 1
    assert "error:" in capsys.readouterr().err
    assert not (tmp_path / "idoc_orders.xml").exists()


@pytest.mark.parametrize("option", [["--client", "1000"], ["--receiver", "SAPCLNT100X"]])
def test_sap_rejects_bad_control_values(tmp_path, option):
    erp = tmp_path / "erp_orders.json"
    erp.write_text(json.dumps([{"co_num": "X", "cust_num": "C", "cust_po": "P",
                                "order_date": "2026-10-19", "due_date": None,
                                "ship_to": "", "lines": []}]))
    assert main(["sap", str(erp), "--out", str(tmp_path), *option]) == 1
    assert not (tmp_path / "idoc_orders.xml").exists()


def test_sap_missing_file_is_an_error_not_a_traceback(tmp_path, capsys):
    assert main(["sap", str(tmp_path / "missing.json"), "--out", str(tmp_path)]) == 1
    assert "error:" in capsys.readouterr().err
