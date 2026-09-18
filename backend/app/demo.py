"""Deterministic demo state.

Every scenario below is built by calling the *real* handler routes in sequence — the same
functions the PWA calls. Nothing is hand-written into the order records, so each seeded order
carries genuine line states, a genuine invoice total and a genuine audit trail. That is the
whole point: the control tower numbers a judge sees are computed from this, not typed in.

Scenarios are rebuilt by POST /demo/reset (all of them) or POST /demo/scenario (one of them).
"""
import base64

# The image payload the seeding script hands to the verification routes. The routes validate
# that a photo arrived and is within size limits; the local adapter then returns a
# deterministic verdict. On AWS the same route sends real bytes to Bedrock.
DEMO_PHOTO = base64.b64encode(b"darkstore-demo-bag-photo").decode("ascii")

MAIN_ORDER = "ORD-DEMO-101"

# --- Scenario definitions -------------------------------------------------------------------
# items:  the order lines, in customer-basket order (routing reorders them for the picker)
# script: the sequence of real API calls that moves the order into its demo state
SCENARIOS = {
    "voice_picking": {
        "order_id": "ORD-DEMO-107",
        "title": "Multilingual voice picking",
        "summary": "Picker confirms items by speaking Telugu, Hindi or English.",
        "picker_id": "PICKER-11",
        "customer_ref": "Flat 21, Lake View Residency",
        "items": [
            {"sku_id": "SKU_RICE_SONA_5KG", "qty": 1},
            {"sku_id": "SKU_CURD_400", "qty": 2},
            {"sku_id": "SKU_CHIPS_LAYS", "qty": 2},
            {"sku_id": "SKU_BISCUIT_PARLE", "qty": 1},
        ],
        "script": [
            {"start": True},
            {"voice": "teesukunnanu", "lang": "te"},
        ],
    },
    "main": {
        "order_id": MAIN_ORDER,
        "title": "Active picking — full demo order",
        "summary": "Ten-line basket parked one step before the contested dairy line.",
        "picker_id": "PICKER-07",
        "customer_ref": "Flat 402, Sai Enclave",
        "items": [
            {"sku_id": "SKU_RICE_SONA_5KG", "qty": 1},
            {"sku_id": "SKU_MILK_AMUL_500", "qty": 2},
            {"sku_id": "SKU_CHIPS_LAYS", "qty": 2},
            {"sku_id": "SKU_OIL_SUN_1L", "qty": 1},
            {"sku_id": "SKU_CURD_400", "qty": 1},
            {"sku_id": "SKU_BISCUIT_PARLE", "qty": 1},
            {"sku_id": "SKU_ATTA_5KG", "qty": 1},
            {"sku_id": "SKU_RICE_BPT_5KG", "qty": 1},
            {"sku_id": "SKU_OIL_RICE_1L", "qty": 1},
            {"sku_id": "SKU_BISCUIT_MARIE", "qty": 1},
        ],
        # Five lines picked by tap, then one by voice, which leaves the open line on
        # Amul Milk 500ml — the line the whole judge story runs through.
        "script": [
            {"start": True},
            {"pick_until": "SKU_MILK_AMUL_500", "voice_last": True},
        ],
    },
    "stockout": {
        "order_id": "ORD-DEMO-102",
        "title": "Stockout → policy-approved substitution",
        "summary": "Amul Milk is out of stock; Nandini passed category, stock and price rules.",
        "picker_id": "PICKER-03",
        "customer_ref": "Villa 8, Green Meadows",
        "items": [
            {"sku_id": "SKU_MILK_AMUL_500", "qty": 2},
            {"sku_id": "SKU_ATTA_5KG", "qty": 1},
            {"sku_id": "SKU_CHIPS_LAYS", "qty": 1},
        ],
        "script": [
            {"start": True},
            {"pick_until": "SKU_MILK_AMUL_500"},
            {"oos": "SKU_MILK_AMUL_500"},
        ],
    },
    "product_verify": {
        "order_id": "ORD-DEMO-103",
        "title": "Product visual verification",
        "summary": "Picker photographs the product; the visual layer checks it against the SKU.",
        "picker_id": "PICKER-05",
        "customer_ref": "Flat 1104, Orchid Towers",
        "items": [
            {"sku_id": "SKU_OIL_SUN_1L", "qty": 1},
            {"sku_id": "SKU_BISCUIT_PARLE", "qty": 2},
            {"sku_id": "SKU_CURD_DODLA_400", "qty": 1},
        ],
        "script": [
            {"start": True},
            {"verify_item": True},
        ],
    },
    "bag_failed": {
        "order_id": "ORD-DEMO-104",
        "title": "Bag verification failed",
        "summary": "All lines picked, but the bag photo is one item short. Retry is required.",
        "picker_id": "PICKER-02",
        "customer_ref": "Flat 7B, Nallagandla Heights",
        "items": [
            {"sku_id": "SKU_CHIPS_LAYS", "qty": 2},
            {"sku_id": "SKU_CURD_400", "qty": 1},
            {"sku_id": "SKU_BISCUIT_MARIE", "qty": 1},
            {"sku_id": "SKU_OIL_SUN_1L", "qty": 1},
        ],
        "script": [
            {"start": True},
            {"pick": "all"},
            {"verify_bag": 1},
        ],
    },
    "ready_for_rider": {
        "order_id": "ORD-DEMO-105",
        "title": "Ready for rider",
        "summary": "Bag verified on the second attempt; handover QR issued by the backend.",
        "picker_id": "PICKER-04",
        "customer_ref": "Flat 302, Kondapur Grand",
        "items": [
            {"sku_id": "SKU_RICE_BPT_5KG", "qty": 1},
            {"sku_id": "SKU_OIL_RICE_1L", "qty": 1},
            {"sku_id": "SKU_CURD_DODLA_400", "qty": 2},
        ],
        "script": [
            {"start": True},
            {"pick": "all"},
            {"verify_bag": 2},
        ],
    },
    "dispatched": {
        "order_id": "ORD-DEMO-106",
        "title": "Dispatched",
        "summary": "Completed order handed to a rider against a matching handover token.",
        "picker_id": "PICKER-04",
        "customer_ref": "Flat 9, Gachibowli Springs",
        "items": [
            {"sku_id": "SKU_ATTA_5KG", "qty": 1},
            {"sku_id": "SKU_BISCUIT_PARLE", "qty": 1},
            {"sku_id": "SKU_CHIPS_LAYS", "qty": 3},
        ],
        "script": [
            {"start": True},
            {"pick": "all"},
            {"verify_bag": 2},
            {"dispatch": "RIDER-21"},
        ],
    },
    "guardrail": {
        "order_id": "ORD-DEMO-108",
        "title": "Guardrail rejection — invalid AI proposal",
        "summary": "The model named a SKU that does not exist. Policy refused; state unchanged.",
        "picker_id": "PICKER-09",
        "customer_ref": "Flat 604, Miyapur Central",
        "items": [
            {"sku_id": "SKU_MILK_AMUL_500", "qty": 1},
            {"sku_id": "SKU_CHIPS_LAYS", "qty": 1},
        ],
        "script": [
            {"start": True},
            {
                "simulated": {
                    "action": "SUBSTITUTE_ITEM",
                    "selected_sku": "SKU-TOTALLY-MADE-UP",
                    "spoken_response_telugu": "\u0c07\u0c26\u0c3f \u0c24\u0c40\u0c38\u0c41\u0c15\u0c4b\u0c02\u0c21\u0c3f.",
                    "spoken_response_hindi": "\u092f\u0939 \u0932\u0940\u091c\u093f\u092f\u0947\u0964",
                    "spoken_response_english": "Take this one instead.",
                    "reason": "Hallucinated SKU the backend must refuse.",
                },
                "utterance": "replace this with SKU-TOTALLY-MADE-UP",
            },
        ],
    },
    "concurrent_pick": {
        "order_id": "ORD-DEMO-109",
        "title": "Concurrent pick — item already taken",
        "summary": "Another picker took this order's unit. Parked ready for the voice report.",
        "picker_id": "PICKER-06",
        "customer_ref": "Flat 12, Kokapet Rise",
        "items": [
            {"sku_id": "SKU_ATTA_5KG", "qty": 1},
            {"sku_id": "SKU_MILK_AMUL_500", "qty": 2},
            {"sku_id": "SKU_BISCUIT_MARIE", "qty": 1},
            {"sku_id": "SKU_CHIPS_LAYS", "qty": 1},
        ],
        # Left on the open Amul Milk line: the picker says "evaro teesukunnaru" live on stage
        # and the ITEM_ALREADY_TAKEN -> CONCURRENT_PICK chain runs for real.
        "script": [
            {"start": True},
            {"pick_until": "SKU_MILK_AMUL_500"},
        ],
    },
}

# Order the control centre shows them in.
SCENARIO_ORDER = [
    "main", "voice_picking", "stockout", "concurrent_pick", "product_verify",
    "guardrail", "bag_failed", "ready_for_rider", "dispatched",
]


def _open_index(view):
    return view.get("current_index")


def _index_of(order, sku_id):
    for i, line in enumerate(order.get("items", [])):
        if line.get("sku_id") == sku_id:
            return i
    return None


def build_scenario(key):
    """Run one scenario's script through the real routes. Returns the resulting order view."""
    import handler  # imported late: handler imports this module

    spec = SCENARIOS[key]
    order_id = spec["order_id"]
    view = handler.create_order(
        {
            "order_id": order_id,
            "picker_id": spec["picker_id"],
            "customer_ref": spec["customer_ref"],
            "items": spec["items"],
            "demo_scenario": key,
        }
    )

    for step in spec["script"]:
        if step.get("start"):
            view = handler.start_picking({"picker_id": spec["picker_id"]}, order_id)

        if "pick_until" in step:
            # Walk the deterministic route, picking each open line until the named SKU is the
            # open line. voice_last routes the final pick through /intent so the seeded audit
            # trail contains a genuine voice-driven pick, not only taps.
            target = step["pick_until"]
            guard = 0
            while guard < 40:
                guard += 1
                idx = _open_index(view)
                if idx is None:
                    break
                line = view["order"]["items"][idx]
                if line["sku_id"] == target:
                    break
                next_idx = None
                for j in range(idx + 1, len(view["order"]["items"])):
                    if view["order"]["items"][j].get("state") not in ("PICKED", "SUBSTITUTED", "REMOVED"):
                        next_idx = j
                        break
                is_last = step.get("voice_last") and (
                    next_idx is not None and view["order"]["items"][next_idx]["sku_id"] == target
                )
                if is_last:
                    view = handler.voice_intent(
                        {"utterance": "got it", "lang": "en", "picker_id": spec["picker_id"],
                         "client_action_id": "%s-seed-voice-pick" % order_id},
                        order_id,
                    )
                else:
                    view = handler.confirm_pick(
                        {"index": idx, "sku_id": line["sku_id"], "picker_id": spec["picker_id"],
                         "source": "tap", "client_action_id": "%s-seed-pick-%d" % (order_id, idx)},
                        order_id,
                    )

        if "pick" in step:
            count = step["pick"]
            total = len(view["order"]["items"])
            count = total if count == "all" else int(count)
            for _ in range(count):
                idx = _open_index(view)
                if idx is None:
                    break
                line = view["order"]["items"][idx]
                view = handler.confirm_pick(
                    {
                        "index": idx,
                        "sku_id": line["sku_id"],
                        "picker_id": spec["picker_id"],
                        "source": "tap",
                        "client_action_id": "%s-seed-pick-%d" % (order_id, idx),
                    },
                    order_id,
                )

        if "oos" in step:
            idx = _index_of(view["order"], step["oos"])
            view = handler.flag_exception(
                {
                    "index": idx,
                    "sku_id": step["oos"],
                    "kind": "OOS",
                    "note": "Shelf empty at the start of the shift",
                    "picker_id": spec["picker_id"],
                    "client_action_id": "%s-seed-oos" % order_id,
                },
                order_id,
            )

        if "voice" in step:
            view = handler.voice_intent(
                {
                    "utterance": step["voice"],
                    "lang": step.get("lang", "te"),
                    "picker_id": spec["picker_id"],
                    "client_action_id": "%s-seed-voice" % order_id,
                },
                order_id,
            )

        if "simulated" in step:
            view = handler.voice_intent(
                {
                    "utterance": step.get("utterance", "simulated intent"),
                    "simulated_intent": step["simulated"],
                    "picker_id": spec["picker_id"],
                    "client_action_id": "%s-seed-sim" % order_id,
                },
                order_id,
            )

        if step.get("verify_item"):
            idx = _open_index(view)
            if idx is not None:
                line = view["order"]["items"][idx]
                view = handler.verify_item(
                    {
                        "index": idx,
                        "sku_id": line["sku_id"],
                        "image_base64": DEMO_PHOTO,
                        "image_format": "jpeg",
                    },
                    order_id,
                )

        if "verify_bag" in step:
            for _ in range(int(step["verify_bag"])):
                view = handler.verify_bag(
                    {"image_base64": DEMO_PHOTO, "image_format": "jpeg"}, order_id
                )

        if "dispatch" in step:
            token = view["order"].get("handover_token")
            view = handler.dispatch({"handover_token": token, "rider_id": step["dispatch"]}, order_id)

    return view


def reset_all():
    """Wipe the store, reseed the catalog, then rebuild every scenario in a fixed order."""
    import dynamo

    dynamo.reset_store()
    built = []
    for key in SCENARIO_ORDER:
        view = build_scenario(key)
        built.append(
            {
                "scenario": key,
                "order_id": SCENARIOS[key]["order_id"],
                "title": SCENARIOS[key]["title"],
                "status": view["order"]["status"],
                "open_index": view.get("current_index"),
            }
        )
    return built


def catalogue():
    """Scenario metadata for the Demo Control Center, without running anything."""
    return [
        {
            "scenario": key,
            "order_id": SCENARIOS[key]["order_id"],
            "title": SCENARIOS[key]["title"],
            "summary": SCENARIOS[key]["summary"],
        }
        for key in SCENARIO_ORDER
    ]
