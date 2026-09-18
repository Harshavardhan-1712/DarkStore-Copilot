"""End-to-end flow tests against the in-memory adapters.

Importing local_server swaps DynamoDB for dicts and Bedrock for the local stand-in, so these
exercise the real handler, the real guardrails and the real state machine — only the two
outbound adapters are substituted. Every assertion here is about backend authority: what the
AI is and is not allowed to change.
"""
import base64

import pytest

import demo
import handler
import local_server as ls
import state_machine as sm
from errors import Conflict, NotFound, PolicyViolation

PHOTO = base64.b64encode(b"x" * 64).decode()
MILK = "SKU_MILK_AMUL_500"


@pytest.fixture(autouse=True)
def fresh_store():
    ls._reset_store()
    yield


def order_view(order_id):
    return handler.read_order({}, order_id)


def seeded(key):
    return demo.build_scenario(key)


def audit_events(order_id):
    return [a["event"] for a in ls.AUDIT if a["order_id"] == order_id]


def new_order(items, order_id="ORD-T1", start=True):
    view = handler.create_order({"order_id": order_id, "picker_id": "PICKER-T", "items": items})
    if start:
        view = handler.start_picking({"picker_id": "PICKER-T"}, order_id)
    return view


# --- normal pick -----------------------------------------------------------------------------
def test_normal_pick_decrements_stock_and_audits():
    view = new_order([{"sku_id": "SKU_CHIPS_LAYS", "qty": 2}])
    before = ls.INV["SKU_CHIPS_LAYS"]["stock_qty"]
    view = handler.confirm_pick({"index": 0, "sku_id": "SKU_CHIPS_LAYS"}, "ORD-T1")
    assert view["order"]["items"][0]["state"] == sm.PICKED
    assert ls.INV["SKU_CHIPS_LAYS"]["stock_qty"] == before - 2
    assert "ITEM_PICKED" in audit_events("ORD-T1")
    assert view["current_index"] is None


def test_pick_is_refused_before_picking_starts():
    new_order([{"sku_id": "SKU_CHIPS_LAYS", "qty": 1}], start=False)
    with pytest.raises(Conflict):
        handler.confirm_pick({"index": 0}, "ORD-T1")


def test_unknown_sku_cannot_enter_an_order():
    with pytest.raises(NotFound):
        handler.create_order({"order_id": "ORD-T9", "items": [{"sku_id": "SKU_GHOST", "qty": 1}]})


# --- missing item / stockout ------------------------------------------------------------------
def test_missing_item_flags_oos_and_offers_only_policy_approved_swaps():
    view = new_order([{"sku_id": MILK, "qty": 2}])
    res = handler.flag_exception({"index": 0, "sku_id": MILK, "kind": "OOS"}, "ORD-T1")
    assert res["order"]["items"][0]["state"] == sm.OOS
    assert "ITEM_OOS" in audit_events("ORD-T1")
    eligible = {c["sku_id"] for c in res["eligible_substitutes"]}
    rejected = {r["sku_id"]: r["code"] for r in res["rejected_substitutes"]}
    assert "SKU_MILK_NANDINI_500" in eligible
    # Amul 1L is twice the price of the 500ml line: policy refuses it, so it is never offered.
    assert rejected["SKU_MILK_AMUL_1L"] == "PRICE_TOO_HIGH"


def test_substitution_commits_stock_and_invoice_together():
    view = new_order([{"sku_id": MILK, "qty": 2}])
    handler.flag_exception({"index": 0, "sku_id": MILK, "kind": "OOS"}, "ORD-T1")
    before_stock = ls.INV["SKU_MILK_NANDINI_500"]["stock_qty"]
    before_total = ls.ORDERS["ORD-T1"]["invoice_total"]
    res = handler.apply_substitute({"index": 0, "substitute_sku": "SKU_MILK_NANDINI_500"}, "ORD-T1")
    line = res["order"]["items"][0]
    assert line["state"] == sm.SUBSTITUTED and line["sku_id"] == "SKU_MILK_NANDINI_500"
    assert line["substituted_from"] == MILK
    assert ls.INV["SKU_MILK_NANDINI_500"]["stock_qty"] == before_stock - 2
    assert ls.ORDERS["ORD-T1"]["invoice_total"] == before_total - 2   # 31 vs 32, two units
    assert "SUBSTITUTION_APPLIED" in audit_events("ORD-T1")


def test_invalid_substitution_is_refused_and_audited():
    new_order([{"sku_id": MILK, "qty": 1}])
    handler.flag_exception({"index": 0, "sku_id": MILK, "kind": "OOS"}, "ORD-T1")
    with pytest.raises(PolicyViolation):
        handler.apply_substitute({"index": 0, "substitute_sku": "SKU_CHIPS_LAYS"}, "ORD-T1")
    assert "SUBSTITUTION_REJECTED" in audit_events("ORD-T1")
    assert ls.ORDERS["ORD-T1"]["items"][0]["sku_id"] == MILK   # untouched


def test_pharma_is_never_auto_substituted():
    new_order([{"sku_id": "SKU_PARA_500", "qty": 1}])
    res = handler.flag_exception({"index": 0, "sku_id": "SKU_PARA_500", "kind": "OOS"}, "ORD-T1")
    assert res["eligible_substitutes"] == []
    assert {r["code"] for r in res["rejected_substitutes"]} == {"CATEGORY_BLOCKED"}


# --- concurrent pick / ITEM_ALREADY_TAKEN ----------------------------------------------------
def test_concurrent_pick_is_not_treated_as_a_stockout():
    view = new_order([{"sku_id": MILK, "qty": 2}])
    res = handler.voice_intent({"utterance": "someone already took this", "lang": "en"}, "ORD-T1")
    assert res["executed"] == "ITEM_ALREADY_TAKEN"
    assert res["policy"] == "CONCURRENT_PICK"
    # A different line state from OOS, and a different audit event, so ops can tell them apart.
    assert res["order"]["items"][0]["state"] == sm.CONTESTED
    events = audit_events("ORD-T1")
    assert "ITEM_CONCURRENT_PICK" in events
    assert "ITEM_OOS" not in events


def test_concurrent_pick_never_lets_the_model_claim_the_item_was_picked():
    new_order([{"sku_id": MILK, "qty": 2}])
    before = ls.INV[MILK]["stock_qty"]
    handler.voice_intent({"utterance": "evaro teesukunnaru", "lang": "te"}, "ORD-T1")
    assert ls.INV[MILK]["stock_qty"] == before            # no stock movement
    assert ls.ORDERS["ORD-T1"]["items"][0]["state"] != sm.PICKED


def test_concurrent_pick_offers_an_approved_substitute():
    new_order([{"sku_id": MILK, "qty": 2}])
    res = handler.voice_intent({"utterance": "kisi ne le liya", "lang": "hi"}, "ORD-T1")
    assert res["concurrent_pick"]["needs_manual_resolution"] is False
    assert res["eligible_substitutes"][0]["sku_id"] == "SKU_MILK_NANDINI_500"


def test_concurrent_pick_with_no_substitute_is_flagged_for_manual_resolution():
    new_order([{"sku_id": "SKU_ATTA_5KG", "qty": 1}])   # no registered substitutes
    res = handler.voice_intent({"utterance": "someone took it", "lang": "en"}, "ORD-T1")
    assert res["concurrent_pick"]["needs_manual_resolution"] is True
    assert "MANUAL_RESOLUTION_REQUIRED" in audit_events("ORD-T1")


def test_contested_line_can_then_be_substituted():
    new_order([{"sku_id": MILK, "qty": 1}])
    handler.voice_intent({"utterance": "someone took it", "lang": "en"}, "ORD-T1")
    res = handler.apply_substitute({"index": 0, "substitute_sku": "SKU_MILK_NANDINI_500"}, "ORD-T1")
    assert res["order"]["items"][0]["state"] == sm.SUBSTITUTED


def test_repeat_concurrent_report_on_a_resolved_line_changes_nothing():
    new_order([{"sku_id": "SKU_CHIPS_LAYS", "qty": 1}, {"sku_id": "SKU_ATTA_5KG", "qty": 1}])
    handler.confirm_pick({"index": 0}, "ORD-T1")
    # Index 0 is already PICKED; a late voice report must not undo it.
    res = handler.voice_intent({"utterance": "someone took it", "index": 0, "lang": "en"}, "ORD-T1")
    assert res["executed"] == "CONCURRENT_PICK_NO_CHANGE"
    assert res["order"]["items"][0]["state"] == sm.PICKED
    assert "CONCURRENT_PICK_DUPLICATE_BLOCKED" in audit_events("ORD-T1")


# --- idempotency ------------------------------------------------------------------------------
def test_duplicate_client_action_id_does_not_pick_twice():
    new_order([{"sku_id": "SKU_CHIPS_LAYS", "qty": 2}])
    before = ls.INV["SKU_CHIPS_LAYS"]["stock_qty"]
    handler.confirm_pick({"index": 0, "client_action_id": "PICKER-T-voice-1"}, "ORD-T1")
    after_first = ls.INV["SKU_CHIPS_LAYS"]["stock_qty"]
    res = handler.confirm_pick({"index": 0, "client_action_id": "PICKER-T-voice-1"}, "ORD-T1")
    assert after_first == before - 2
    assert ls.INV["SKU_CHIPS_LAYS"]["stock_qty"] == after_first   # not decremented again
    assert res["executed"] == "DUPLICATE_IGNORED"


def test_duplicate_voice_action_id_is_replayed_not_re_executed():
    new_order([{"sku_id": "SKU_CHIPS_LAYS", "qty": 1}, {"sku_id": "SKU_ATTA_5KG", "qty": 1}])
    first = handler.voice_intent({"utterance": "picked", "client_action_id": "v-1"}, "ORD-T1")
    assert first["executed"] == "CONFIRM_PICK"
    again = handler.voice_intent({"utterance": "picked", "client_action_id": "v-1"}, "ORD-T1")
    assert again["executed"] == "DUPLICATE_IGNORED" and again["idempotent_replay"] is True
    assert sum(1 for e in audit_events("ORD-T1") if e == "ITEM_PICKED") == 1


def test_mutating_voice_audit_records_carry_the_action_id():
    new_order([{"sku_id": "SKU_CHIPS_LAYS", "qty": 1}])
    handler.voice_intent({"utterance": "got it", "client_action_id": "v-trace"}, "ORD-T1")
    picked = [a for a in ls.AUDIT if a["event"] == "ITEM_PICKED"]
    assert picked[-1]["detail"]["client_action_id"] == "v-trace"


# --- voice fast paths -------------------------------------------------------------------------
def test_telugu_fast_path_confirms_without_calling_the_model():
    new_order([{"sku_id": "SKU_CHIPS_LAYS", "qty": 1}])
    res = handler.voice_intent({"utterance": "teesukunnanu", "lang": "te"}, "ORD-T1")
    assert res["executed"] == "CONFIRM_PICK"
    assert res["interpretation"]["interpreter"] == "deterministic-fast-path"
    assert res["intent"]["spoken_response_telugu"]


def test_hindi_fast_path_reports_unavailability_and_proposes_a_swap():
    new_order([{"sku_id": MILK, "qty": 1}])
    res = handler.voice_intent({"utterance": "nahi hai", "lang": "hi"}, "ORD-T1")
    assert res["executed"] == "SUBSTITUTE_ITEM"
    assert res["order"]["items"][0]["sku_id"] == "SKU_MILK_NANDINI_500"
    assert res["intent"]["spoken_response_hindi"]


def test_ambiguous_speech_falls_through_to_the_model_adapter():
    new_order([{"sku_id": "SKU_CHIPS_LAYS", "qty": 1}])
    res = handler.voice_intent({"utterance": "hmm what about this thing", "lang": "en"}, "ORD-T1")
    assert res["interpretation"]["interpreter"] == "amazon-bedrock"
    assert res["executed"] == "FLAG_EXCEPTION"      # stand-in declines rather than guessing


def test_empty_utterance_is_rejected():
    new_order([{"sku_id": "SKU_CHIPS_LAYS", "qty": 1}])
    with pytest.raises(Exception):
        handler.voice_intent({"utterance": "   "}, "ORD-T1")


# --- guardrails -------------------------------------------------------------------------------
def test_hallucinated_sku_is_rejected_and_order_state_is_unchanged():
    new_order([{"sku_id": MILK, "qty": 1}])
    res = handler.voice_intent({
        "utterance": "replace this with SKU-TOTALLY-MADE-UP",
        "simulated_intent": {
            "action": "SUBSTITUTE_ITEM", "selected_sku": "SKU-TOTALLY-MADE-UP",
            "spoken_response_telugu": "a", "spoken_response_hindi": "b",
            "spoken_response_english": "c", "reason": "hallucination",
        }}, "ORD-T1")
    assert res["intent"].get("fallback") is True
    assert "GUARDRAIL_REJECTED" in audit_events("ORD-T1")
    assert ls.ORDERS["ORD-T1"]["items"][0]["sku_id"] == MILK
    assert any(step["ok"] is False for step in res["policy_chain"])


def test_policy_chain_explains_an_approved_action():
    new_order([{"sku_id": "SKU_CHIPS_LAYS", "qty": 1}])
    res = handler.voice_intent({"utterance": "picked"}, "ORD-T1")
    labels = [s["label"] for s in res["policy_chain"]]
    assert "You said" in labels and "AI understands" in labels and "Action" in labels


# --- verification -----------------------------------------------------------------------------
def test_product_visual_verification_does_not_mutate_the_line():
    new_order([{"sku_id": "SKU_OIL_SUN_1L", "qty": 1}])
    res = handler.verify_item({"index": 0, "sku_id": "SKU_OIL_SUN_1L",
                               "image_base64": PHOTO, "image_format": "jpeg"}, "ORD-T1")
    assert res["product_verification"]["verdict"] == "MATCH"
    assert ls.ORDERS["ORD-T1"]["items"][0]["state"] == sm.AVAILABLE
    assert "PRODUCT_VERIFY_MATCH" in audit_events("ORD-T1")


def test_product_verification_rejects_a_bad_payload():
    new_order([{"sku_id": "SKU_OIL_SUN_1L", "qty": 1}])
    with pytest.raises(Exception):
        handler.verify_item({"index": 0, "image_base64": "", "image_format": "jpeg"}, "ORD-T1")
    with pytest.raises(Exception):
        handler.verify_item({"index": 0, "image_base64": PHOTO, "image_format": "gif"}, "ORD-T1")


def test_bag_check_requires_every_line_to_be_finished():
    new_order([{"sku_id": "SKU_CHIPS_LAYS", "qty": 1}, {"sku_id": "SKU_ATTA_5KG", "qty": 1}])
    handler.confirm_pick({"index": 0}, "ORD-T1")
    with pytest.raises(Conflict):
        handler.verify_bag({"image_base64": PHOTO, "image_format": "jpeg"}, "ORD-T1")


def test_bag_verification_fails_then_passes_on_retry():
    new_order([{"sku_id": "SKU_CHIPS_LAYS", "qty": 1}, {"sku_id": "SKU_ATTA_5KG", "qty": 1}])
    handler.confirm_pick({"index": 0}, "ORD-T1")
    handler.confirm_pick({"index": 1}, "ORD-T1")

    first = handler.verify_bag({"image_base64": PHOTO, "image_format": "jpeg"}, "ORD-T1")
    assert first["verification"]["verdict"] == "FAILED"
    assert first["verification"]["missing_items"]           # names the missing line
    assert first["verification"]["expected_units"] == 2
    assert first["verification"]["detected_units"] == 1
    assert "handover" not in first
    assert first["order"]["status"] == sm.BAG_VERIFICATION

    second = handler.verify_bag({"image_base64": PHOTO, "image_format": "jpeg"}, "ORD-T1")
    assert second["verification"]["verdict"] == "VERIFIED"
    assert second["handover"]["token"]
    assert second["order"]["status"] == sm.READY_FOR_PICKUP


def test_dispatch_requires_a_matching_handover_token():
    new_order([{"sku_id": "SKU_CHIPS_LAYS", "qty": 1}])
    handler.confirm_pick({"index": 0}, "ORD-T1")
    handler.verify_bag({"image_base64": PHOTO, "image_format": "jpeg"}, "ORD-T1")
    res = handler.verify_bag({"image_base64": PHOTO, "image_format": "jpeg"}, "ORD-T1")
    with pytest.raises(PolicyViolation):
        handler.dispatch({"handover_token": "forged"}, "ORD-T1")
    assert ls.ORDERS["ORD-T1"]["status"] == sm.READY_FOR_PICKUP
    done = handler.dispatch({"handover_token": res["handover"]["token"], "rider_id": "R-1"}, "ORD-T1")
    assert done["order"]["status"] == sm.DISPATCHED


def test_dispatch_is_impossible_before_verification():
    new_order([{"sku_id": "SKU_CHIPS_LAYS", "qty": 1}])
    with pytest.raises(Conflict):
        handler.dispatch({"handover_token": "anything"}, "ORD-T1")


# --- demo scenarios and reset -----------------------------------------------------------------
def test_demo_reset_builds_every_scenario_deterministically():
    first = demo.reset_all()
    assert len(first) == len(demo.SCENARIO_ORDER) == 9
    statuses = {row["order_id"]: row["status"] for row in first}
    assert statuses["ORD-DEMO-105"] == sm.READY_FOR_PICKUP
    assert statuses["ORD-DEMO-106"] == sm.DISPATCHED
    assert statuses["ORD-DEMO-104"] == sm.BAG_VERIFICATION

    second = demo.reset_all()
    assert [r["open_index"] for r in first] == [r["open_index"] for r in second]


def test_main_demo_order_is_parked_on_the_contested_dairy_line():
    demo.reset_all()
    view = order_view(demo.MAIN_ORDER)
    assert len(view["order"]["items"]) == 10
    assert view["current_item"]["sku_id"] == MILK
    assert view["current_item"]["qty"] == 2


def test_seeded_orders_carry_real_audit_trails():
    demo.reset_all()
    assert "SUBSTITUTION_APPLIED" not in audit_events("ORD-DEMO-102")
    assert "ITEM_OOS" in audit_events("ORD-DEMO-102")
    assert "GUARDRAIL_REJECTED" in audit_events("ORD-DEMO-108")
    assert "PRODUCT_VERIFY_MATCH" in audit_events("ORD-DEMO-103")
    assert "BAG_VERIFY_FAILED" in audit_events("ORD-DEMO-104")
    assert "DISPATCHED" in audit_events("ORD-DEMO-106")


def test_single_scenario_rebuild_is_isolated():
    demo.reset_all()
    open_index = order_view("ORD-DEMO-107")["current_index"]
    handler.confirm_pick({"index": open_index}, "ORD-DEMO-107")
    rebuilt = demo.build_scenario("voice_picking")
    assert rebuilt["order"]["order_id"] == "ORD-DEMO-107"
    assert order_view(demo.MAIN_ORDER)["current_item"]["sku_id"] == MILK   # untouched


# --- dashboard and analysis -------------------------------------------------------------------
def test_dashboard_separates_stockout_from_concurrent_pick():
    demo.reset_all()
    handler.voice_intent({"utterance": "evaro teesukunnaru", "lang": "te"}, "ORD-DEMO-109")
    data = handler.dashboard({})
    assert data["metrics"]["concurrent_pick_events"] >= 1
    assert data["metrics"]["oos_events"] >= 1
    kinds = {row["kind"] for row in data["live_exceptions"]}
    assert "CONCURRENT_PICK" in kinds and "STOCKOUT" in kinds
    assert data["recent_decisions"]


def test_analysis_attributes_unavailability_to_the_right_cause():
    demo.reset_all()
    handler.voice_intent({"utterance": "someone took it", "lang": "en"}, "ORD-DEMO-109")
    out = handler.analysis({})
    assert out["unavailability"]["concurrent_pick"] >= 1
    assert out["unavailability"]["stockout"] >= 1
    assert out["unavailability"]["total"] == (out["unavailability"]["stockout"]
                                              + out["unavailability"]["concurrent_pick"])
    assert out["dataset"]["is_demo_data"] is True
    assert out["picker_performance"]


def test_order_list_reports_exception_states():
    demo.reset_all()
    handler.voice_intent({"utterance": "someone took it", "lang": "en"}, "ORD-DEMO-109")
    rows = {r["order_id"]: r for r in handler.list_orders_route({})["orders"]}
    assert "CONTESTED" in rows["ORD-DEMO-109"]["exception_states"]
    assert "OOS" in rows["ORD-DEMO-102"]["exception_states"]
    assert rows[demo.MAIN_ORDER]["items_total"] == 10


def test_inventory_listing_is_available_without_aws():
    rows = handler.list_inventory_route({})
    assert rows["count"] == len(ls.INV)


def test_health_and_unknown_scenario():
    assert handler.health({})["service"] == "darkstore-copilot"
    with pytest.raises(NotFound):
        handler.demo_scenario({"scenario": "does-not-exist"})
