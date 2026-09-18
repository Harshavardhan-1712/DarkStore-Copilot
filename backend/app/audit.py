"""Append-only audit trail. Every non-deterministic decision leaves a record."""
import time
import uuid


def make_entry(order_id, event, actor, detail=None):
    return {
        "audit_id": "AUD-%s" % uuid.uuid4().hex[:12],
        "order_id": order_id,
        "ts": int(time.time() * 1000),
        "event": event,          # e.g. SUBSTITUTION_APPLIED, GUARDRAIL_REJECTED, BAG_VERIFY_FAILED
        "actor": actor,          # picker id, "bedrock", or "system"
        "detail": detail or {},
    }
