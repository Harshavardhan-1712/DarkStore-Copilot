from routing import optimize_route, parse_aisle, parse_shelf, next_open_index

INV = {
    "SKU_MILK": {"sku_id": "SKU_MILK", "name": "Amul Milk 500ml", "aisle": "A3", "shelf": "S2", "price": 32, "category": "dairy"},
    "SKU_RICE": {"sku_id": "SKU_RICE", "name": "Sona Masoori 5kg", "aisle": "A1", "shelf": "S4", "price": 410, "category": "staples"},
    "SKU_OIL":  {"sku_id": "SKU_OIL",  "name": "Sunflower Oil 1L", "aisle": "A2", "shelf": "S1", "price": 145, "category": "staples"},
    "SKU_CURD": {"sku_id": "SKU_CURD", "name": "Curd 400g", "aisle": "A3", "shelf": "S1", "price": 30, "category": "dairy"},
}


def test_parse_helpers():
    assert parse_aisle("A03") == ("A", 3)
    assert parse_aisle("12") == ("", 12)
    assert parse_shelf("S7") == 7
    assert parse_aisle(None)[1] == 10 ** 6


def test_route_is_serpentine_and_stable():
    items = [{"sku_id": s, "qty": 1} for s in ("SKU_MILK", "SKU_RICE", "SKU_OIL", "SKU_CURD")]
    route = [i["sku_id"] for i in optimize_route(items, INV)]
    # Odd aisles are walked shelf-ascending, even aisles shelf-descending: no backtracking.
    assert route == ["SKU_RICE", "SKU_OIL", "SKU_CURD", "SKU_MILK"]
    assert route == [i["sku_id"] for i in optimize_route(list(reversed(items)), INV)]


def test_route_enriches_and_sequences():
    out = optimize_route([{"sku_id": "SKU_MILK", "qty": 2}], INV)
    assert out[0]["aisle"] == "A3" and out[0]["qty"] == 2 and out[0]["seq"] == 0
    assert out[0]["state"] == "AVAILABLE"


def test_unknown_sku_sorts_last_without_crashing():
    out = optimize_route([{"sku_id": "GHOST", "qty": 1}, {"sku_id": "SKU_RICE", "qty": 1}], INV)
    assert out[-1]["sku_id"] == "GHOST"


def test_next_open_index_skips_terminal_states():
    items = [{"state": "PICKED"}, {"state": "SUBSTITUTED"}, {"state": "OOS"}, {"state": "AVAILABLE"}]
    assert next_open_index(items) == 2
    assert next_open_index([{"state": "PICKED"}]) is None
