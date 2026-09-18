"""DynamoDB access. This module is the single source of truth for inventory, money and state.

Every write that moves stock or money goes through TransactWriteItems with condition
expressions, so a double-tap, a retry or two pickers on the same order can never oversell.
"""
from decimal import Decimal

import boto3
from boto3.dynamodb.types import TypeSerializer, TypeDeserializer
from botocore.exceptions import ClientError

import config
from errors import Conflict, NotFound, PolicyViolation

_serializer = TypeSerializer()
_deserializer = TypeDeserializer()

_resource = None
_client = None


def _res():
    global _resource
    if _resource is None:
        _resource = boto3.resource("dynamodb", region_name=config.AWS_REGION)
    return _resource


def _cli():
    global _client
    if _client is None:
        _client = boto3.client("dynamodb", region_name=config.AWS_REGION)
    return _client


def ser(value):
    return _serializer.serialize(value)


def deser(item):
    return {k: _deserializer.deserialize(v) for k, v in item.items()}


def to_decimal(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))



def list_orders(limit=100):
    resp = _res().Table(config.ORDERS_TABLE).scan(Limit=max(1, min(int(limit), 200)))
    return resp.get("Items", [])


def list_audit(limit=500):
    resp = _res().Table(config.AUDIT_TABLE).scan(Limit=max(1, min(int(limit), 1000)))
    return resp.get("Items", [])


def list_inventory(limit=200):
    resp = _res().Table(config.INVENTORY_TABLE).scan(Limit=max(1, min(int(limit), 500)))
    return resp.get("Items", [])


def reset_store():
    """Wipe and reseed demo state. Only the in-memory local adapter implements this.

    Against real DynamoDB this is refused on purpose: a one-click truncate of the live
    inventory and order tables is not something an API should offer.
    """
    raise PolicyViolation(
        "Demo reset is only available on the local in-memory store, not against DynamoDB.",
        {"adapter": "dynamodb"},
    )

# --- Inventory ------------------------------------------------------------------------------
def get_inventory(sku_id):
    resp = _res().Table(config.INVENTORY_TABLE).get_item(Key={"sku_id": sku_id})
    return resp.get("Item")


def get_inventory_many(sku_ids):
    """BatchGetItem in chunks of 25. Returns {sku_id: record} — missing SKUs are simply absent."""
    out = {}
    unique = list(dict.fromkeys(sku_ids))
    for i in range(0, len(unique), 25):
        chunk = unique[i : i + 25]
        resp = _res().batch_get_item(
            RequestItems={config.INVENTORY_TABLE: {"Keys": [{"sku_id": s} for s in chunk]}}
        )
        for rec in resp.get("Responses", {}).get(config.INVENTORY_TABLE, []):
            out[rec["sku_id"]] = rec
    return out


def put_inventory(record):
    _res().Table(config.INVENTORY_TABLE).put_item(Item=record)
    return record


# --- Orders ---------------------------------------------------------------------------------
def get_order(order_id):
    resp = _res().Table(config.ORDERS_TABLE).get_item(Key={"order_id": order_id})
    item = resp.get("Item")
    if not item:
        raise NotFound("Order %s does not exist." % order_id, {"order_id": order_id})
    return item


def put_order(order):
    _res().Table(config.ORDERS_TABLE).put_item(
        Item=order, ConditionExpression="attribute_not_exists(order_id)"
    )
    return order


def put_audit(entry):
    _res().Table(config.AUDIT_TABLE).put_item(Item=entry)
    return entry


def set_order_status(order_id, expected_status, new_status, extra=None):
    """Guarded status move. Fails loudly if someone else already moved the order."""
    names = {"#s": "status"}
    values = {":new": ser(new_status), ":cur": ser(expected_status)}
    sets = ["#s = :new", "updated_at = :ts"]
    values[":ts"] = ser(_now_ms())
    for i, (key, val) in enumerate((extra or {}).items()):
        names["#e%d" % i] = key
        values[":e%d" % i] = ser(val)
        sets.append("#e%d = :e%d" % (i, i))
    try:
        resp = _cli().update_item(
            TableName=config.ORDERS_TABLE,
            Key={"order_id": ser(order_id)},
            UpdateExpression="SET " + ", ".join(sets),
            ConditionExpression="#s = :cur",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise Conflict("Order moved on before this request landed. Reload the order.")
        raise
    return deser(resp["Attributes"])


def commit_pick(order_id, index, sku_id, qty, item_state, audit_entry):
    """Atomically: decrement stock, mark the line picked, write the audit row."""
    ops = [
        {
            "Update": {
                "TableName": config.INVENTORY_TABLE,
                "Key": {"sku_id": ser(sku_id)},
                "UpdateExpression": "SET stock_qty = stock_qty - :q",
                "ConditionExpression": "attribute_exists(sku_id) AND stock_qty >= :q",
                "ExpressionAttributeValues": {":q": ser(qty)},
            }
        },
        {
            "Update": {
                "TableName": config.ORDERS_TABLE,
                "Key": {"order_id": ser(order_id)},
                "UpdateExpression": "SET #items[%d].#st = :new, updated_at = :ts" % index,
                "ConditionExpression": "#items[%d].#st <> :new AND #items[%d].sku_id = :sku" % (index, index),
                "ExpressionAttributeNames": {"#items": "items", "#st": "state"},
                "ExpressionAttributeValues": {
                    ":new": ser(item_state),
                    ":sku": ser(sku_id),
                    ":ts": ser(_now_ms()),
                },
            }
        },
        {"Put": {"TableName": config.AUDIT_TABLE, "Item": {k: ser(v) for k, v in audit_entry.items()}}},
    ]
    _transact(ops, "Pick could not be committed — stock or line state changed.")


def commit_substitution(order_id, index, original, substitute, qty, invoice_delta, audit_entry):
    """Atomically: reserve substitute stock, rewrite the line, adjust the invoice, audit it.

    Either all four land or none do. There is no window where the bag and the bill disagree.
    """
    ops = [
        {
            "Update": {
                "TableName": config.INVENTORY_TABLE,
                "Key": {"sku_id": ser(substitute["sku_id"])},
                "UpdateExpression": "SET stock_qty = stock_qty - :q",
                "ConditionExpression": "attribute_exists(sku_id) AND stock_qty >= :q",
                "ExpressionAttributeValues": {":q": ser(qty)},
            }
        },
        {
            "Update": {
                "TableName": config.ORDERS_TABLE,
                "Key": {"order_id": ser(order_id)},
                "UpdateExpression": (
                    "SET #items[{i}].sku_id = :sub, #items[{i}].#nm = :name, "
                    "#items[{i}].unit_price = :price, #items[{i}].#st = :state, "
                    "#items[{i}].substituted_from = :orig, #items[{i}].aisle = :aisle, "
                    "#items[{i}].shelf = :shelf, invoice_total = invoice_total + :delta, "
                    "updated_at = :ts"
                ).format(i=index),
                "ConditionExpression": "#items[{i}].sku_id = :orig AND #s = :picking".format(i=index),
                "ExpressionAttributeNames": {
                    "#items": "items",
                    "#st": "state",
                    "#nm": "name",
                    "#s": "status",
                },
                "ExpressionAttributeValues": {
                    ":sub": ser(substitute["sku_id"]),
                    ":name": ser(substitute.get("name", substitute["sku_id"])),
                    ":price": ser(to_decimal(substitute.get("price", 0))),
                    ":state": ser("SUBSTITUTED"),
                    ":orig": ser(original["sku_id"]),
                    ":aisle": ser(substitute.get("aisle")),
                    ":shelf": ser(substitute.get("shelf")),
                    ":delta": ser(to_decimal(invoice_delta)),
                    ":picking": ser("PICKING"),
                    ":ts": ser(_now_ms()),
                },
            }
        },
        {"Put": {"TableName": config.AUDIT_TABLE, "Item": {k: ser(v) for k, v in audit_entry.items()}}},
    ]
    _transact(ops, "Substitute was taken by another picker. Pick a different one.")


def mark_item_state(order_id, index, sku_id, new_state, audit_entry=None):
    """Non-financial line state change (AVAILABLE -> OOS, -> EXCEPTION)."""
    ops = [
        {
            "Update": {
                "TableName": config.ORDERS_TABLE,
                "Key": {"order_id": ser(order_id)},
                "UpdateExpression": "SET #items[%d].#st = :new, updated_at = :ts" % index,
                "ConditionExpression": "#items[%d].sku_id = :sku" % index,
                "ExpressionAttributeNames": {"#items": "items", "#st": "state"},
                "ExpressionAttributeValues": {
                    ":new": ser(new_state),
                    ":sku": ser(sku_id),
                    ":ts": ser(_now_ms()),
                },
            }
        }
    ]
    if audit_entry:
        ops.append(
            {"Put": {"TableName": config.AUDIT_TABLE, "Item": {k: ser(v) for k, v in audit_entry.items()}}}
        )
    _transact(ops, "Line state changed underneath this request. Reload the order.")


def _transact(ops, conflict_message):
    try:
        _cli().transact_write_items(TransactItems=ops)
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("TransactionCanceledException", "ConditionalCheckFailedException"):
            reasons = exc.response.get("CancellationReasons", [])
            raise Conflict(conflict_message, {"reasons": [r.get("Code") for r in reasons]})
        raise


def _now_ms():
    import time

    return int(time.time() * 1000)
