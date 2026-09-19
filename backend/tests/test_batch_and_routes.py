"""Batch planner (read-only) and the additive route front door."""
import copy
import json
import sys
import types
from decimal import Decimal

import pytest

try:
    import dynamo
except Exception:
    dynamo = types.ModuleType("dynamo")
    sys.modules["dynamo"] = dynamo

try:
    import handler
except Exception:
    handler = types.ModuleType("handler")
    handler.lambda_handler = lambda event, context: {"statusCode": 200, "headers": {}, "body": "{}"}
    sys.modules["handler"] = handler

import batch_plan as bp
import extras
import phantom_stock as ps


def line(sku, name, aisle, shelf, qty=1, state="PENDING"):
    return {"sku_id": sku, "name": name, "aisle": aisle, "shelf": shelf, "qty": qty, "state": state}


MILK = ("SKU_MILK", "Amul Milk 500ml", "2", "B")
BREAD = ("SKU_BREAD", "Bread", "10", "A")
RICE = ("SKU_RICE", "Rice 1kg", "5", "A")
SOAP = ("SKU_SOAP", "Soap", "1", "C")


def make_orders():
    return {
        "ORD-1": {"order_id": "ORD-1", "status": "PICKING",
                  "items": [line(*MILK, qty=1), line(*RICE, qty=2), line(*BREAD)]},
        "ORD-2": {"order_id": "ORD-2", "status": "PICKING",
                  "items": [line(*BREAD), line(*MILK, qty=2)]},
        "ORD-3": {"order_id": "ORD-3", "status": "CREATED", "items": [line(*SOAP)]},   # shares nothing
        "ORD-4": {"order_id": "ORD-4", "status": "DISPATCHED", "items": [line(*MILK, state="PICKED")]},
    }


@pytest.fixture
def world(monkeypatch):
    orders = make_orders()
    inv = {
        "SKU_MILK": {"stock_qty": Decimal("2")},   # 3 needed across the batch: short
        "SKU_RICE": {"stock_qty": Decimal("9")},
        "SKU_BREAD": {"stock_qty": Decimal("9")},
        "SKU_SOAP": {"stock_qty": Decimal("9")},
    }
    monkeypatch.setattr(dynamo, "list_orders", lambda limit=100: copy.deepcopy(list(orders.values())), raising=False)
    monkeypatch.setattr(dynamo, "get_order", lambda oid: copy.deepcopy(orders[oid]), raising=False)
    monkeypatch.setattr(dynamo, "get_inventory_many",
                        lambda skus: {s: copy.deepcopy(inv[s]) for s in skus if s in inv}, raising=False)
    monkeypatch.setattr(dynamo, "get_inventory", lambda s: {"sku_id": s, "name": s, "stock_qty": Decimal("5"),
                                                            "price": Decimal("10")}, raising=False)
    monkeypatch.setattr(ps, "_store", ps.MemoryStore())
    return types.SimpleNamespace(orders=orders, inv=inv)


def test_lines_merge_into_one_stop_per_sku_in_walk_order(world):
    plan = bp.build_plan(["ORD-1", "ORD-2"])
    assert [s["sku_id"] for s in plan["stops"]] == ["SKU_MILK", "SKU_RICE", "SKU_BREAD"]   # aisle 2, 5, 10
    milk = plan["stops"][0]
    assert milk["total_qty"] == 3
    assert milk["allocations"] == [
        {"order_id": "ORD-1", "tote": "A", "index": 0, "qty": 1},
        {"order_id": "ORD-2", "tote": "B", "index": 1, "qty": 2},
    ]
    assert plan["totals"] == {"orders": 2, "lines": 5, "stops": 3, "units": 7, "visits_saved": 2}


def test_shortfall_against_system_stock_is_flagged(world):
    milk, rice, bread = bp.build_plan(["ORD-1", "ORD-2"])["stops"]
    assert milk["short"] is True and milk["stock_qty"] == 2
    assert rice["short"] is False


def test_finished_lines_and_closed_orders_are_left_out(world):
    world.orders["ORD-1"]["items"][0]["state"] = "PICKED"
    plan = bp.build_plan(["ORD-1", "ORD-2", "ORD-4"])
    assert [o["order_id"] for o in plan["orders"]] == ["ORD-1", "ORD-2"]
    milk = next(s for s in plan["stops"] if s["sku_id"] == "SKU_MILK")
    assert milk["total_qty"] == 2 and len(milk["allocations"]) == 1


def test_auto_selection_prefers_orders_that_share_skus_and_skips_closed_ones(world):
    plan = bp.build_plan(max_orders=2)
    assert [o["order_id"] for o in plan["orders"]] == ["ORD-1", "ORD-2"]   # ORD-3 shares nothing, ORD-4 is dispatched


def test_planning_changes_nothing(world):
    before = copy.deepcopy(world.orders)
    bp.build_plan()
    bp.build_plan(["ORD-1", "ORD-2"])
    assert world.orders == before


def test_natural_sort_puts_aisle_2_before_aisle_10():
    assert sorted(["10", "2", "A12", "A3", "1"], key=bp._natural) == ["1", "2", "10", "A3", "A12"]


def test_bad_input_is_rejected(world):
    with pytest.raises(bp.PlanError):
        bp.build_plan(max_orders="lots")
    with pytest.raises(bp.PlanError):
        bp.build_plan(["ORD-1"] * 7)


# --- routes ------------------------------------------------------------------------------------
def call(method, path, body=None):
    event = {"httpMethod": method, "path": path, "body": json.dumps(body) if body is not None else None}
    res = extras.lambda_handler(event, None)
    return res["statusCode"], (json.loads(res["body"]) if res["body"] else None), res["headers"]


def test_other_paths_pass_straight_through_to_the_existing_handler(monkeypatch, world):
    seen = []
    monkeypatch.setattr(handler, "lambda_handler",
                        lambda event, ctx: seen.append(event["path"]) or {"statusCode": 204, "headers": {}, "body": ""})
    res = extras.lambda_handler({"httpMethod": "POST", "path": "/orders/ORD-1/pick", "body": "{}"}, None)
    assert res["statusCode"] == 204 and seen == ["/orders/ORD-1/pick"]


def test_report_then_queue_round_trip(world):
    status, body, headers = call("POST", "/phantom-stock/report", {
        "sku_id": "SKU_MILK", "lines": [{"order_id": "ORD-1", "index": 0}], "source": "voice", "picker_id": "P1"})
    assert status == 200 and body["finding"] == ps.FINDING_PHANTOM
    assert headers["Access-Control-Allow-Origin"] == "*"

    status, body, _ = call("GET", "/phantom-stock")
    assert status == 200 and body["summary"]["voice_reports"] == 1


def test_errors_map_to_http_statuses(world):
    assert call("POST", "/phantom-stock/report", {"sku_id": "", "lines": []})[0] == 400
    assert call("POST", "/phantom-stock/report", {"sku_id": "SKU_MILK", "lines": [{"order_id": "ORD-1", "index": 9}]})[0] == 409
    assert call("POST", "/phantom-stock/report", None)[0] == 400   # no body: sku_id is required
    assert call("GET", "/phantom-stock/nope")[0] == 404


def test_batch_plan_route_and_preflight(world):
    status, body, _ = call("POST", "/batches/plan", {"order_ids": ["ORD-1", "ORD-2"]})
    assert status == 200 and body["totals"]["stops"] == 3
    status, body, headers = call("OPTIONS", "/batches/plan")
    assert status == 204 and "POST" in headers["Access-Control-Allow-Methods"]


def test_reset_only_for_the_in_memory_store(world, monkeypatch):
    call("POST", "/phantom-stock/report", {"sku_id": "SKU_MILK", "lines": [{"order_id": "ORD-1", "index": 0}]})
    assert call("POST", "/phantom-stock/reset")[0] == 200
    assert call("GET", "/phantom-stock")[1]["summary"]["reports"] == 0

    class Durable(ps.MemoryStore):
        durable = True

    monkeypatch.setattr(ps, "_store", Durable())
    assert call("POST", "/phantom-stock/reset")[0] == 400
