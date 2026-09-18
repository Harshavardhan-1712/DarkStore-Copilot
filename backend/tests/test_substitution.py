from decimal import Decimal

from substitution import evaluate_candidate, price_delta, rank_substitutes

MILK = {"sku_id": "SKU_MILK", "category": "dairy", "price": Decimal("32"), "stock_qty": 0}


def cand(sku, price, stock=10, category="dairy", **extra):
    return {"sku_id": sku, "name": sku, "category": category, "price": Decimal(str(price)),
            "stock_qty": stock, **extra}


def test_accepts_like_for_like():
    ok, code, _ = evaluate_candidate(MILK, cand("SKU_MILK_ALT", 34), 1)
    assert ok and code == "OK"


def test_rejects_out_of_stock_and_category_jump():
    assert evaluate_candidate(MILK, cand("SKU_MILK_ALT", 34, stock=0), 1)[1] == "INSUFFICIENT_STOCK"
    assert evaluate_candidate(MILK, cand("SKU_SOAP", 34, category="home"), 1)[1] == "CATEGORY_MISMATCH"


def test_rejects_price_out_of_bounds():
    assert evaluate_candidate(MILK, cand("SKU_PREMIUM", 60), 1)[1] == "PRICE_TOO_HIGH"
    assert evaluate_candidate(MILK, cand("SKU_CHEAP", 10), 1)[1] == "PRICE_TOO_LOW"


def test_rejects_blocked_category_and_delisted():
    pharma = {**MILK, "category": "pharma"}
    assert evaluate_candidate(pharma, cand("SKU_OTHER", 33, category="pharma"), 1)[1] == "CATEGORY_BLOCKED"
    assert evaluate_candidate(MILK, cand("SKU_OLD", 33, active=False), 1)[1] == "INACTIVE_SKU"


def test_rejects_missing_and_identical_sku():
    assert evaluate_candidate(MILK, None, 1)[1] == "UNKNOWN_SKU"
    assert evaluate_candidate(MILK, cand("SKU_MILK", 32), 1)[1] == "SAME_SKU"


def test_ranking_prefers_closest_price():
    accepted, rejected = rank_substitutes(
        MILK, [cand("SKU_A", 35), cand("SKU_B", 32), cand("SKU_C", 100), None], 1
    )
    assert [c["sku_id"] for c in accepted] == ["SKU_B", "SKU_A"]
    assert {r["code"] for r in rejected} == {"PRICE_TOO_HIGH", "UNKNOWN_SKU"}


def test_price_delta_scales_with_qty():
    assert price_delta(MILK, cand("SKU_B", 34), 3) == Decimal("6")
