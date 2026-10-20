import csv
import json
from decimal import Decimal

from conftest import SAMPLES, parse_ok, read_sample
from edi_bridge import m850, mapping


def first_order(text: str) -> m850.Order:
    ic = parse_ok(text)
    return m850.map_850(ic.groups[0].transactions[0], ic)


def xref():
    return (mapping.load_partners(SAMPLES / "partners.csv"),
            mapping.load_items(SAMPLES / "items.csv"))


# --- 850 -> Order --------------------------------------------------------------

def test_850_header_fields(good):
    order = first_order(good)
    assert order.sender_id == "MNONORTH"
    assert order.control_number == "0001"
    assert (order.purpose, order.order_type, order.po_number) == ("00", "SA", "PO45001")
    assert order.po_date == "2026-10-19"
    assert order.requested_delivery == "2026-11-02"
    assert (order.ship_to_name, order.ship_to_id) == ("North Mobile DC", "NDC01")


def test_850_line_fields(good):
    lines = first_order(good).lines
    assert len(lines) == 2
    first, second = lines
    assert first.line_no == "1"
    assert first.quantity == Decimal("500") and first.uom == "EA"
    assert first.unit_price == Decimal("0.85")
    assert (first.vendor_part, first.buyer_part, first.upc) == ("UICC-TRIO-128K", "MN-SIM-0128", "")
    assert (second.vendor_part, second.quantity) == ("ESIM-PROF-A", Decimal("1000"))


def test_850_product_id_pairs_in_any_order(good):
    text = good.replace("PO1*1*500*EA*0.85**VP*UICC-TRIO-128K*BP*MN-SIM-0128",
                        "PO1*1*500*EA*0.85**UP*012345678905*BP*MN-SIM-0128*VP*UICC-TRIO-128K")
    line = first_order(text).lines[0]
    assert (line.upc, line.buyer_part, line.vendor_part) == (
        "012345678905", "MN-SIM-0128", "UICC-TRIO-128K")


def test_850_without_beg_does_not_crash():
    from edi_bridge import x12
    ic, _ = x12.read_x12(read_sample("850_missing_beg.x12"))
    order = m850.map_850(ic.groups[0].transactions[0], ic)
    assert order.po_number == "" and len(order.lines) == 2


def test_x12_date_and_decimal_helpers():
    assert m850.x12_date("20261019") == "2026-10-19"
    assert m850.x12_date("1019") == "1019"
    assert m850.to_decimal("0.40") == Decimal("0.40")
    assert m850.to_decimal("abc") is None and m850.to_decimal("NaN") is None


# --- Order -> ERP record -------------------------------------------------------

def test_maps_good_order_to_erp_record(good):
    record, errors = mapping.map_order(first_order(good), *xref())
    assert errors == []
    assert record.co_num == "EDIPO45001"
    assert (record.cust_num, record.cust_po) == ("MNO-NORTH", "PO45001")
    assert (record.order_date, record.due_date, record.ship_to) == ("2026-10-19", "2026-11-02", "NDC01")
    assert [(ln.co_line, ln.item, ln.qty_ordered, ln.u_m, ln.price) for ln in record.lines] == [
        (1, "UICC-TRIO-128K", Decimal("500"), "EA", Decimal("0.85")),
        (2, "ESIM-PROF-A", Decimal("1000"), "EA", Decimal("0.40")),
    ]


def test_unknown_item_is_a_mapping_error_not_a_crash():
    record, errors = mapping.map_order(first_order(read_sample("850_unknown_item.x12")), *xref())
    assert record is None
    assert [(e.code, e.line_no) for e in errors] == [("UNKNOWN_ITEM", "2")]
    assert "UICC-NANO-256K" in errors[0].message


def test_unknown_partner_is_a_mapping_error(good):
    order = first_order(good)
    order.sender_id = "NOBODY"
    record, errors = mapping.map_order(order, *xref())
    assert record is None
    assert [(e.code, e.line_no) for e in errors] == [("UNKNOWN_PARTNER", None)]


def test_vendor_part_is_used_when_buyer_part_is_unknown(good):
    order = first_order(good)
    order.lines[0].buyer_part = "NOT-IN-TABLE"
    record, errors = mapping.map_order(order, *xref())
    assert errors == [] and record.lines[0].item == "UICC-TRIO-128K"


def test_cross_reference_is_per_customer(good):
    # MNO-SOUTH's part number must not resolve for an MNO-NORTH order.
    order = first_order(good)
    order.lines[0].buyer_part, order.lines[0].vendor_part = "ST-USIM-128", ""
    _, errors = mapping.map_order(order, *xref())
    assert [e.code for e in errors] == ["UNKNOWN_ITEM"]


def test_co_num_is_ten_characters_at_most():
    assert mapping.make_co_num("PO-45001") == "EDIPO45001"
    assert len(mapping.make_co_num("VERY-LONG-PO-NUMBER-123")) == 10


def test_writes_erp_json_and_csv(good, tmp_path):
    record, _ = mapping.map_order(first_order(good), *xref())
    mapping.write_json([record], tmp_path / "erp.json")
    header_path, lines_path = mapping.write_erp_csv([record], tmp_path)

    data = json.loads((tmp_path / "erp.json").read_text())
    assert data[0]["co_num"] == "EDIPO45001"
    assert data[0]["lines"][0]["price"] == "0.85"  # Decimal kept exact, as text

    with open(header_path, newline="") as f:
        assert list(csv.DictReader(f)) == [{
            "co_num": "EDIPO45001", "cust_num": "MNO-NORTH", "cust_po": "PO45001",
            "order_date": "2026-10-19", "due_date": "2026-11-02", "ship_to": "NDC01"}]
    with open(lines_path, newline="") as f:
        rows = list(csv.DictReader(f))
    assert [(r["co_line"], r["item"], r["qty_ordered"], r["price"]) for r in rows] == [
        ("1", "UICC-TRIO-128K", "500", "0.85"), ("2", "ESIM-PROF-A", "1000", "0.40")]
