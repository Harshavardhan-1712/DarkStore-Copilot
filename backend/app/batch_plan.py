"""Batch picking: one walk through the store for several orders.

Read-only. It merges the open lines of a few orders into one route (one stop per SKU, ordered by
aisle and shelf) and says how many units go into which order's tote. Recording a pick stays
exactly what it is today: one pick per order line through the existing pick action. So no order
or inventory record is created or changed here, and the plan is recomputed from live order
state every time it is asked for.
"""
import re
from decimal import Decimal

import dynamo

OPEN_STATUSES = ("CREATED", "PICKING")
# Lines in these states are finished; anything else on an open order still needs picking.
CLOSED_LINE_STATES = ("PICKED", "SUBSTITUTED", "REMOVED")
DEFAULT_MAX_ORDERS = 4
HARD_MAX_ORDERS = 6
TOTES = "ABCDEFGH"


class PlanError(Exception):
    status = 400


def _natural(value):
    """Sort key so aisle "2" comes before "10" and "A3" before "A12"."""
    parts = [p for p in re.split(r"(\d+)", str(value if value is not None else "")) if p != ""]
    return [(0, int(p), "") if p.isdigit() else (1, 0, p.lower()) for p in parts]


def _qty(line):
    d = Decimal(str(line.get("qty", 1)))
    return int(d) if d == d.to_integral_value() else float(d)


def _open_lines(order):
    return [
        (index, line)
        for index, line in enumerate(order.get("items") or [])
        if line.get("state") not in CLOSED_LINE_STATES
    ]


def _select_orders(max_orders):
    """Open orders that share the most SKUs with each other, so the walk actually saves steps."""
    open_orders = [o for o in dynamo.list_orders(100) if o.get("status") in OPEN_STATUSES and _open_lines(o)]
    skus = [{l.get("sku_id") for _, l in _open_lines(o)} for o in open_orders]

    def overlap(i):
        others = set().union(*(s for j, s in enumerate(skus) if j != i)) if len(skus) > 1 else set()
        return len(skus[i] & others)

    ranked = sorted(range(len(open_orders)), key=lambda i: (-overlap(i), i))[:max_orders]
    return [open_orders[i] for i in sorted(ranked)]   # keep shift order for stable tote letters


def build_plan(order_ids=None, max_orders=DEFAULT_MAX_ORDERS):
    try:
        max_orders = max(1, min(int(max_orders), HARD_MAX_ORDERS))
    except (TypeError, ValueError):
        raise PlanError("max_orders must be a number.")

    if order_ids:
        if not isinstance(order_ids, list) or len(order_ids) > HARD_MAX_ORDERS:
            raise PlanError("order_ids must be a list of at most %d orders." % HARD_MAX_ORDERS)
        orders = [dynamo.get_order(str(oid)) for oid in order_ids]
        orders = [o for o in orders if o.get("status") in OPEN_STATUSES]
    else:
        orders = _select_orders(max_orders)

    tote_of = {o["order_id"]: TOTES[i] for i, o in enumerate(orders)}
    stops = {}
    open_counts = {o["order_id"]: 0 for o in orders}
    for order in orders:
        for index, line in _open_lines(order):
            open_counts[order["order_id"]] += 1
            stop = stops.setdefault(line["sku_id"], {
                "sku_id": line["sku_id"],
                "name": line.get("name"),
                "aisle": line.get("aisle"),
                "shelf": line.get("shelf"),
                "total_qty": 0,
                "allocations": [],
            })
            qty = _qty(line)
            stop["total_qty"] += qty
            stop["allocations"].append({
                "order_id": order["order_id"], "tote": tote_of[order["order_id"]], "index": index, "qty": qty,
            })

    stock = dynamo.get_inventory_many(list(stops)) if stops else {}
    for sku_id, stop in stops.items():
        record = stock.get(sku_id)
        stop["stock_qty"] = None if record is None else _qty({"qty": record.get("stock_qty", 0)})
        stop["short"] = stop["stock_qty"] is not None and stop["stock_qty"] < stop["total_qty"]

    ordered = sorted(stops.values(), key=lambda s: (_natural(s["aisle"]), _natural(s["shelf"]), str(s["name"]).lower()))
    lines = sum(len(s["allocations"]) for s in ordered)
    return {
        "orders": [
            {"order_id": o["order_id"], "tote": tote_of[o["order_id"]], "open_lines": open_counts[o["order_id"]]}
            for o in orders
        ],
        "stops": ordered,
        "totals": {
            "orders": len(orders),
            "lines": lines,
            "stops": len(ordered),
            "units": sum(s["total_qty"] for s in ordered),
            # Picking each order on its own visits a shelf once per line; a batch once per SKU.
            "visits_saved": lines - len(ordered),
        },
    }
