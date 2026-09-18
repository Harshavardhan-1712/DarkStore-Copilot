"""Substitution business rules. Pure functions — DynamoDB decides truth, this decides policy."""
from decimal import Decimal

import config

OK = "OK"


def _dec(value, default="0"):
    if value is None:
        return Decimal(default)
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def evaluate_candidate(original, candidate, qty):
    """Return (accepted: bool, code: str, message: str) for one substitute SKU.

    original / candidate are inventory records straight out of DynamoDB.
    """
    if not candidate:
        return False, "UNKNOWN_SKU", "Substitute is not in the inventory table."

    if candidate.get("sku_id") == original.get("sku_id"):
        return False, "SAME_SKU", "Substitute is the same SKU as the original."

    orig_cat = (original.get("category") or "").lower()
    cand_cat = (candidate.get("category") or "").lower()

    if orig_cat in config.BLOCKED_SUBSTITUTION_CATEGORIES:
        return False, "CATEGORY_BLOCKED", "%s items cannot be substituted." % orig_cat

    if config.REQUIRE_CATEGORY_MATCH and orig_cat != cand_cat:
        return False, "CATEGORY_MISMATCH", "Substitute is in %s, original is in %s." % (cand_cat, orig_cat)

    if candidate.get("active") is False:
        return False, "INACTIVE_SKU", "Substitute is delisted."

    stock = int(_dec(candidate.get("stock_qty")))
    if stock < qty:
        return False, "INSUFFICIENT_STOCK", "Only %d on hand, order needs %d." % (stock, qty)

    orig_price = _dec(original.get("price"))
    cand_price = _dec(candidate.get("price"))
    if orig_price > 0:
        ratio = cand_price / orig_price
        if ratio > Decimal(str(config.MAX_SUBSTITUTE_PRICE_RATIO)):
            return False, "PRICE_TOO_HIGH", "Costs %.0f%% of the original." % (ratio * 100)
        if ratio < Decimal(str(config.MIN_SUBSTITUTE_PRICE_RATIO)):
            return False, "PRICE_TOO_LOW", "Costs only %.0f%% of the original." % (ratio * 100)

    return True, OK, "Eligible."


def rank_substitutes(original, candidates, qty):
    """Score eligible substitutes; closest price to the original wins, ties broken by stock."""
    accepted, rejected = [], []
    orig_price = _dec(original.get("price"))
    for cand in candidates:
        ok, code, msg = evaluate_candidate(original, cand, qty)
        record = {
            "sku_id": cand.get("sku_id") if cand else None,
            "name": (cand or {}).get("name"),
            "price": (cand or {}).get("price"),
            "stock_qty": (cand or {}).get("stock_qty"),
            "aisle": (cand or {}).get("aisle"),
            "shelf": (cand or {}).get("shelf"),
            "code": code,
            "message": msg,
        }
        (accepted if ok else rejected).append(record)

    accepted.sort(
        key=lambda c: (abs(_dec(c["price"]) - orig_price), -int(_dec(c["stock_qty"])), str(c["sku_id"]))
    )
    return accepted, rejected


def price_delta(original, substitute, qty):
    """Signed invoice change in currency units, as a Decimal."""
    return (_dec(substitute.get("price")) - _dec(original.get("price"))) * Decimal(qty)
