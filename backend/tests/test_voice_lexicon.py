"""Deterministic multilingual fast path.

The critical property under test is ordering: "evaro teesukunnaru" (someone else took it)
contains "teesukunnaru" (I took it). Classifying the first as a confirmation would silently
mark an item picked that the picker never had in hand.
"""
import voice_lexicon as vl

LINE = {"sku_id": "SKU_MILK", "name": "Amul Milk 500ml", "qty": 2}
SUBS = [{"sku_id": "SKU_MILK_ALT", "name": "Nandini Milk 500ml", "price": "31"}]


def action(text):
    match = vl.classify(text)
    return match["action"] if match else None


def test_english_confirmations():
    for text in ("picked", "got it", "done", "I have it"):
        assert action(text) == vl.CONFIRM_PICK, text


def test_telugu_confirmations():
    for text in ("teesukunnanu", "tissukunna", "sare"):
        assert action(text) == vl.CONFIRM_PICK, text


def test_hindi_confirmations():
    for text in ("mil gaya", "le liya", "ho gaya"):
        assert action(text) == vl.CONFIRM_PICK, text


def test_unavailability_across_languages():
    for text in ("not available", "out of stock", "ledu", "milk ledu",
                 "stock lo ledu", "nahi hai", "khatam hai"):
        assert action(text) == vl.SUBSTITUTE_ITEM, text


def test_concurrent_pick_across_languages():
    for text in ("someone took it", "someone already picked this", "another picker took this",
                 "this item is already taken", "someone already took the product",
                 "evaro teesukunnaru", "inkokaru teesukunnaru", "already teesukunnaru",
                 "item evaro teesukunnaru",
                 "kisi ne le liya", "koi aur le gaya", "ye item kisi ne le liya"):
        assert action(text) == vl.ITEM_ALREADY_TAKEN, text


def test_concurrent_pick_wins_over_confirmation_substring():
    # The whole reason the rules are ordered. A regression here is a silent mis-pick.
    assert action("evaro teesukunnaru") == vl.ITEM_ALREADY_TAKEN
    assert action("teesukunnanu") == vl.CONFIRM_PICK


def test_ambiguous_text_defers_to_the_model():
    assert vl.classify("what should I do about this one") is None
    assert vl.classify("") is None
    assert vl.fast_path("no idea", LINE, SUBS) == (None, None)


def test_native_script_is_matched():
    assert action("\u0c32\u0c47\u0c26\u0c41") == vl.SUBSTITUTE_ITEM
    assert action("\u0915\u093f\u0938\u0940 \u0928\u0947 \u0932\u0947 \u0932\u093f\u092f\u093e") == vl.ITEM_ALREADY_TAKEN


def test_fast_path_shapes_a_full_contract():
    intent, meta = vl.fast_path("milk ledu", LINE, SUBS)
    assert intent["action"] == vl.SUBSTITUTE_ITEM
    assert intent["selected_sku"] == "SKU_MILK_ALT"
    assert meta["interpreter"] == "deterministic-fast-path"
    for field in ("spoken_response_telugu", "spoken_response_hindi", "spoken_response_english", "reason"):
        assert intent[field].strip()


def test_unavailable_with_no_substitute_becomes_an_exception():
    # Never invent a swap the policy engine would refuse anyway.
    intent, _ = vl.fast_path("ledu", LINE, [])
    assert intent["action"] == vl.FLAG_EXCEPTION
