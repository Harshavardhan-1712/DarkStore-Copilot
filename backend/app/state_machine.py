"""Order and line-item state machines. Transitions are the only way state changes."""
from errors import Conflict

# --- Order level ---------------------------------------------------------------------------
CREATED = "CREATED"
PICKING = "PICKING"
BAG_VERIFICATION = "BAG_VERIFICATION"
READY_FOR_PICKUP = "READY_FOR_PICKUP"
DISPATCHED = "DISPATCHED"
CANCELLED = "CANCELLED"

ORDER_TRANSITIONS = {
    CREATED: {PICKING, CANCELLED},
    PICKING: {PICKING, BAG_VERIFICATION, CANCELLED},
    # A failed bag check keeps the order in BAG_VERIFICATION; a picker may also be sent back
    # to PICKING if verification finds a missing line.
    BAG_VERIFICATION: {BAG_VERIFICATION, PICKING, READY_FOR_PICKUP, CANCELLED},
    READY_FOR_PICKUP: {DISPATCHED, BAG_VERIFICATION, CANCELLED},
    DISPATCHED: set(),
    CANCELLED: set(),
}

# --- Line-item level ----------------------------------------------------------------------
AVAILABLE = "AVAILABLE"
PICKED = "PICKED"
OOS = "OOS"
SUBSTITUTED = "SUBSTITUTED"
EXCEPTION = "EXCEPTION"
REMOVED = "REMOVED"
# A line another picker has already taken. Deliberately NOT the same state as OOS: the store
# has stock somewhere, this order's unit is gone, and operations needs to tell the two apart.
CONTESTED = "CONTESTED"

ITEM_TRANSITIONS = {
    AVAILABLE: {PICKED, OOS, EXCEPTION, REMOVED, CONTESTED},
    OOS: {SUBSTITUTED, EXCEPTION, REMOVED, AVAILABLE, CONTESTED},
    EXCEPTION: {SUBSTITUTED, PICKED, REMOVED, AVAILABLE, CONTESTED},
    CONTESTED: {SUBSTITUTED, EXCEPTION, REMOVED, AVAILABLE, PICKED},
    SUBSTITUTED: {PICKED, EXCEPTION},
    PICKED: {EXCEPTION},
    REMOVED: set(),
}

TERMINAL_ITEM_STATES = {PICKED, SUBSTITUTED, REMOVED}


def assert_order_transition(current, target):
    if target not in ORDER_TRANSITIONS.get(current, set()):
        raise Conflict(
            "Order cannot move from %s to %s." % (current, target),
            {"from": current, "to": target},
        )
    return target


def assert_item_transition(current, target):
    if target not in ITEM_TRANSITIONS.get(current, set()):
        raise Conflict(
            "Item cannot move from %s to %s." % (current, target),
            {"from": current, "to": target},
        )
    return target


def order_is_complete(items):
    """Every line has reached a terminal state, so the bag can be checked."""
    return bool(items) and all(i.get("state") in TERMINAL_ITEM_STATES for i in items)
