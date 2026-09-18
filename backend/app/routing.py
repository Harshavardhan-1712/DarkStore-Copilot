"""Deterministic pick-path construction.

No AI touches this. Given the store layout attributes on each SKU (aisle, shelf) we produce a
stable serpentine walk: up odd aisles, down even ones, so the picker never backtracks.
"""
import re

_AISLE_RE = re.compile(r"^\s*([A-Za-z]*)\s*0*(\d+)\s*$")


def parse_aisle(aisle):
    """'A3' -> ('A', 3). Unparseable aisles sort last but stay stable."""
    if aisle is None:
        return ("~", 10**6)
    m = _AISLE_RE.match(str(aisle))
    if not m:
        return ("~" + str(aisle).upper(), 10**6)
    zone, number = m.groups()
    return (zone.upper(), int(number))


def parse_shelf(shelf):
    """Shelves are usually 'S2' / '2' / 'B12'. Returns an int used only for ordering."""
    if shelf is None:
        return 10**6
    m = re.search(r"(\d+)", str(shelf))
    return int(m.group(1)) if m else 10**6


def route_key(item):
    """Sort key for one order line. Serpentine: even-numbered aisles are walked in reverse."""
    zone, aisle_no = parse_aisle(item.get("aisle"))
    shelf_no = parse_shelf(item.get("shelf"))
    direction = 1 if aisle_no % 2 == 1 else -1
    return (zone, aisle_no, shelf_no * direction, str(item.get("sku_id", "")))


def optimize_route(order_items, inventory_by_sku):
    """Return order lines enriched with layout data, ordered as the picker should walk them.

    order_items: [{"sku_id": ..., "qty": ...}, ...]
    inventory_by_sku: {sku_id: inventory record}
    """
    enriched = []
    for line in order_items:
        sku = line["sku_id"]
        inv = inventory_by_sku.get(sku, {})
        enriched.append(
            {
                "sku_id": sku,
                "name": inv.get("name", sku),
                "category": inv.get("category"),
                "qty": int(line.get("qty", 1)),
                "aisle": inv.get("aisle"),
                "shelf": inv.get("shelf"),
                "unit_price": inv.get("price"),
                "state": line.get("state", "AVAILABLE"),
                "substituted_from": line.get("substituted_from"),
            }
        )
    enriched.sort(key=route_key)
    for position, line in enumerate(enriched):
        line["seq"] = position
    return enriched


def next_open_index(items):
    """Index of the first line still needing picker action, or None when the walk is done."""
    from state_machine import TERMINAL_ITEM_STATES

    for idx, line in enumerate(items):
        if line.get("state") not in TERMINAL_ITEM_STATES:
            return idx
    return None
