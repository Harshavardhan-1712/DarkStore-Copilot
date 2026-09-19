"""Phantom-stock loop: every out-of-stock report becomes a free inventory audit.

When a picker says the shelf is empty, the system usually already "knows" the answer: either
stock was 0 (a known stock-out) or it said N > 0 and the shelf disagreed. The second case is
phantom stock, and it is exactly what a cycle count exists to find. Each report is captured as a
signal, and repeated signals on one SKU rise to the top of a recount queue.

Additive by design. This module only READS inventory and orders. Signals live in their own
store (in memory locally, an optional DynamoDB table in AWS), so no existing record, table or
audit stream is written, and nothing here adjusts stock. A human recounts, then corrects stock
through the normal inventory process.
"""
import os
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import dynamo

FINDING_PHANTOM = "PHANTOM_STOCK_SUSPECT"   # system shows stock, shelf is empty
FINDING_KNOWN = "KNOWN_OUT_OF_STOCK"        # system already showed zero
SOURCES = ("voice", "button", "batch")
RECOUNT_AFTER = 2       # reports (or distinct pickers) on one SKU before it is "recount now"
MAX_LINES = 20


class SignalError(Exception):
    """Base for errors that carry an HTTP status."""

    status = 400


class SkuNotFound(SignalError):
    status = 404


class LinesMoved(SignalError):
    status = 409


def _num(value):
    """JSON-friendly number from Decimal / int / float / str."""
    d = value if isinstance(value, Decimal) else Decimal(str(value if value is not None else 0))
    return int(d) if d == d.to_integral_value() else float(d)


def _plain(value):
    """Turn DynamoDB Decimals back into ints/floats, recursively."""
    if isinstance(value, Decimal):
        return _num(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- storage -----------------------------------------------------------------------------------
class MemoryStore:
    """Default. Lives as long as the process; fine locally, not durable across Lambda instances."""

    durable = False

    def __init__(self):
        self._items = []
        self._lock = threading.Lock()

    def add(self, signal):
        with self._lock:
            self._items.append(dict(signal))

    def all(self):
        with self._lock:
            return [dict(s) for s in self._items]

    def clear(self):
        with self._lock:
            del self._items[:]


class DynamoStore:
    """Optional. A dedicated table (partition key `signal_id`), separate from every existing table."""

    durable = True

    def __init__(self, table):
        self._table = table

    def add(self, signal):
        import json

        # DynamoDB rejects Python floats, so numbers go in as Decimal.
        self._table.put_item(Item=json.loads(json.dumps(signal), parse_float=Decimal))

    def all(self):
        items, kwargs = [], {}
        while True:
            page = self._table.scan(**kwargs)
            items.extend(_plain(i) for i in page.get("Items", []))
            if not page.get("LastEvaluatedKey"):
                return items
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def clear(self):
        raise SignalError("Clearing is only available for the in-memory store.")


_store = None


def get_store():
    global _store
    if _store is None:
        table_name = os.environ.get("PHANTOM_TABLE")
        if table_name:
            import boto3

            _store = DynamoStore(boto3.resource("dynamodb").Table(table_name))
        else:
            _store = MemoryStore()
    return _store


def set_store(store):
    """Tests inject a store here."""
    global _store
    _store = store


def clear():
    """Demo reset. A no-op for a durable store, so a reset can never wipe production signals."""
    store = get_store()
    if not store.durable:
        store.clear()


# --- recording ---------------------------------------------------------------------------------
def _line_key(line):
    return "%s:%s" % (line["order_id"], line["index"])


def record_report(sku_id, lines, source="button", picker_id="", reason="", store=None):
    """Log one shelf-empty observation. Returns the finding; never changes inventory or orders.

    `lines` is every order line the picker just found unfillable at this shelf, so a batch pick
    counts as ONE observation, not one per order. A line already covered by an earlier signal
    (say, reported in a batch and again when the order is opened) is not counted twice.
    """
    store = store or get_store()
    sku_id = str(sku_id or "").strip()
    if not sku_id:
        raise SignalError("sku_id is required.")
    if not isinstance(lines, list) or not lines:
        raise SignalError("At least one order line is required.")
    if len(lines) > MAX_LINES:
        raise SignalError("Too many lines in one report.")
    if source not in SOURCES:
        raise SignalError("source must be one of: %s." % ", ".join(SOURCES))

    inventory = dynamo.get_inventory(sku_id)
    if not inventory:
        raise SkuNotFound("Unknown SKU %s." % sku_id)

    # Only lines that still point at this SKU count; a line that has already been substituted
    # is no longer evidence about this shelf.
    valid, orders = [], {}
    for line in lines:
        try:
            order_id, index = str(line["order_id"]), int(line["index"])
        except (KeyError, TypeError, ValueError):
            continue
        if order_id not in orders:
            orders[order_id] = dynamo.get_order(order_id)
        items = orders[order_id].get("items") or []
        if 0 <= index < len(items) and items[index].get("sku_id") == sku_id:
            valid.append({"order_id": order_id, "index": index})
    if not valid:
        raise LinesMoved("Those order lines no longer point at %s." % sku_id)

    keys = {_line_key(v) for v in valid}
    system_qty = Decimal(str(inventory.get("stock_qty", 0)))
    finding = FINDING_PHANTOM if system_qty > 0 else FINDING_KNOWN

    for prior in store.all():
        if prior.get("sku_id") == sku_id and keys & {_line_key(l) for l in prior.get("lines", [])}:
            return {"deduped": True, "finding": prior.get("finding"), "sku_id": sku_id,
                    "name": prior.get("name"), "system_qty": prior.get("system_qty")}

    signal = {
        "signal_id": uuid.uuid4().hex,
        "ts": _now(),
        "sku_id": sku_id,
        "name": inventory.get("name"),
        "system_qty": _num(system_qty),
        "price": _num(inventory.get("price", 0)),
        "reported_qty": 0,
        "finding": finding,
        "source": source,
        "picker_id": str(picker_id or ""),
        "reason": str(reason or "")[:200],
        "lines": valid,
    }
    store.add(signal)
    return {"deduped": False, "finding": finding, "sku_id": sku_id,
            "name": signal["name"], "system_qty": signal["system_qty"]}


# --- the recount queue -------------------------------------------------------------------------
def build_queue(store=None):
    """SKUs the books say are in stock but pickers found empty, most urgent first."""
    store = store or get_store()
    signals = store.all()

    by_sku = {}
    for s in signals:
        if s.get("finding") == FINDING_PHANTOM:
            by_sku.setdefault(s["sku_id"], []).append(s)

    rows = []
    for sku_id, group in by_sku.items():
        inventory = dynamo.get_inventory(sku_id) or {}
        current = Decimal(str(inventory.get("stock_qty", group[-1].get("system_qty", 0))))
        if current <= 0:
            continue  # the books already agree with the shelf: nothing left to audit
        pickers = {s.get("picker_id") for s in group if s.get("picker_id")}
        price = Decimal(str(inventory.get("price", group[-1].get("price", 0))))
        rows.append({
            "sku_id": sku_id,
            "name": inventory.get("name") or group[-1].get("name"),
            "aisle": inventory.get("aisle"),
            "shelf": inventory.get("shelf"),
            "reports": len(group),
            "pickers": len(pickers),
            "voice_reports": sum(1 for s in group if s.get("source") == "voice"),
            "last_reported": max(s.get("ts", "") for s in group),
            "system_qty": _num(current),
            "value_at_risk": _num(current * price),
            "status": "RECOUNT_NOW" if len(group) >= RECOUNT_AFTER or len(pickers) >= RECOUNT_AFTER else "WATCH",
        })

    rows.sort(key=lambda r: (r["status"] != "RECOUNT_NOW", -r["reports"], -r["value_at_risk"], r["sku_id"]))

    reports = sum(r["reports"] for r in rows)
    voice = sum(r["voice_reports"] for r in rows)
    return {
        "queue": rows,
        "summary": {
            "skus_flagged": len(rows),
            "recount_now": sum(1 for r in rows if r["status"] == "RECOUNT_NOW"),
            "reports": reports,
            "voice_reports": voice,
            "value_at_risk": _num(sum(Decimal(str(r["value_at_risk"])) for r in rows)),
            "known_stockouts": sum(1 for s in signals if s.get("finding") == FINDING_KNOWN),
        },
        "durable": store.durable,
    }
