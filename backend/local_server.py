"""Run the whole flow on a laptop with no AWS account.

    python backend/local_server.py        # http://localhost:8080

Same handler, same guardrails, same state machine. Only two things are swapped out:
DynamoDB becomes an in-memory dict, and Bedrock becomes a keyword matcher that emits the exact
same JSON contract — so the guardrail code is still doing real work, not being bypassed.
"""
import copy
import json
import os
import re
import sys
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "app"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "infra"))

import bedrock  # noqa: E402
import voice_lexicon  # noqa: E402
import dynamo  # noqa: E402
import handler  # noqa: E402
import extras  # noqa: E402
import phantom_stock  # noqa: E402
from errors import Conflict, NotFound  # noqa: E402
from seed_data import CATALOG  # noqa: E402

def _fresh_inventory():
    return {
        sku: {
            "sku_id": sku, "name": name, "category": cat, "aisle": aisle, "shelf": shelf,
            "stock_qty": Decimal(stock), "price": Decimal(price), "substitution_skus": subs,
            "active": True,
        }
        for sku, name, cat, aisle, shelf, stock, price, subs in CATALOG
    }


INV = _fresh_inventory()
ORDERS = {}
AUDIT = []
QUIET = False   # suppressed while the demo state is being seeded, so the log stays readable


# --- in-memory DynamoDB ---------------------------------------------------------------------
def _get_inventory(sku_id):
    return copy.deepcopy(INV.get(sku_id))


def _get_inventory_many(sku_ids):
    return {s: copy.deepcopy(INV[s]) for s in sku_ids if s in INV}


def _get_order(order_id):
    if order_id not in ORDERS:
        raise NotFound("Order %s does not exist." % order_id)
    return copy.deepcopy(ORDERS[order_id])


def _put_order(order):
    # The in-memory adapter allows overwriting an existing order id; that is how
    # POST /demo/scenario rebuilds a single scenario from scratch. Real DynamoDB keeps its
    # attribute_not_exists condition, so this looseness never reaches production.
    ORDERS[order["order_id"]] = copy.deepcopy(order)
    return order


def _put_audit(entry):
    AUDIT.append(entry)
    if not QUIET:
        print("  audit: %s %s" % (entry["event"], json.dumps(entry["detail"], default=str)[:140]))
    return entry


def _list_inventory(limit=200):
    return [copy.deepcopy(v) for v in list(INV.values())[:limit]]


def _reset_store():
    """Wipe in-memory state and reseed the catalog. Only the local adapter supports this."""
    INV.clear()
    INV.update(_fresh_inventory())
    ORDERS.clear()
    del AUDIT[:]
    phantom_stock.clear()   # demo reset also empties the phantom-stock signals (in-memory store)
    _verify.calls = 0


def _list_orders(limit=100):
    return [copy.deepcopy(v) for v in list(ORDERS.values())[:limit]]

def _list_audit(limit=500):
    return [copy.deepcopy(v) for v in AUDIT[-limit:]]


def _set_order_status(order_id, expected, new, extra=None):
    order = ORDERS[order_id]
    if order["status"] != expected:
        raise Conflict("Order moved on before this request landed. Reload the order.")
    order["status"] = new
    order.update(extra or {})
    return copy.deepcopy(order)


def _take_stock(sku_id, qty):
    if INV[sku_id]["stock_qty"] < qty:
        raise Conflict("Stock ran out for %s." % sku_id)
    INV[sku_id]["stock_qty"] -= qty


def _commit_pick(order_id, index, sku_id, qty, item_state, entry):
    _take_stock(sku_id, Decimal(qty))
    ORDERS[order_id]["items"][index]["state"] = item_state
    _put_audit(entry)


def _commit_substitution(order_id, index, original, substitute, qty, delta, entry):
    _take_stock(substitute["sku_id"], Decimal(qty))
    line = ORDERS[order_id]["items"][index]
    line.update({
        "sku_id": substitute["sku_id"], "name": substitute["name"], "unit_price": substitute["price"],
        "state": "SUBSTITUTED", "substituted_from": original["sku_id"],
        "aisle": substitute["aisle"], "shelf": substitute["shelf"],
    })
    ORDERS[order_id]["invoice_total"] += Decimal(delta)
    _put_audit(entry)


def _mark_item_state(order_id, index, sku_id, new_state, entry=None):
    ORDERS[order_id]["items"][index]["state"] = new_state
    if entry:
        _put_audit(entry)


for name, fn in [
    ("get_inventory", _get_inventory), ("get_inventory_many", _get_inventory_many),
    ("get_order", _get_order), ("put_order", _put_order), ("put_audit", _put_audit),
    ("list_orders", _list_orders), ("list_audit", _list_audit),
    ("list_inventory", _list_inventory), ("reset_store", _reset_store),
    ("set_order_status", _set_order_status), ("commit_pick", _commit_pick),
    ("commit_substitution", _commit_substitution), ("mark_item_state", _mark_item_state),
]:
    setattr(dynamo, name, fn)


# --- stand-in for Bedrock -------------------------------------------------------------------
# handler.py resolves obvious multilingual commands itself via voice_lexicon, so anything that
# reaches here is the ambiguous tail Bedrock would normally handle. This stand-in applies one
# extra layer of loose matching and otherwise declines, which is exactly what a real model
# should do when it is unsure: FLAG_EXCEPTION rather than a guess.
_LOOSE_TAKEN = re.compile(r"\b(gone|not\s+here\s+anymore|picked\s+by|took\s+the|missing\s+from\s+my)\b", re.I)
_LOOSE_MISSING = re.compile(r"\b(empty|finish(ed)?|over|nothing|can.?t\s+find|cannot\s+find|dorakatledu)\b", re.I)
_LOOSE_CONFIRM = re.compile(r"\b(bag\s*lo|in\s+my\s+hand|added|put\s+it\s+in|scanned)\b", re.I)


def _fake_intent(utterance, line, subs, lang_hint="te"):
    if _LOOSE_TAKEN.search(utterance):
        return voice_lexicon.build_intent(
            voice_lexicon.ITEM_ALREADY_TAKEN, line, None, "local model: concurrent pick phrasing")
    if _LOOSE_MISSING.search(utterance) and subs:
        return voice_lexicon.build_intent(
            voice_lexicon.SUBSTITUTE_ITEM, line, subs[0], "local model: unavailability phrasing")
    if _LOOSE_CONFIRM.search(utterance):
        return voice_lexicon.build_intent(
            voice_lexicon.CONFIRM_PICK, line, None, "local model: confirmation phrasing")
    return voice_lexicon.build_intent(
        voice_lexicon.FLAG_EXCEPTION, line, None, "local model was not confident")


def _interpret(utterance, line, subs, lang_hint="te"):
    import contracts
    allowed = {line["sku_id"]} | {s["sku_id"] for s in subs}
    return contracts.validate_intent(_fake_intent(utterance, line, subs, lang_hint), allowed)


def _verify_product(image_bytes, image_format, expected):
    # Local demo adapter: proves the upload, contract, audit and UI path without pretending
    # that a local keyword server is doing computer vision. AWS deployment swaps in Bedrock.
    return {"verdict": "MATCH", "expected_sku": expected.get("sku_id"),
            "detected_product": expected.get("name", "Expected product"), "confidence": 0.94,
            "notes": "Local demo visual check received the image; AWS Bedrock performs the real visual comparison after deployment.",
            "provider": "local-demo"}


def _verify(image_bytes, image_format, manifest, context=None):
    """Deterministic fail-then-pass per order, so the retry path is reproducible on stage.

    Keyed on the attempt number the handler supplies rather than a global counter, so seeding
    nine demo orders and then running the demo by hand both behave the same way.
    """
    attempt = int((context or {}).get("attempt", 1))
    if attempt <= 1:
        missing = manifest[-1]
        return {
            "verdict": "FAILED", "missing_skus": [missing["sku_id"]], "confidence": 0.81,
            "notes": "%s is not visible in the photo." % missing.get("name", missing["sku_id"]),
            "spoken_response_telugu": "", "spoken_response_hindi": "",
        }
    return {"verdict": "VERIFIED", "missing_skus": [], "confidence": 0.93,
            "notes": "All items visible and counts match.",
            "spoken_response_telugu": "", "spoken_response_hindi": ""}


bedrock.interpret_utterance = _interpret
bedrock.verify_bag = _verify
bedrock.verify_product = _verify_product


class Handler(BaseHTTPRequestHandler):
    def _serve(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else ""
        event = {"httpMethod": self.command, "path": self.path.split("?")[0], "body": body or None}
        # extras answers /phantom-stock and /batches itself and passes everything else to handler.
        result = extras.lambda_handler(event, None)
        payload = result["body"].encode()
        self.send_response(result["statusCode"])
        for k, v in result["headers"].items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_OPTIONS = _serve

    def log_message(self, fmt, *args):
        print("  %s %s" % (self.command, self.path))


def seed_demo_state():
    """Build every demo scenario at boot, so the app has real orders the moment it opens."""
    global QUIET
    import demo
    QUIET = True
    try:
        built = demo.reset_all()
    finally:
        QUIET = False
    for row in built:
        print("  seeded %-14s %-26s %s" % (row["order_id"], row["title"][:26], row["status"]))
    return built


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    print("DarkStore Copilot local API on http://localhost:%d  (in-memory store, no AWS)" % port)
    seed_demo_state()
    print("\n  POST /demo/reset restores this exact state at any time.")
    print("  Set VITE_API_BASE=http://localhost:%d in frontend/.env\n" % port)
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()
