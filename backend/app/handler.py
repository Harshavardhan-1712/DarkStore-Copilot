"""Single Lambda behind API Gateway. Routes in, deterministic business logic, JSON out.

Separation of concerns, stated plainly:
  DynamoDB  -> absolute truth for inventory, SKU identity, stock, price and order state.
  Bedrock   -> understands speech and photos, proposes an action, speaks the local language.
  This file -> decides what actually happens, and is the only thing allowed to change state.
"""
import base64
import json
import logging
import os
import re
import secrets
import time
import uuid
from decimal import Decimal

import audit
import bedrock
import config
import contracts
import demo
import dynamo
import routing
import voice_lexicon
import state_machine as sm
import substitution
from errors import AppError, Conflict, GuardrailRejection, NotFound, PolicyViolation, ServiceUnavailable

log = logging.getLogger()
log.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

ALLOWED_IMAGE_FORMATS = {"jpeg", "jpg", "png", "webp"}
MAX_IMAGE_BYTES = 4 * 1024 * 1024


class Json(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj) if obj % 1 else int(obj)
        if isinstance(obj, (bytes, bytearray)):
            return base64.b64encode(obj).decode()
        return super().default(obj)


def respond(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*"),
            "Access-Control-Allow-Headers": "content-type,authorization",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Cache-Control": "no-store",
        },
        "body": json.dumps(body, cls=Json, ensure_ascii=False),
    }


# --- Routing table --------------------------------------------------------------------------
ROUTES = []


def route(method, pattern):
    regex = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern) + "/?$")

    def wrap(fn):
        ROUTES.append((method, regex, fn))
        return fn

    return wrap


def lambda_handler(event, context):
    method = (event.get("requestContext", {}).get("http", {}).get("method") or event.get("httpMethod") or "GET").upper()
    path = event.get("rawPath") or event.get("path") or "/"
    stage = event.get("requestContext", {}).get("stage")
    if stage and path.startswith("/" + stage):
        path = path[len(stage) + 1 :] or "/"

    if method == "OPTIONS":
        return respond(204, {})

    body = {}
    if event.get("body"):
        raw = event["body"]
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode("utf-8")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            return respond(400, {"ok": False, "error": {"code": "BAD_JSON", "message": "Request body is not JSON."}})

    for m, regex, fn in ROUTES:
        if m != method:
            continue
        match = regex.match(path)
        if match:
            try:
                return respond(200, {"ok": True, **fn(body, **match.groupdict())})
            except AppError as exc:
                log.warning("handled_error path=%s code=%s detail=%s", path, exc.code, exc.detail)
                return respond(exc.status, exc.to_body())
            except Exception:
                log.exception("unhandled path=%s", path)
                return respond(500, {"ok": False, "error": {"code": "INTERNAL", "message": "Something broke. Retry."}})

    return respond(404, {"ok": False, "error": {"code": "NO_ROUTE", "message": "%s %s" % (method, path)}})


# --- Helpers --------------------------------------------------------------------------------
def _now_ms():
    return int(time.time() * 1000)




def _already_processed(order_id, client_action_id):
    """Client retry guard, keyed on client_action_id.

    A picker's phone on a flaky store network retries. A voice hook can fire twice. Neither
    may decrement stock twice, so every mutating route checks here first and every mutating
    audit record carries the id. Production should promote this to a dedicated idempotency
    item with a TTL rather than scanning the audit log.
    """
    if not client_action_id:
        return None
    for entry in dynamo.list_audit(1000):
        if entry.get("order_id") == order_id and entry.get("detail", {}).get("client_action_id") == client_action_id:
            return dynamo.get_order(order_id)
    return None


def _replayed(order, client_action_id):
    """Uniform response for a request that was already executed under this action id."""
    return {
        **_view(order),
        "executed": "DUPLICATE_IGNORED",
        "idempotent_replay": True,
        "client_action_id": client_action_id,
        "policy_chain": [
            _step(True, "Action id already processed", client_action_id),
            _step(True, "Duplicate mutation prevented", "Order state unchanged"),
        ],
    }


def _step(ok, label, detail=""):
    """One row of the AI -> POLICY -> ACTION chain the UI renders. ok=None means informational."""
    return {"ok": ok, "label": label, "detail": str(detail)[:200]}

def _view(order):
    """The shape the PWA renders: order, current step, progress."""
    items = order.get("items", [])
    idx = routing.next_open_index(items)
    picked = sum(1 for i in items if i.get("state") in sm.TERMINAL_ITEM_STATES)
    return {
        "order": order,
        "current_index": idx,
        "current_item": items[idx] if idx is not None else None,
        "progress": {"picked": picked, "total": len(items)},
    }


def _require_line(order, index=None, sku_id=None):
    items = order.get("items", [])
    if index is None:
        index = routing.next_open_index(items)
    if index is None:
        raise Conflict("Every line on this order is already done.")
    index = int(index)
    if index < 0 or index >= len(items):
        raise NotFound("No line at position %s." % index)
    line = items[index]
    if sku_id and line["sku_id"] != sku_id:
        raise Conflict("Line %d is %s, not %s. Reload the order." % (index, line["sku_id"], sku_id))
    return index, line


def _eligible_substitutes(line):
    """Read the original SKU's registered substitutes and run them through store policy."""
    original = dynamo.get_inventory(line.get("substituted_from") or line["sku_id"])
    if not original:
        raise NotFound("SKU %s is not in inventory." % line["sku_id"])
    sub_ids = list(original.get("substitution_skus") or [])
    candidates = dynamo.get_inventory_many(sub_ids) if sub_ids else {}
    accepted, rejected = substitution.rank_substitutes(
        original, [candidates.get(s) for s in sub_ids], int(line.get("qty", 1))
    )
    return original, accepted, rejected


def _apply_substitution(order, index, line, substitute_sku, actor, source, client_action_id=None):
    original, accepted, rejected = _eligible_substitutes(line)
    chosen = next((c for c in accepted if c["sku_id"] == substitute_sku), None)
    if chosen is None:
        reason = next((r["message"] for r in rejected if r["sku_id"] == substitute_sku), "Not a registered substitute.")
        dynamo.put_audit(
            audit.make_entry(
                order["order_id"], "SUBSTITUTION_REJECTED", actor,
                {"original": original["sku_id"], "attempted": substitute_sku, "reason": reason,
                 "source": source, "client_action_id": client_action_id},
            )
        )
        raise PolicyViolation("%s cannot replace %s. %s" % (substitute_sku, original["sku_id"], reason),
                              {"eligible": accepted})

    sub_record = dynamo.get_inventory(substitute_sku)
    qty = int(line.get("qty", 1))
    delta = substitution.price_delta(original, sub_record, qty)
    entry = audit.make_entry(
        order["order_id"], "SUBSTITUTION_APPLIED", actor,
        {
            "original_sku": original["sku_id"],
            "substitute_sku": substitute_sku,
            "qty": qty,
            "invoice_delta": str(delta),
            "source": source,
            "client_action_id": client_action_id,
        },
    )
    dynamo.commit_substitution(order["order_id"], index, original, sub_record, qty, delta, entry)
    return {"substitute": chosen, "invoice_delta": delta}


# --- Orders ---------------------------------------------------------------------------------
@route("POST", "/orders")
def create_order(body):
    """Seed or receive an order, then build the pick path deterministically from store layout."""
    raw_items = body.get("items") or []
    if not raw_items:
        raise AppError("An order needs at least one item.")

    inventory = dynamo.get_inventory_many([i["sku_id"] for i in raw_items])
    unknown = [i["sku_id"] for i in raw_items if i["sku_id"] not in inventory]
    if unknown:
        raise NotFound("Unknown SKUs on this order.", {"skus": unknown})

    items = routing.optimize_route(raw_items, inventory)
    total = sum(dynamo.to_decimal(i["unit_price"]) * i["qty"] for i in items)
    order_id = body.get("order_id") or "ORD-%s" % uuid.uuid4().hex[:10].upper()

    order = {
        "order_id": order_id,
        "status": sm.CREATED,
        "picker_id": body.get("picker_id"),
        "customer_ref": body.get("customer_ref"),
        "items": items,
        "optimized_route": [i["sku_id"] for i in items],
        "invoice_total": total,
        "bag_attempts": 0,
        "demo_scenario": body.get("demo_scenario"),
        "created_at": _now_ms(),
        "updated_at": _now_ms(),
    }
    dynamo.put_order(order)
    return _view(order)


@route("GET", "/orders/{order_id}")
def read_order(body, order_id):
    return _view(dynamo.get_order(order_id))


@route("POST", "/orders/{order_id}/start")
def start_picking(body, order_id):
    order = dynamo.get_order(order_id)
    sm.assert_order_transition(order["status"], sm.PICKING)
    updated = dynamo.set_order_status(order_id, order["status"], sm.PICKING,
                                      {"picker_id": body.get("picker_id") or order.get("picker_id")})
    dynamo.put_audit(audit.make_entry(order_id, "ORDER_PICKING_STARTED", body.get("picker_id") or order.get("picker_id"), {}))
    return _view(updated)


@route("GET", "/orders/{order_id}/substitutes")
def list_substitutes(body, order_id):
    order = dynamo.get_order(order_id)
    index, line = _require_line(order)
    original, accepted, rejected = _eligible_substitutes(line)
    return {"index": index, "original": original["sku_id"], "eligible": accepted, "rejected": rejected}


# --- Picking --------------------------------------------------------------------------------
@route("POST", "/orders/{order_id}/pick")
def confirm_pick(body, order_id):
    """Tap fallback for CONFIRM_PICK. Identical path to the voice route — same guarantees."""
    order = dynamo.get_order(order_id)
    client_action_id = body.get("client_action_id")
    prior = _already_processed(order_id, client_action_id)
    if prior:
        return _replayed(prior, client_action_id)
    if order["status"] != sm.PICKING:
        raise Conflict("Order is %s, not PICKING." % order["status"])
    index, line = _require_line(order, body.get("index"), body.get("sku_id"))
    sm.assert_item_transition(line.get("state"), sm.PICKED)

    entry = audit.make_entry(order_id, "ITEM_PICKED", body.get("picker_id") or order.get("picker_id"),
                             {"sku_id": line["sku_id"], "qty": int(line.get("qty", 1)),
                              "source": body.get("source", "tap"), "client_action_id": body.get("client_action_id")})
    dynamo.commit_pick(order_id, index, line["sku_id"], int(line.get("qty", 1)), sm.PICKED, entry)
    return _view(dynamo.get_order(order_id))


@route("POST", "/orders/{order_id}/exception")
def flag_exception(body, order_id):
    """Picker cannot complete a line: missing, damaged, wrong label."""
    order = dynamo.get_order(order_id)
    client_action_id = body.get("client_action_id")
    prior = _already_processed(order_id, client_action_id)
    if prior:
        return _replayed(prior, client_action_id)
    index, line = _require_line(order, body.get("index"), body.get("sku_id"))
    target = sm.OOS if body.get("kind") == "OOS" else sm.EXCEPTION
    sm.assert_item_transition(line.get("state"), target)

    entry = audit.make_entry(order_id, "ITEM_" + target, body.get("picker_id") or order.get("picker_id"),
                             {"sku_id": line["sku_id"], "note": str(body.get("note", ""))[:280], "client_action_id": body.get("client_action_id")})
    dynamo.mark_item_state(order_id, index, line["sku_id"], target, entry)

    original, accepted, rejected = _eligible_substitutes(line)
    return {**_view(dynamo.get_order(order_id)), "eligible_substitutes": accepted, "rejected_substitutes": rejected}


@route("POST", "/orders/{order_id}/substitute")
def apply_substitute(body, order_id):
    """Tap fallback for SUBSTITUTE_ITEM. Order and invoice move together or not at all."""
    order = dynamo.get_order(order_id)
    client_action_id = body.get("client_action_id")
    prior = _already_processed(order_id, client_action_id)
    if prior:
        return _replayed(prior, client_action_id)
    if order["status"] != sm.PICKING:
        raise Conflict("Order is %s, not PICKING." % order["status"])
    index, line = _require_line(order, body.get("index"))
    if line.get("state") == sm.AVAILABLE:
        # Picker went straight to the swap list without tapping "Not on shelf" first.
        # Record the stockout so the audit trail still shows why the swap happened.
        dynamo.mark_item_state(order_id, index, line["sku_id"], sm.OOS,
                               audit.make_entry(order_id, "ITEM_OOS", order.get("picker_id"),
                                                {"sku_id": line["sku_id"], "source": "substitute_request"}))
        line = {**line, "state": sm.OOS}
    sm.assert_item_transition(line.get("state"), sm.SUBSTITUTED)
    sku = str(body.get("substitute_sku", "")).strip().upper()
    if not sku:
        raise AppError("substitute_sku is required.")

    result = _apply_substitution(order, index, line, sku,
                                 body.get("picker_id") or order.get("picker_id"),
                                 body.get("source", "tap"), client_action_id)
    return {**_view(dynamo.get_order(order_id)), **result,
            "policy_chain": [_step(None, "Requested substitute", sku),
                             _step(True, "Policy", "Registered substitute, category, stock and price all passed"),
                             _step(True, "Action", "%s committed to the order" % result["substitute"]["name"])]}


# --- Voice ----------------------------------------------------------------------------------
def _interpret(utterance, line, accepted, allowed, lang, simulated, order_id):
    """Produce a validated intent plus how it was produced. Never mutates anything.

    Three sources, in priority order:
      1. simulated_intent  — the demo switch. Still validated by the same guardrail.
      2. deterministic fast path — obvious multilingual commands, resolved locally.
      3. Amazon Bedrock — genuinely ambiguous natural language.
    """
    if simulated:
        try:
            return contracts.validate_intent(simulated, allowed), {"interpreter": "simulated-intent"}, None
        except GuardrailRejection as exc:
            # The demo switch deliberately feeds bad proposals. Audit them exactly like a
            # real model hallucination, otherwise the control tower under-reports guardrails.
            dynamo.put_audit(audit.make_entry(order_id, "GUARDRAIL_REJECTED", "bedrock",
                                              {"utterance": utterance[:280], "reason": exc.message,
                                               "detail": exc.detail, "source": "simulated-intent",
                                               "proposed_sku": str(simulated.get("selected_sku"))[:64]}))
            return contracts.manual_fallback(line, exc.message), {"interpreter": "simulated-intent"}, exc

    fast_intent, meta = voice_lexicon.fast_path(utterance, line, accepted)
    if fast_intent:
        try:
            return contracts.validate_intent(fast_intent, allowed), meta, None
        except GuardrailRejection as exc:
            return contracts.manual_fallback(line, exc.message), meta, exc

    try:
        intent = bedrock.interpret_utterance(utterance, line, accepted, lang)
        return intent, {"interpreter": "amazon-bedrock"}, None
    except GuardrailRejection as exc:
        dynamo.put_audit(audit.make_entry(order_id, "GUARDRAIL_REJECTED", "bedrock",
                                          {"utterance": utterance[:280], "reason": exc.message,
                                           "detail": exc.detail}))
        return contracts.manual_fallback(line, exc.message), {"interpreter": "amazon-bedrock"}, exc
    except Exception as exc:  # network, throttle, model unavailable
        log.exception("bedrock_unavailable")
        dynamo.put_audit(audit.make_entry(order_id, "BEDROCK_UNAVAILABLE", "system",
                                          {"utterance": utterance[:280], "error": type(exc).__name__}))
        return (contracts.manual_fallback(line, "Voice service unavailable."),
                {"interpreter": "amazon-bedrock", "unavailable": True}, None)


def _handle_concurrent_pick(order, index, line, picker, utterance, accepted, client_action_id):
    """ITEM_ALREADY_TAKEN -> CONCURRENT_PICK. The backend, not the model, concludes what is true.

    The model is only allowed to report what the picker said. Whether this order's unit is
    actually gone is settled here, against current order and inventory state, and the line is
    moved to CONTESTED — a different state from OOS, because the causes differ and operations
    needs to tell picker-coordination problems apart from inventory shortages.
    """
    order_id = order["order_id"]
    chain = [_step(None, "AI understands", "ITEM_ALREADY_TAKEN"),
             _step(None, "Policy", "CONCURRENT_PICK")]

    live = dynamo.get_order(order_id)
    live_line = live["items"][index]
    chain.append(_step(True, "Current order checked", "Line %d is %s" % (index, live_line.get("state"))))

    # Already resolved by someone else: report it, change nothing.
    if live_line.get("state") in sm.TERMINAL_ITEM_STATES:
        dynamo.put_audit(audit.make_entry(
            order_id, "CONCURRENT_PICK_DUPLICATE_BLOCKED", picker,
            {"sku_id": live_line.get("sku_id"), "line_state": live_line.get("state"),
             "utterance": utterance[:280], "policy": "CONCURRENT_PICK",
             "client_action_id": client_action_id, "source": "voice"}))
        chain.append(_step(True, "Duplicate pick prevented", "Line was already %s" % live_line.get("state")))
        chain.append(_step(True, "Order state unchanged", "No mutation applied"))
        return "CONCURRENT_PICK_NO_CHANGE", {
            "policy": "CONCURRENT_PICK", "policy_chain": chain,
            "concurrent_pick": {"line_state": live_line.get("state"), "duplicate_prevented": True,
                                "needs_manual_resolution": False},
        }

    inv = dynamo.get_inventory(live_line.get("sku_id")) or {}
    store_stock = int(dynamo.to_decimal(inv.get("stock_qty", 0)))
    chain.append(_step(True, "Inventory state checked", "%d on hand store-wide" % store_stock))

    sm.assert_item_transition(live_line.get("state"), sm.CONTESTED)
    entry = audit.make_entry(order_id, "ITEM_CONCURRENT_PICK", picker, {
        "sku_id": live_line.get("sku_id"), "qty": int(live_line.get("qty", 1)),
        "policy": "CONCURRENT_PICK", "store_stock": store_stock,
        "duplicate_pick_prevented": True, "utterance": utterance[:280],
        "client_action_id": client_action_id, "source": "voice",
    })
    dynamo.mark_item_state(order_id, index, live_line["sku_id"], sm.CONTESTED, entry)
    chain.append(_step(True, "Duplicate pick prevented", "Line moved to CONTESTED, not picked"))

    needs_manual = not accepted
    if accepted:
        chain.append(_step(True, "Approved substitute available", accepted[0]["name"]))
    else:
        chain.append(_step(False, "No approved substitute", "Flagged for manual resolution"))
        dynamo.put_audit(audit.make_entry(order_id, "MANUAL_RESOLUTION_REQUIRED", "system", {
            "sku_id": live_line.get("sku_id"), "cause": "CONCURRENT_PICK",
            "client_action_id": client_action_id}))

    return "ITEM_ALREADY_TAKEN", {
        "policy": "CONCURRENT_PICK",
        "policy_chain": chain,
        "eligible_substitutes": accepted,
        "concurrent_pick": {
            "sku_id": live_line.get("sku_id"), "name": live_line.get("name"),
            "qty": int(live_line.get("qty", 1)), "store_stock": store_stock,
            "duplicate_prevented": True, "needs_manual_resolution": needs_manual,
            "reason": "Another picker has already taken this item.",
        },
    }


@route("POST", "/orders/{order_id}/intent")
def voice_intent(body, order_id):
    """Speech -> interpretation -> guardrail -> policy -> deterministic execution.

    The interpretation layer picks an action and speaks the picker's language. It never
    decides whether the action is allowed: that is settled below, against the store of record.
    """
    utterance = str(body.get("utterance", "")).strip()
    if not utterance:
        raise AppError("utterance is required.")
    order = dynamo.get_order(order_id)

    client_action_id = body.get("client_action_id")
    prior = _already_processed(order_id, client_action_id)
    if prior:
        return _replayed(prior, client_action_id)

    if order["status"] != sm.PICKING:
        raise Conflict("Order is %s, not PICKING." % order["status"])
    index, line = _require_line(order, body.get("index"))
    picker = body.get("picker_id") or order.get("picker_id")

    _, accepted, rejected = _eligible_substitutes(line)
    allowed = {line["sku_id"]} | {c["sku_id"] for c in accepted}

    intent, interpretation, guardrail = _interpret(
        utterance, line, accepted, allowed, body.get("lang", "te"),
        body.get("simulated_intent"), order_id,
    )

    executed, result = intent["action"], {}
    chain = [_step(None, "You said", utterance),
             _step(None, "AI understands", intent["action"])]
    if guardrail:
        chain = [
            _step(None, "You said", utterance),
            _step(None, "AI proposal", (body.get("simulated_intent") or {}).get("selected_sku") or "unparseable output"),
            _step(False, "Guardrail rejected the proposal", guardrail.message),
            _step(True, "Order state unchanged", "No mutation applied"),
        ]

    if intent["action"] == "CONFIRM_PICK" and not intent.get("fallback"):
        entry = audit.make_entry(order_id, "ITEM_PICKED", picker,
                                 {"sku_id": line["sku_id"], "qty": int(line.get("qty", 1)),
                                  "source": "voice", "utterance": utterance[:280],
                                  "interpreter": interpretation.get("interpreter"),
                                  "client_action_id": client_action_id})
        dynamo.commit_pick(order_id, index, line["sku_id"], int(line.get("qty", 1)), sm.PICKED, entry)
        chain.append(_step(True, "Policy", "Line open and order PICKING"))
        chain.append(_step(True, "Action", "%s marked picked" % line.get("name", line["sku_id"])))

    elif intent["action"] == "ITEM_ALREADY_TAKEN" and not intent.get("fallback"):
        executed, result = _handle_concurrent_pick(
            order, index, line, picker, utterance, accepted, client_action_id)
        chain = result.pop("policy_chain")

    elif intent["action"] == "SUBSTITUTE_ITEM" and not intent.get("fallback"):
        if line.get("state") == sm.AVAILABLE:
            dynamo.mark_item_state(order_id, index, line["sku_id"], sm.OOS,
                                   audit.make_entry(order_id, "ITEM_OOS", picker,
                                                    {"sku_id": line["sku_id"], "source": "voice",
                                                     "client_action_id": client_action_id}))
        try:
            result = _apply_substitution(order, index, line, intent["selected_sku"], picker, "voice",
                                         client_action_id)
            chain.append(_step(True, "Policy", "Registered substitute, category, stock and price all passed"))
            chain.append(_step(True, "Action", "%s selected" % result["substitute"]["name"]))
        except PolicyViolation as exc:
            # Policy said no. Hand the picker the eligible list and let them tap.
            executed = "AWAITING_PICKER_CHOICE"
            result = {"policy_message": exc.message, "eligible_substitutes": accepted}
            chain.append(_step(False, "Policy rejected the proposal", exc.message))
            chain.append(_step(True, "Order state unchanged", "Picker chooses manually"))

    else:  # FLAG_EXCEPTION, or any fallback
        if line.get("state") == sm.AVAILABLE:
            dynamo.mark_item_state(order_id, index, line["sku_id"], sm.EXCEPTION,
                                   audit.make_entry(order_id, "ITEM_EXCEPTION", picker,
                                                    {"sku_id": line["sku_id"], "utterance": utterance[:280],
                                                     "reason": intent["reason"],
                                                     "client_action_id": client_action_id}))
        executed = "FLAG_EXCEPTION"
        result = {"eligible_substitutes": accepted}
        if not guardrail:
            chain.append(_step(None, "Action", "Line flagged for picker decision"))

    return {**_view(dynamo.get_order(order_id)), "intent": intent, "executed": executed,
            "interpretation": interpretation, "policy_chain": chain,
            "rejected_substitutes": rejected, "client_action_id": client_action_id, **result}


@route("POST", "/orders/{order_id}/verify-item")
def verify_item(body, order_id):
    order = dynamo.get_order(order_id)
    if order["status"] != sm.PICKING:
        raise Conflict("Product verification is available while the order is PICKING.")
    index, line = _require_line(order, body.get("index"), body.get("sku_id"))
    image_format = str(body.get("image_format", "jpeg")).lower().lstrip(".")
    if image_format not in ALLOWED_IMAGE_FORMATS:
        raise AppError("Photo must be jpeg, png or webp.")
    image_format = "jpeg" if image_format == "jpg" else image_format
    try:
        image_bytes = base64.b64decode(str(body.get("image_base64", "")).split(",")[-1], validate=True)
    except Exception:
        raise AppError("Photo was not valid base64.")
    if not image_bytes:
        raise AppError("Photo is empty.")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise AppError("Photo is over 4 MB. Retake it at a lower resolution.")
    try:
        result = bedrock.verify_product(image_bytes, image_format, line)
    except GuardrailRejection as exc:
        result = {"verdict": "UNCLEAR", "expected_sku": line["sku_id"], "detected_product": "Unknown",
                  "confidence": 0.0, "notes": "The visual model could not produce a safe result. Retake the photo.",
                  "provider": "guardrail-fallback"}
        dynamo.put_audit(audit.make_entry(order_id, "ITEM_VERIFY_GUARDRAIL", order.get("picker_id"),
                                          {"sku_id": line["sku_id"], "reason": exc.message}))
    except Exception:
        log.exception("product_visual_unavailable")
        raise AppError("Product visual check is unavailable right now. You can still use manual pick confirmation.")
    event = "ITEM_VERIFY_MATCH" if result["verdict"] == "MATCH" else "ITEM_VERIFY_FAILED"
    dynamo.put_audit(audit.make_entry(order_id, "PRODUCT_" + event.split("ITEM_")[-1],
                                      order.get("picker_id"), {**result, "index": index}))
    return {**_view(order), "product_verification": result}

# --- Speech synthesis -----------------------------------------------------------------------
@route("POST", "/orders/{order_id}/speech")
def synthesize_speech_for_order(body, order_id):
    """Order-scoped alias. Speech has no order side effects; the id is for audit correlation."""
    return synthesize_speech(body)


@route("POST", "/speech")
def synthesize_speech(body):
    text = str(body.get("text", "")).strip()
    lang = str(body.get("lang", "hi")).lower()
    if not text:
        raise AppError("text is required.")
    if lang not in config.SUPPORTED_LANGS:
        raise AppError("Unsupported language.")
    try:
        result = bedrock.synthesize_speech(text, lang)
    except Exception as exc:
        log.exception("polly_unavailable")
        raise ServiceUnavailable("Amazon Polly is unavailable. Use browser speech fallback.", {"provider": "amazon-polly", "error": type(exc).__name__})
    return result


# --- Operations dashboard ------------------------------------------------------------------
@route("GET", "/dashboard")
def dashboard(body):
    orders = dynamo.list_orders(200)
    audits = dynamo.list_audit(1000)
    statuses = [sm.CREATED, sm.PICKING, sm.BAG_VERIFICATION, sm.READY_FOR_PICKUP, sm.DISPATCHED, sm.CANCELLED]
    status_counts = {s: 0 for s in statuses}
    for o in orders:
        status_counts[o.get("status")] = status_counts.get(o.get("status"), 0) + 1

    today = time.strftime("%Y-%m-%d", time.localtime())
    today_orders = [o for o in orders if time.strftime("%Y-%m-%d", time.localtime(o.get("created_at", 0) / 1000)) == today]
    oos = [a for a in audits if a.get("event") == "ITEM_OOS"]
    concurrent = [a for a in audits if a.get("event") == "ITEM_CONCURRENT_PICK"]
    concurrent_blocked = [a for a in audits if a.get("event") == "CONCURRENT_PICK_DUPLICATE_BLOCKED"]
    manual_needed = [a for a in audits if a.get("event") == "MANUAL_RESOLUTION_REQUIRED"]
    substitutions = [a for a in audits if a.get("event") == "SUBSTITUTION_APPLIED"]
    auto_resolved = [a for a in substitutions if a.get("detail", {}).get("source") == "voice"]
    bag_verified = [a for a in audits if a.get("event") == "BAG_VERIFY_VERIFIED"]
    bag_failed = [a for a in audits if a.get("event") == "BAG_VERIFY_FAILED"]
    bag_attempts = [a for a in audits if str(a.get("event", "")).startswith("BAG_VERIFY_")]
    guardrail = [a for a in audits if a.get("event") == "GUARDRAIL_REJECTED"]
    product_matches = [a for a in audits if a.get("event") == "PRODUCT_VERIFY_MATCH"]
    product_failures = [a for a in audits if a.get("event") == "PRODUCT_VERIFY_FAILED"]
    product_attempts = product_matches + product_failures

    start_by_order = {}
    end_by_order = {}
    for a in audits:
        event = a.get("event")
        if event == "ORDER_PICKING_STARTED": start_by_order[a.get("order_id")] = a.get("ts")
        if event in ("BAG_VERIFY_VERIFIED", "BAG_VERIFY_FAILED"): end_by_order[a.get("order_id")] = a.get("ts")
    durations = [(end_by_order[k] - v) / 1000 for k, v in start_by_order.items() if k in end_by_order and end_by_order[k] >= v]
    avg_pick_seconds = round(sum(durations) / len(durations), 1) if durations else None

    active = [o for o in orders if o.get("status") in (sm.PICKING, sm.BAG_VERIFICATION)]
    units_pending = sum(
        int(i.get("qty", 1)) for o in active for i in o.get("items", [])
        if i.get("state") not in sm.TERMINAL_ITEM_STATES
    )
    active_pickers = len({o.get("picker_id") for o in active if o.get("picker_id")})
    dispatched_today = sum(1 for a in audits if a.get("event") == "DISPATCHED" and time.strftime("%Y-%m-%d", time.localtime(a.get("ts", 0) / 1000)) == today)
    oos_resolved = len({a.get("order_id") for a in substitutions})
    oos_orders = len({a.get("order_id") for a in oos})
    oos_resolution_rate = round(oos_resolved / oos_orders * 100, 1) if oos_orders else None
    ready_for_pickup = status_counts.get(sm.READY_FOR_PICKUP, 0)

    # Aggregate aisle demand from the order snapshot so the dashboard stays useful
    # even with the local in-memory store and without a second inventory query.
    aisle_map = {}
    for o in active:
        seen = set()
        for i in o.get("items", []):
            if i.get("state") in sm.TERMINAL_ITEM_STATES:
                continue
            aisle = str(i.get("aisle") or "—")
            qty = int(i.get("qty", 1))
            rec = aisle_map.setdefault(aisle, {"aisle": aisle, "units": 0, "orders": 0})
            rec["units"] += qty
            if o.get("order_id") not in seen:
                rec["orders"] += 1
                seen.add(o.get("order_id"))
    aisle_load = sorted(aisle_map.values(), key=lambda x: (-x["units"], x["aisle"]))[:6]
    max_units = max([x["units"] for x in aisle_load] or [1])
    for row in aisle_load:
        row["percent"] = round(row["units"] / max_units * 100, 1)

    recent = sorted(orders, key=lambda x: x.get("updated_at", 0), reverse=True)[:8]
    return {
        "generated_at": _now_ms(),
        "live_exceptions": _live_exceptions(orders, audits),
        "recent_decisions": _recent_decisions(audits),
        "metrics": {
            "orders_today": len(today_orders),
            "orders_total": len(orders),
            "active_picks": len(active),
            "active_pickers": active_pickers,
            "units_pending": units_pending,
            "ready_for_pickup": ready_for_pickup,
            "dispatched_today": dispatched_today,
            "avg_pick_seconds": avg_pick_seconds,
            "oos_events": len(oos),
            "oos_resolution_rate": oos_resolution_rate,
            "ai_resolved_substitutions": len(auto_resolved),
            "substitutions_total": len(substitutions),
            "bag_verification_rate": round(len(bag_verified) / len(bag_attempts) * 100, 1) if bag_attempts else None,
            "bag_attempts": len(bag_attempts),
            "bag_failures": len(bag_failed),
            "guardrail_rejections": len(guardrail),
            "concurrent_pick_events": len(concurrent),
            "duplicate_picks_prevented": len(concurrent) + len(concurrent_blocked),
            "manual_resolution_required": len(manual_needed),
            "product_verify_attempts": len(product_attempts),
            "product_verify_matches": len(product_matches),
            "product_verify_rate": round(len(product_matches) / len(product_attempts) * 100, 1) if product_attempts else None,
        },
        "status_counts": status_counts,
        "recent_orders": recent,
        "aisle_load": aisle_load,
    }


# --- Bag verification and handover ----------------------------------------------------------
@route("POST", "/orders/{order_id}/verify-bag")
def verify_bag(body, order_id):
    order = dynamo.get_order(order_id)
    items = order.get("items", [])
    if not sm.order_is_complete(items):
        raise Conflict("Finish every line before checking the bag.",
                       {"open_index": routing.next_open_index(items)})

    if order["status"] == sm.PICKING:
        order = dynamo.set_order_status(order_id, sm.PICKING, sm.BAG_VERIFICATION)
    elif order["status"] != sm.BAG_VERIFICATION:
        raise Conflict("Order is %s." % order["status"])

    image_format = str(body.get("image_format", "jpeg")).lower().lstrip(".")
    if image_format not in ALLOWED_IMAGE_FORMATS:
        raise AppError("Photo must be jpeg, png or webp.")
    image_format = "jpeg" if image_format == "jpg" else image_format
    try:
        image_bytes = base64.b64decode(str(body.get("image_base64", "")).split(",")[-1], validate=True)
    except Exception:
        raise AppError("Photo was not valid base64.")
    if not image_bytes:
        raise AppError("Photo is empty.")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise AppError("Photo is over 4 MB. Retake it at a lower resolution.")

    manifest = [i for i in items if i.get("state") != sm.REMOVED]
    attempt_no = int(order.get("bag_attempts", 0)) + 1
    try:
        verdict = bedrock.verify_bag(image_bytes, image_format, manifest,
                                     context={"order_id": order_id, "attempt": attempt_no})
    except GuardrailRejection as exc:
        verdict = {"verdict": "FAILED", "missing_skus": [], "confidence": 0.0,
                   "notes": "Could not read the photo clearly. Retake it top-down in better light.",
                   "spoken_response_telugu": "", "spoken_response_hindi": ""}
        dynamo.put_audit(audit.make_entry(order_id, "GUARDRAIL_REJECTED", "bedrock",
                                          {"stage": "bag_verification", "reason": exc.message}))
    except Exception:
        log.exception("bedrock_vision_unavailable")
        raise AppError("Bag check is unavailable right now. Retry, or hand over manually with a supervisor.")

    attempts = attempt_no
    expected_units = sum(int(i.get("qty", 1)) for i in manifest)
    missing_units = sum(int(i.get("qty", 1)) for i in manifest if i["sku_id"] in set(verdict["missing_skus"]))
    verdict = {**verdict,
               "expected_items": len(manifest),
               "expected_units": expected_units,
               "detected_units": expected_units - missing_units,
               "missing_items": [{"sku_id": i["sku_id"], "name": i.get("name"), "qty": int(i.get("qty", 1))}
                                 for i in manifest if i["sku_id"] in set(verdict["missing_skus"])]}
    dynamo.put_audit(audit.make_entry(order_id, "BAG_VERIFY_" + verdict["verdict"],
                                      order.get("picker_id"), {**verdict, "attempt": attempts}))

    if verdict["verdict"] == "VERIFIED":
        token = secrets.token_urlsafe(18)
        updated = dynamo.set_order_status(
            order_id, sm.BAG_VERIFICATION, sm.READY_FOR_PICKUP,
            {"bag_attempts": attempts, "handover_token": token, "verified_at": _now_ms()},
        )
        return {**_view(updated), "verification": verdict,
                "handover": {"token": token, "qr_payload": "darkstore://handover/%s/%s" % (order_id, token)}}

    updated = dynamo.set_order_status(order_id, sm.BAG_VERIFICATION, sm.BAG_VERIFICATION,
                                      {"bag_attempts": attempts})
    return {**_view(updated), "verification": verdict}


@route("POST", "/orders/{order_id}/dispatch")
def dispatch(body, order_id):
    order = dynamo.get_order(order_id)
    sm.assert_order_transition(order["status"], sm.DISPATCHED)
    if body.get("handover_token") != order.get("handover_token"):
        raise PolicyViolation("Handover token does not match. Rider must rescan the QR code.")
    updated = dynamo.set_order_status(order_id, sm.READY_FOR_PICKUP, sm.DISPATCHED,
                                      {"rider_id": body.get("rider_id"), "dispatched_at": _now_ms()})
    dynamo.put_audit(audit.make_entry(order_id, "DISPATCHED", body.get("rider_id") or "rider", {}))
    return _view(updated)


# --- Exception and decision feeds ----------------------------------------------------------
# Severity and copy for the events operations actually has to act on. Stockout and concurrent
# pick are deliberately separate rows: one is an inventory problem, the other is a picker
# coordination problem, and the fix is different.
_DECISION_EVENTS = {
    "ITEM_CONCURRENT_PICK": ("CONCURRENT_PICK", "Concurrent pick detected — duplicate pick prevented"),
    "CONCURRENT_PICK_DUPLICATE_BLOCKED": ("CONCURRENT_PICK", "Repeat report on a resolved line — no mutation"),
    "SUBSTITUTION_APPLIED": ("APPROVED", "Substitution approved by policy and committed"),
    "SUBSTITUTION_REJECTED": ("BLOCKED", "Substitution refused by policy"),
    "GUARDRAIL_REJECTED": ("BLOCKED", "AI proposal blocked by guardrail"),
    "BEDROCK_UNAVAILABLE": ("DEGRADED", "Voice model unavailable — manual fallback offered"),
    "ITEM_OOS": ("STOCKOUT", "Stockout reported on the shelf"),
    "BAG_VERIFY_FAILED": ("BLOCKED", "Bag verification failed"),
    "BAG_VERIFY_VERIFIED": ("APPROVED", "Bag verification passed"),
    "PRODUCT_VERIFY_MATCH": ("APPROVED", "Product visual check matched"),
    "PRODUCT_VERIFY_FAILED": ("BLOCKED", "Product visual check did not match"),
    "ITEM_VERIFY_GUARDRAIL": ("DEGRADED", "Visual model output refused — retake requested"),
    "MANUAL_RESOLUTION_REQUIRED": ("BLOCKED", "No approved substitute — manual resolution needed"),
}


def _live_exceptions(orders, audits):
    """Open exceptions, derived from current line state and the audit trail. Nothing invented."""
    latest_bag = {}
    guardrails = {}
    for a in sorted(audits, key=lambda x: x.get("ts", 0)):
        event = a.get("event", "")
        if event.startswith("BAG_VERIFY_"):
            latest_bag[a.get("order_id")] = a
        if event in ("GUARDRAIL_REJECTED", "MANUAL_RESOLUTION_REQUIRED"):
            guardrails[a.get("order_id")] = a

    rows = []
    for order in orders:
        if order.get("status") in (sm.DISPATCHED, sm.CANCELLED):
            continue
        order_id = order.get("order_id")
        for line in order.get("items", []):
            if line.get("state") == sm.CONTESTED:
                rows.append({
                    "order_id": order_id, "kind": "CONCURRENT_PICK", "severity": "orange",
                    "title": "%s unavailable — another picker took it" % line.get("name"),
                    "detail": "Duplicate pick prevented. Substitute or manual resolution required.",
                    "sku_id": line.get("sku_id"),
                })
            elif line.get("state") == sm.OOS:
                rows.append({
                    "order_id": order_id, "kind": "STOCKOUT", "severity": "red",
                    "title": "%s out of stock" % line.get("name"),
                    "detail": "Awaiting a policy-approved substitute or a skip decision.",
                    "sku_id": line.get("sku_id"),
                })
            elif line.get("state") == sm.EXCEPTION:
                rows.append({
                    "order_id": order_id, "kind": "EXCEPTION", "severity": "red",
                    "title": "%s flagged by the picker" % line.get("name"),
                    "detail": "Line needs a supervisor decision before the bag can be checked.",
                    "sku_id": line.get("sku_id"),
                })

        bag = latest_bag.get(order_id)
        if bag and bag.get("event") == "BAG_VERIFY_FAILED" and order.get("status") == sm.BAG_VERIFICATION:
            missing = bag.get("detail", {}).get("missing_items") or []
            names = ", ".join(m.get("name") or m.get("sku_id") for m in missing) or "unidentified item"
            rows.append({
                "order_id": order_id, "kind": "BAG_VERIFICATION", "severity": "yellow",
                "title": "Bag verification failed — missing %s" % names,
                "detail": "Attempt %s. Retry the scan before handover." % bag.get("detail", {}).get("attempt"),
            })

        gr = guardrails.get(order_id)
        if gr:
            rows.append({
                "order_id": order_id,
                "kind": "AI_BLOCKED" if gr.get("event") == "GUARDRAIL_REJECTED" else "MANUAL_RESOLUTION",
                "severity": "blue",
                "title": ("AI proposal blocked — manual intervention"
                          if gr.get("event") == "GUARDRAIL_REJECTED"
                          else "No approved substitute — manual resolution"),
                "detail": str(gr.get("detail", {}).get("reason") or gr.get("detail", {}).get("cause") or "")[:160],
            })

    severity_rank = {"red": 0, "orange": 1, "yellow": 2, "blue": 3}
    rows.sort(key=lambda r: (severity_rank.get(r["severity"], 9), r["order_id"]))
    return rows[:12]


def _recent_decisions(audits, limit=14):
    """The AI / policy decision feed. One row per real audit record, newest first."""
    rows = []
    for a in sorted(audits, key=lambda x: x.get("ts", 0), reverse=True):
        mapped = _DECISION_EVENTS.get(a.get("event"))
        if not mapped:
            continue
        outcome, label = mapped
        detail = a.get("detail", {}) or {}
        rows.append({
            "ts": a.get("ts"), "order_id": a.get("order_id"), "event": a.get("event"),
            "outcome": outcome, "label": label, "actor": a.get("actor"),
            "sku_id": detail.get("sku_id") or detail.get("substitute_sku") or detail.get("attempted"),
            "note": str(detail.get("reason") or detail.get("notes") or detail.get("policy") or "")[:140],
        })
        if len(rows) >= limit:
            break
    return rows


# --- Audit feed -----------------------------------------------------------------------------
@route("GET", "/orders/{order_id}/audit")
def order_audit(body, order_id):
    """The AI Activity feed for one order. Straight audit records, newest last."""
    entries = [a for a in dynamo.list_audit(1000) if a.get("order_id") == order_id]
    entries.sort(key=lambda a: a.get("ts", 0))
    return {"order_id": order_id, "audit": entries[-60:],
            "decisions": _recent_decisions(entries, limit=40)}


@route("GET", "/audit")
def store_audit(body):
    entries = sorted(dynamo.list_audit(1000), key=lambda a: a.get("ts", 0))
    return {"audit": entries[-120:]}


# --- Order list -----------------------------------------------------------------------------
@route("GET", "/orders")
def list_orders_route(body):
    """Summaries for the order picker and the demo control centre."""
    rows = []
    for o in dynamo.list_orders(200):
        items = o.get("items", [])
        done = sum(1 for i in items if i.get("state") in sm.TERMINAL_ITEM_STATES)
        open_index = routing.next_open_index(items)
        rows.append({
            "order_id": o.get("order_id"), "status": o.get("status"),
            "picker_id": o.get("picker_id"), "customer_ref": o.get("customer_ref"),
            "demo_scenario": o.get("demo_scenario"),
            "items_total": len(items), "items_done": done,
            "invoice_total": o.get("invoice_total"), "updated_at": o.get("updated_at"),
            "open_item": (items[open_index].get("name") if open_index is not None else None),
            "exception_states": sorted({i.get("state") for i in items
                                        if i.get("state") in (sm.OOS, sm.EXCEPTION, sm.CONTESTED)}),
        })
    rows.sort(key=lambda r: str(r["order_id"]))
    return {"orders": rows}


# --- Inventory ------------------------------------------------------------------------------
@route("GET", "/inventory")
def list_inventory_route(body):
    records = sorted(dynamo.list_inventory(500), key=lambda r: (str(r.get("aisle")), str(r.get("shelf")),
                                                                str(r.get("sku_id"))))
    return {"inventory": records, "count": len(records)}


# --- Analysis workspace ---------------------------------------------------------------------
@route("GET", "/analysis")
def analysis(body):
    """Historical / intelligence view. Ops answers "what now"; this answers "what happened".

    Every number is computed from the same orders and audit records the picker app wrote.
    """
    orders = dynamo.list_orders(200)
    audits = sorted(dynamo.list_audit(1000), key=lambda a: a.get("ts", 0))

    def evs(*names):
        return [a for a in audits if a.get("event") in names]

    picks = evs("ITEM_PICKED")
    oos = evs("ITEM_OOS")
    concurrent = evs("ITEM_CONCURRENT_PICK")
    concurrent_blocked = evs("CONCURRENT_PICK_DUPLICATE_BLOCKED")
    subs = evs("SUBSTITUTION_APPLIED")
    subs_rejected = evs("SUBSTITUTION_REJECTED")
    guardrail = evs("GUARDRAIL_REJECTED")
    manual = evs("MANUAL_RESOLUTION_REQUIRED")
    bag_pass = evs("BAG_VERIFY_VERIFIED")
    bag_fail = evs("BAG_VERIFY_FAILED")
    prod_match = evs("PRODUCT_VERIFY_MATCH")
    prod_fail = evs("PRODUCT_VERIFY_FAILED")
    dispatched = evs("DISPATCHED")

    # Pick cycle time per order: picking started -> first bag check.
    starts, ends = {}, {}
    for a in audits:
        if a.get("event") == "ORDER_PICKING_STARTED":
            starts.setdefault(a.get("order_id"), a.get("ts"))
        if str(a.get("event", "")).startswith("BAG_VERIFY_"):
            ends.setdefault(a.get("order_id"), a.get("ts"))
    cycles = [{"order_id": k, "seconds": round((ends[k] - v) / 1000, 1)}
              for k, v in starts.items() if k in ends and ends[k] >= v]
    cycle_values = [c["seconds"] for c in cycles]

    # Picker performance, from audit actors only.
    pickers = {}
    for a in audits:
        actor = a.get("actor")
        if not actor or actor in ("system", "bedrock"):
            continue
        rec = pickers.setdefault(actor, {"picker_id": actor, "picks": 0, "substitutions": 0,
                                         "exceptions": 0, "concurrent_reports": 0, "bag_failures": 0})
        event = a.get("event")
        if event == "ITEM_PICKED":
            rec["picks"] += 1
        elif event == "SUBSTITUTION_APPLIED":
            rec["substitutions"] += 1
        elif event in ("ITEM_EXCEPTION", "ITEM_OOS"):
            rec["exceptions"] += 1
        elif event in ("ITEM_CONCURRENT_PICK", "CONCURRENT_PICK_DUPLICATE_BLOCKED"):
            rec["concurrent_reports"] += 1
        elif event == "BAG_VERIFY_FAILED":
            rec["bag_failures"] += 1
    picker_rows = sorted(pickers.values(), key=lambda r: (-r["picks"], r["picker_id"]))

    # Aisle workload across every order, not just the active ones.
    aisles = {}
    for o in orders:
        for line in o.get("items", []):
            key = str(line.get("aisle") or "—")
            rec = aisles.setdefault(key, {"aisle": key, "units": 0, "lines": 0, "exceptions": 0})
            rec["units"] += int(line.get("qty", 1))
            rec["lines"] += 1
            if line.get("state") in (sm.OOS, sm.EXCEPTION, sm.CONTESTED):
                rec["exceptions"] += 1
    aisle_rows = sorted(aisles.values(), key=lambda r: (-r["units"], r["aisle"]))
    peak = max([r["units"] for r in aisle_rows] or [1])
    for row in aisle_rows:
        row["percent"] = round(row["units"] / peak * 100, 1)

    unavailability_total = len(oos) + len(concurrent)
    interpreters = {}
    for a in picks + subs:
        key = a.get("detail", {}).get("interpreter") or a.get("detail", {}).get("source") or "tap"
        interpreters[key] = interpreters.get(key, 0) + 1

    return {
        "generated_at": _now_ms(),
        # A hackathon dataset is small by construction. Say so rather than implying a month of ops.
        "dataset": {
            "orders": len(orders),
            "audit_records": len(audits),
            "is_demo_data": len(orders) <= 20,
            "label": "Seeded demo dataset" if len(orders) <= 20 else "Store dataset",
        },
        "pick_cycle": {
            "samples": len(cycle_values),
            "avg_seconds": round(sum(cycle_values) / len(cycle_values), 1) if cycle_values else None,
            "fastest_seconds": min(cycle_values) if cycle_values else None,
            "slowest_seconds": max(cycle_values) if cycle_values else None,
            "per_order": sorted(cycles, key=lambda c: c["seconds"])[:10],
        },
        "unavailability": {
            "total": unavailability_total,
            "stockout": len(oos),
            "concurrent_pick": len(concurrent),
            "duplicate_prevented": len(concurrent) + len(concurrent_blocked),
            "stockout_share": round(len(oos) / unavailability_total * 100, 1) if unavailability_total else None,
            "concurrent_share": round(len(concurrent) / unavailability_total * 100, 1) if unavailability_total else None,
        },
        "recovery": {
            "substitutions_applied": len(subs),
            "substitutions_rejected": len(subs_rejected),
            "manual_resolution_required": len(manual),
            "recovery_rate": (round(len(subs) / unavailability_total * 100, 1)
                              if unavailability_total else None),
        },
        "verification": {
            "product_attempts": len(prod_match) + len(prod_fail),
            "product_matches": len(prod_match),
            "product_match_rate": (round(len(prod_match) / (len(prod_match) + len(prod_fail)) * 100, 1)
                                   if (prod_match or prod_fail) else None),
            "bag_attempts": len(bag_pass) + len(bag_fail),
            "bag_passes": len(bag_pass),
            "bag_first_pass_rate": (round(len(bag_pass) / (len(bag_pass) + len(bag_fail)) * 100, 1)
                                    if (bag_pass or bag_fail) else None),
        },
        "ai_policy": {
            "total_decisions": len(subs) + len(subs_rejected) + len(guardrail) + len(concurrent),
            "approved": len(subs),
            "blocked": len(subs_rejected) + len(guardrail),
            "guardrail_rejections": len(guardrail),
            "interpreters": [{"source": k, "count": v} for k, v in sorted(interpreters.items())],
        },
        "throughput": {
            "orders_total": len(orders),
            "items_picked": len(picks),
            "dispatched": len(dispatched),
        },
        "aisle_workload": aisle_rows[:10],
        "picker_performance": picker_rows[:10],
        "decisions": _recent_decisions(audits, limit=25),
    }


# --- Demo control ---------------------------------------------------------------------------
@route("GET", "/demo/scenarios")
def demo_scenarios(body):
    return {"scenarios": demo.catalogue(), "main_order_id": demo.MAIN_ORDER}


@route("POST", "/demo/reset")
def demo_reset(body):
    """Restore the entire demo: inventory, orders, line states and audit records.

    The local server keeps state in memory, so a restart wipes it. This rebuilds everything
    by replaying real API calls, which means the result is deterministic and the audit trail
    is genuine rather than pre-written.
    """
    built = demo.reset_all()
    return {"reset": True, "seeded": built, "main_order_id": demo.MAIN_ORDER,
            "scenarios": demo.catalogue()}


@route("POST", "/demo/scenario")
def demo_scenario(body):
    """Rebuild one scenario from scratch and return its live order view."""
    key = str(body.get("scenario", "")).strip()
    if key not in demo.SCENARIOS:
        raise NotFound("Unknown demo scenario.", {"scenario": key[:40],
                                                  "available": demo.SCENARIO_ORDER})
    view = demo.build_scenario(key)
    return {**view, "scenario": key, "title": demo.SCENARIOS[key]["title"]}


@route("GET", "/inventory/{sku_id}")
def read_inventory(body, sku_id):
    record = dynamo.get_inventory(sku_id.upper())
    if not record:
        raise NotFound("SKU %s is not in inventory." % sku_id)
    return {"inventory": record}


@route("GET", "/health")
def health(body):
    return {"service": "darkstore-copilot", "ts": _now_ms()}
