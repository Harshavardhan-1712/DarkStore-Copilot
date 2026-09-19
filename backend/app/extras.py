"""Front door for the voice-first extras: the phantom-stock loop and the batch planner.

    extras.lambda_handler(event, context)

answers the new routes itself and hands every other request, untouched, to handler.lambda_handler.
Point the Lambda entry at this function (or call it from local_server.py) and handler.py, the
state machine and the existing routes stay exactly as they are.

    GET  /phantom-stock          recount queue (SKUs pickers found empty that the system shows in stock)
    POST /phantom-stock/report   log one shelf-empty observation
    POST /phantom-stock/reset    clear signals (in-memory store only, for demo resets)
    POST /batches/plan           read-only walk plan across open orders
"""
import base64
import json
import logging
import re
from decimal import Decimal

import batch_plan
import handler
import phantom_stock

_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
}
_ROUTE = re.compile(r"/(phantom-stock|batches)(?:/([a-z-]+))?/?$")


def _json_default(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return str(value)


def _reply(status, payload):
    body = "" if payload is None else json.dumps(payload, default=_json_default)
    return {"statusCode": status, "headers": dict(_HEADERS), "body": body}


def _method(event):
    http = (event.get("requestContext") or {}).get("http") or {}
    return str(event.get("httpMethod") or http.get("method") or "GET").upper()


def _path(event):
    return event.get("path") or event.get("rawPath") or ""


def _body(event):
    raw = event.get("body")
    if not raw:
        return {}
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    try:
        data = json.loads(raw)
    except ValueError:
        raise phantom_stock.SignalError("Request body must be JSON.")
    if not isinstance(data, dict):
        raise phantom_stock.SignalError("Request body must be a JSON object.")
    return data


def _status_for(exc):
    """Our own errors carry .status; the existing app's errors are matched by name."""
    status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    return {"NotFound": 404, "Conflict": 409}.get(type(exc).__name__)


def _dispatch(method, resource, action, event):
    if resource == "phantom-stock":
        if method == "GET" and action is None:
            return phantom_stock.build_queue()
        if method == "POST" and action == "report":
            data = _body(event)
            return phantom_stock.record_report(
                data.get("sku_id"),
                data.get("lines"),
                source=data.get("source", "button"),
                picker_id=data.get("picker_id", ""),
                reason=data.get("reason", ""),
            )
        if method == "POST" and action == "reset":
            if phantom_stock.get_store().durable:
                raise phantom_stock.SignalError("Reset is only available for the in-memory store.")
            phantom_stock.clear()
            return {"cleared": True}
    if resource == "batches" and method == "POST" and action == "plan":
        data = _body(event)
        return batch_plan.build_plan(data.get("order_ids"), data.get("max_orders", batch_plan.DEFAULT_MAX_ORDERS))
    return None


def lambda_handler(event, context):
    match = _ROUTE.search(_path(event))
    if not match:
        return handler.lambda_handler(event, context)

    method = _method(event)
    if method == "OPTIONS":
        return _reply(204, None)

    try:
        result = _dispatch(method, match.group(1), match.group(2), event)
    except Exception as exc:  # noqa: BLE001 - every failure becomes a JSON error, never a stack trace
        status = _status_for(exc)
        if status is None:
            logging.getLogger(__name__).exception("extras route failed")
            return _reply(500, {"error": "Something went wrong on the store service."})
        return _reply(status, {"error": str(exc)})
    if result is None:
        return _reply(404, {"error": "Not found."})
    return _reply(200, result)
