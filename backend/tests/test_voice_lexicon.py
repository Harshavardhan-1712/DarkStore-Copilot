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
    for text in ("teesukunnanu", "tissukunna", "theesukunnanu", "dorikindi"):
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


# --- strictness: the fast path must not fire on noise, negation or conversation --------------
def test_negated_or_conditional_confirmations_are_not_confirmations():
    # A false CONFIRM_PICK is a silent mis-pick, so all of these go to the model instead.
    for text in ("not picked", "I have not picked it", "haven't picked yet", "not done", "no I did not pick",
                 "mil gaya nahi", "abhi nahi mila", "kharab ho gaya", "damaged, done", "wrong item, got it"):
        assert vl.classify(text) is None or vl.classify(text)["action"] != vl.CONFIRM_PICK, text


def test_ambient_okay_and_ran_out_are_not_confirmations():
    # "sare" is just "okay"; "ipoyindi" usually means "ran out" (the opposite of a pick).
    for text in ("sare", "ipoyindi", "milk ipoyindi"):
        assert vl.classify(text) is None, text


def test_hedged_or_questioning_speech_defers_to_the_model():
    for text in ("kya ho gaya", "pata nahi hai", "maybe picked", "not sure it is out of stock",
                 "problem ledu", "problem nahi hai", "parvaledu", "what is done", "teliyadu"):
        assert vl.classify(text) is None, text


def test_long_conversation_is_never_a_fast_path_command():
    text = "arre bhai wo customer ne bola tha ki mil gaya to bata dena mujhe"
    assert vl.classify(text) is None
    assert vl.classify("picked " + "word " * vl.MAX_FAST_WORDS) is None


def test_third_person_taking_is_not_my_confirmation():
    assert action("usne le liya") == vl.ITEM_ALREADY_TAKEN
    assert action("dusre ne le liya") == vl.ITEM_ALREADY_TAKEN


def test_stock_out_variants():
    for text in ("nahi mila", "nhi hai", "dorakadu", "milk dorakatledu"):
        assert action(text) == vl.SUBSTITUTE_ITEM, text


def test_native_script_matches_whole_words_only():
    ledu = "\u0c32\u0c47\u0c26\u0c41"                                   # ledu (not available)
    did_not_take = "\u0c24\u0c40\u0c38\u0c41\u0c15\u0c4b\u0c32\u0c47\u0c26\u0c41"   # teesukoledu (did not take)
    assert action(ledu) == vl.SUBSTITUTE_ITEM
    assert action("milk " + ledu) == vl.SUBSTITUTE_ITEM
    assert action(did_not_take) is None   # contains "ledu" as a substring but is a different word


def test_native_script_confirmation_variants_and_punctuation():
    assert action("\u0c24\u0c40\u0c38\u0c41\u0c15\u0c41\u0c28\u0c4d\u0c28\u0c3e") == vl.CONFIRM_PICK          # teesukunna
    assert action("\u0c24\u0c40\u0c38\u0c41\u0c15\u0c41\u0c28\u0c4d\u0c28\u0c3e\u0c28\u0c41.") == vl.CONFIRM_PICK   # trailing full stop
    assert action("\u092e\u093f\u0932 \u0917\u092f\u093e\u0964") == vl.CONFIRM_PICK                               # mil gaya + danda
    assert action("\u0938\u094d\u091f\u0949\u0915 \u0916\u0924\u094d\u092e \u0939\u094b \u0917\u092f\u093e") == vl.SUBSTITUTE_ITEM  # "stock khatam ho gaya" is a stock-out, not a confirm


def test_native_script_taken_wins_over_confirmation():
    assert action("\u0c07\u0c26\u0c3f \u0c0e\u0c35\u0c30\u0c4b \u0c24\u0c40\u0c38\u0c41\u0c15\u0c41\u0c28\u0c4d\u0c28\u0c3e\u0c30\u0c41") == vl.ITEM_ALREADY_TAKEN
    assert action("\u0c24\u0c40\u0c38\u0c41\u0c15\u0c41\u0c28\u0c4d\u0c28\u0c3e\u0c30\u0c41") == vl.ITEM_ALREADY_TAKEN


def test_zero_width_characters_do_not_break_matching():
    assert action("pick\u200bed") == vl.CONFIRM_PICK
