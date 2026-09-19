"""Phantom-stock loop: OOS reports become audit signals and never touch existing data."""
import copy
import sys
import types
from decimal import Decimal

import pytest

try:
    import dynamo
except Exception:  # the real adapter is not importable here: a stand-in with the same call names
    dynamo = types.ModuleType("dynamo")
    sys.modules["dynamo"] = dynamo

import phantom_stock as ps


def _inventory():
    return {
        "SKU_MILK": {"sku_id": "SKU_MILK", "name": "Amul Milk 500ml", "aisle": "2", "shelf": "B",
                     "stock_qty": Decimal("14"), "price": Decimal("30")},
        "SKU_RICE": {"sku_id": "SKU_RICE", "name": "Sona Rice 1kg", "aisle": "5", "shelf": "A",
                     "stock_qty": Decimal("6"), "price": Decimal("62.50")},
        "SKU_ZERO": {"sku_id": "SKU_ZERO", "name": "Curd 400g", "aisle": "2", "shelf": "C",
                     "stock_qty": Decimal("0"), "price": Decimal("40")},
    }


def _orders():
    def line(sku):
        return {"sku_id": sku, "name": sku, "qty": 1, "state": "PENDING"}

    return {
        "ORD-1": {"order_id": "ORD-1", "status": "PICKING", "items": [line("SKU_MILK"), line("SKU_RICE")]},
        "ORD-2": {"order_id": "ORD-2", "status": "PICKING", "items": [line("SKU_MILK"), line("SKU_ZERO")]},
    }


@pytest.fixture
def world(monkeypatch):
    inv, orders = _inventory(), _orders()

    def get_order(order_id):
        if order_id not in orders:
            raise LookupError(order_id)
        return copy.deepcopy(orders[order_id])

    monkeypatch.setattr(dynamo, "get_inventory", lambda s: copy.deepcopy(inv.get(s)), raising=False)
    monkeypatch.setattr(dynamo, "get_order", get_order, raising=False)
    store = ps.MemoryStore()
    return types.SimpleNamespace(inv=inv, orders=orders, store=store)


def report(world, sku="SKU_MILK", lines=None, **kw):
    lines = [{"order_id": "ORD-1", "index": 0}] if lines is None else lines
    return ps.record_report(sku, lines, store=world.store, **kw)


def test_empty_shelf_with_stock_on_the_books_is_a_phantom_signal(world):
    out = report(world, source="voice", picker_id="P1")
    assert out["finding"] == ps.FINDING_PHANTOM
    assert out["system_qty"] == 14
    assert out["deduped"] is False
    [signal] = world.store.all()
    assert signal["source"] == "voice" and signal["reported_qty"] == 0


def test_reports_never_change_inventory_or_orders(world):
    inv_before, orders_before = copy.deepcopy(world.inv), copy.deepcopy(world.orders)
    report(world, source="voice")
    report(world, sku="SKU_ZERO", lines=[{"order_id": "ORD-2", "index": 1}], source="button")
    ps.build_queue(store=world.store)
    assert world.inv == inv_before
    assert world.orders == orders_before


def test_zero_on_the_books_is_a_known_stockout_not_phantom(world):
    out = report(world, sku="SKU_ZERO", lines=[{"order_id": "ORD-2", "index": 1}])
    assert out["finding"] == ps.FINDING_KNOWN
    queue = ps.build_queue(store=world.store)
    assert queue["queue"] == []
    assert queue["summary"]["known_stockouts"] == 1


def test_a_line_is_counted_once_even_if_reported_twice(world):
    first = report(world, lines=[{"order_id": "ORD-1", "index": 0}, {"order_id": "ORD-2", "index": 0}], source="batch")
    # Later the picker opens ORD-2 on its own and taps "missing" for the same line.
    again = report(world, lines=[{"order_id": "ORD-2", "index": 0}], source="button")
    assert first["deduped"] is False and again["deduped"] is True
    assert len(world.store.all()) == 1


def test_batch_report_is_one_observation_not_one_per_order(world):
    report(world, lines=[{"order_id": "ORD-1", "index": 0}, {"order_id": "ORD-2", "index": 0}], source="batch")
    [row] = ps.build_queue(store=world.store)["queue"]
    assert row["reports"] == 1 and row["status"] == "WATCH"


def test_lines_that_no_longer_match_the_sku_are_ignored(world):
    world.orders["ORD-2"]["items"][0]["sku_id"] = "SKU_OTHER"   # already substituted
    report(world, lines=[{"order_id": "ORD-1", "index": 0}, {"order_id": "ORD-2", "index": 0}])
    [signal] = world.store.all()
    assert signal["lines"] == [{"order_id": "ORD-1", "index": 0}]

    with pytest.raises(ps.LinesMoved):
        report(world, lines=[{"order_id": "ORD-2", "index": 0}])


def test_input_validation(world):
    for bad in (dict(sku="", lines=[{"order_id": "ORD-1", "index": 0}]),
                dict(lines=[]),
                dict(lines=[{"order_id": "ORD-1", "index": 0}], source="telepathy")):
        with pytest.raises(ps.SignalError):
            report(world, **bad)
    with pytest.raises(ps.SkuNotFound):
        report(world, sku="SKU_NOPE")
    with pytest.raises(ps.SignalError):
        report(world, lines=[{"order_id": "ORD-1", "index": 0}] * (ps.MAX_LINES + 1))


def test_queue_escalates_on_repeat_reports_and_ranks_by_urgency(world):
    report(world, sku="SKU_RICE", lines=[{"order_id": "ORD-1", "index": 1}], source="button", picker_id="P1")
    report(world, lines=[{"order_id": "ORD-1", "index": 0}], source="voice", picker_id="P1")
    report(world, lines=[{"order_id": "ORD-2", "index": 0}], source="voice", picker_id="P2")

    queue = ps.build_queue(store=world.store)
    milk, rice = queue["queue"]
    assert milk["sku_id"] == "SKU_MILK" and milk["status"] == "RECOUNT_NOW"
    assert milk["reports"] == 2 and milk["pickers"] == 2 and milk["voice_reports"] == 2
    assert milk["value_at_risk"] == 14 * 30
    assert rice["status"] == "WATCH" and rice["value_at_risk"] == 6 * 62.5

    s = queue["summary"]
    assert (s["skus_flagged"], s["recount_now"], s["reports"], s["voice_reports"]) == (2, 1, 3, 2)
    assert s["value_at_risk"] == 14 * 30 + 6 * 62.5


def test_sku_drops_off_the_queue_once_the_books_agree_with_the_shelf(world):
    report(world, source="voice")
    world.inv["SKU_MILK"]["stock_qty"] = Decimal("0")   # someone recounted and corrected stock
    assert ps.build_queue(store=world.store)["queue"] == []


def test_clear_is_a_noop_for_a_durable_store(monkeypatch):
    class Durable(ps.MemoryStore):
        durable = True

    store = Durable()
    store.add({"sku_id": "X"})
    monkeypatch.setattr(ps, "_store", store)
    ps.clear()
    assert len(store.all()) == 1


class FakeTable:
    def __init__(self):
        self.items = []

    def put_item(self, Item):
        assert not any(isinstance(v, float) for v in Item.values()), "DynamoDB rejects floats"
        self.items.append(Item)

    def scan(self, **kwargs):
        start = kwargs.get("ExclusiveStartKey", {}).get("i", 0)
        page = self.items[start:start + 1]
        out = {"Items": page}
        if start + 1 < len(self.items):
            out["LastEvaluatedKey"] = {"i": start + 1}
        return out


def test_dynamo_store_writes_decimals_and_reads_plain_numbers_across_pages(world):
    store = ps.DynamoStore(FakeTable())
    ps.record_report("SKU_RICE", [{"order_id": "ORD-1", "index": 1}], store=store)
    ps.record_report("SKU_MILK", [{"order_id": "ORD-1", "index": 0}], store=store)
    rows = store.all()
    assert len(rows) == 2
    assert rows[0]["price"] == 62.5 and rows[1]["system_qty"] == 14
    assert all(not isinstance(v, Decimal) for r in rows for v in r.values())
