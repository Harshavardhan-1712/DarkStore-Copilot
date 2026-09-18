import json

import pytest

from contracts import extract_json, parse_and_validate, validate_bag_verdict, validate_intent, validate_product_verdict
from errors import GuardrailRejection

ALLOWED = {"SKU_MILK", "SKU_MILK_ALT"}


def good(**over):
    base = {
        "action": "CONFIRM_PICK",
        "selected_sku": "SKU_MILK",
        "spoken_response_telugu": "\u0c38\u0c30\u0c47",
        "spoken_response_hindi": "\u0920\u0940\u0915 \u0939\u0948",
        "spoken_response_english": "Okay, picked. Next item.",
        "reason": "Picker confirmed the item.",
    }
    base.update(over)
    return base


def test_accepts_clean_intent():
    out = validate_intent(good(), ALLOWED)
    assert out["action"] == "CONFIRM_PICK" and out["selected_sku"] == "SKU_MILK"


def test_strips_markdown_fences():
    raw = "Sure!\n```json\n%s\n```" % json.dumps(good())
    assert parse_and_validate(raw, ALLOWED)["action"] == "CONFIRM_PICK"


def test_rejects_hallucinated_sku():
    with pytest.raises(GuardrailRejection):
        validate_intent(good(action="SUBSTITUTE_ITEM", selected_sku="SKU_DOES_NOT_EXIST"), ALLOWED)


def test_rejects_unknown_action_and_missing_fields():
    with pytest.raises(GuardrailRejection):
        validate_intent(good(action="DELETE_ORDER"), ALLOWED)
    broken = good()
    del broken["reason"]
    with pytest.raises(GuardrailRejection):
        validate_intent(broken, ALLOWED)


def test_rejects_extra_fields_and_empty_speech():
    with pytest.raises(GuardrailRejection):
        validate_intent({**good(), "run_sql": "drop table"}, ALLOWED)
    with pytest.raises(GuardrailRejection):
        validate_intent(good(spoken_response_telugu="  "), ALLOWED)


def test_rejects_non_json_output():
    with pytest.raises(GuardrailRejection):
        extract_json("I could not understand the picker.")


def test_exception_may_omit_sku():
    out = validate_intent(good(action="FLAG_EXCEPTION", selected_sku=""), ALLOWED)
    assert out["selected_sku"] is None


def test_bag_verdict_fails_closed_on_low_confidence():
    out = validate_bag_verdict({"verdict": "VERIFIED", "confidence": 0.2, "missing_skus": []}, {"SKU_MILK"})
    assert out["verdict"] == "FAILED"


def test_bag_verdict_drops_invented_skus():
    out = validate_bag_verdict(
        {"verdict": "FAILED", "confidence": 0.9, "missing_skus": ["SKU_MILK", "SKU_INVENTED"]}, {"SKU_MILK"}
    )
    assert out["missing_skus"] == ["SKU_MILK"]


def test_bag_verdict_cannot_pass_while_naming_missing_items():
    out = validate_bag_verdict(
        {"verdict": "VERIFIED", "confidence": 0.99, "missing_skus": ["SKU_MILK"]}, {"SKU_MILK"}
    )
    assert out["verdict"] == "FAILED"


def test_product_match_fails_closed_on_low_confidence():
    out = validate_product_verdict({"verdict": "MATCH", "confidence": 0.2, "detected_product": "Milk"}, "SKU_MILK")
    assert out["verdict"] == "UNCLEAR"

def test_product_verdict_accepts_expected_sku():
    out = validate_product_verdict({"verdict": "MATCH", "confidence": 0.94, "detected_product": "Amul Taaza Milk 500ml"}, "SKU_MILK")
    assert out["expected_sku"] == "SKU_MILK"
