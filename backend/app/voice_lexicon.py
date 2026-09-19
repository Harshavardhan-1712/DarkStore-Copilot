"""Deterministic multilingual fast path for obvious picker utterances.

Pickers say the same six or seven things all day. Sending those to Bedrock costs money,
adds 1-3 seconds in an aisle, and introduces a chance of misclassification for phrases that
are completely unambiguous. So this module resolves the obvious cases locally and Bedrock is
reserved for genuinely natural / ambiguous language.

This module is pure: it maps text -> (action, language, matched_phrase) and nothing else.
It never decides whether the action is allowed — handler.py + policy do that, as always.

Ordering matters. "evaro teesukunnaru" (someone else took it) contains "teesukunnaru", which
looks like a confirmation. ITEM_ALREADY_TAKEN is therefore matched before CONFIRM_PICK.

"Obvious" is deliberately strict. A phone in a noisy aisle transcribes colleagues, negations and
half-sentences too, and a keyword hit inside those must not silently pick or swap an item. So
the fast path only fires when the utterance is short, is not hedged or negated, and matches on
whole words. Everything else is handed to the model, which reads the whole sentence.
"""
import re
import unicodedata

CONFIRM_PICK = "CONFIRM_PICK"
ITEM_ALREADY_TAKEN = "ITEM_ALREADY_TAKEN"
SUBSTITUTE_ITEM = "SUBSTITUTE_ITEM"
FLAG_EXCEPTION = "FLAG_EXCEPTION"

# A real one-shot command is a few words. Longer speech is conversation, not a command.
MAX_FAST_WORDS = 7

# (action, language, compiled pattern). Evaluated top to bottom; first match wins.
_RULES = [
    # --- ITEM_ALREADY_TAKEN / concurrent pick. Checked first: see note above. -------------
    (ITEM_ALREADY_TAKEN, "en", r"\bsome\s*one\s+(else\s+)?(has\s+)?(already\s+)?(took|taken|picked|grabbed)\b"),
    (ITEM_ALREADY_TAKEN, "en", r"\banother\s+(picker|person|guy)\b"),
    (ITEM_ALREADY_TAKEN, "en", r"\balready\s+(taken|picked|gone|collected)\b"),
    (ITEM_ALREADY_TAKEN, "en", r"\bsomebody\s+(took|taken|picked)\b"),
    (ITEM_ALREADY_TAKEN, "te", r"\b(evaro|evvaro|yevaro|inkokaru|inkevaro|marokaru|maro\s*picker)\b"),
    (ITEM_ALREADY_TAKEN, "te", r"\balready\s+(teesukunnaru|tisukunnaru|teeskunnaru)\b"),
    (ITEM_ALREADY_TAKEN, "te", r"\b(teesukunnaru|tisukunnaru|teeskunnaru|theesukunnaru)\b"),
    (ITEM_ALREADY_TAKEN, "hi", r"\b(kisi|kisine|koi)\s*(ne|aur)?\s*(le\s*liya|le\s*gaya|utha\s*liya)\b"),
    (ITEM_ALREADY_TAKEN, "hi", r"\bkoi\s+aur\b"),
    (ITEM_ALREADY_TAKEN, "hi", r"\bpehle\s+se\s+(le\s*liya|nahi\s*hai)\b"),
    # Third person: "usne le liya" is *he* took it. Without this it fell through to CONFIRM_PICK's
    # "le liya" and silently marked the item picked by the wrong person.
    (ITEM_ALREADY_TAKEN, "hi", r"\b(us\s*ne|unhone|dusre\s*(ne|picker)|doosre\s*(ne|picker))\b"),

    # --- Stock-out / not available ---------------------------------------------------------
    (SUBSTITUTE_ITEM, "en", r"\b(out\s+of\s+stock|not\s+available|unavailable|no\s+stock|shelf\s+is\s+empty|not\s+there|nothing\s+here)\b"),
    (SUBSTITUTE_ITEM, "te", r"\b(ledu|ledhu|leedu|stock\s*lo\s*ledu|ippudu\s*ledu|khaali|dorakadu|dorakatledu|dorakatledhu|dorakaledu)\b"),
    (SUBSTITUTE_ITEM, "hi", r"\b((nahi|nahin|nhi)\s*(hai|mila|mil\s*raha)|khatam\s*(hai|ho\s*gaya)|stock\s*(nahi|nhi)|khali\s*hai)\b"),

    # --- Confirmation ----------------------------------------------------------------------
    # Not here on purpose: "sare" (just "okay", said to anyone) and "ipoyindi" (usually "it has
    # run out", i.e. the opposite of a pick). The model sees those in context instead.
    (CONFIRM_PICK, "en", r"\b(picked|got\s+it|gotit|have\s+it|found\s+it|done|taken\s+it|in\s+the\s+bag|okay\s+picked)\b"),
    (CONFIRM_PICK, "te", r"\b(th?[ei]+s+u?kunna(nu|m)?|dorikindh?i|ayindi)\b"),
    (CONFIRM_PICK, "hi", r"\b(mil\s*gay[ai]|le\s*liya|ho\s*gaya|kar\s*liya|rakh\s*liya)\b"),
]

_COMPILED = [(action, lang, re.compile(pattern, re.I)) for action, lang, pattern in _RULES]

# Native-script keywords. Chrome returns Telugu / Hindi speech in Telugu / Devanagari script for
# te-IN / hi-IN, so this is the path most real Telugu and Hindi utterances take. Matched as whole
# words, never as substrings: "లేదు" (ledu) sits inside "తీసుకోలేదు" (teesukoledu, "did not take
# it"), and a substring hit would have reported that as a stock-out.
_SCRIPT_RULES = [
    # ITEM_ALREADY_TAKEN
    (ITEM_ALREADY_TAKEN, "te", ("ఎవరో", "ఇంకొకరు", "మరొకరు", "తీసుకున్నారు")),     # evaro, inkokaru, marokaru, teesukunnaru
    (ITEM_ALREADY_TAKEN, "hi", ("किसी ने", "किसी और ने", "कोई और", "उसने", "उन्होंने")),  # kisi ne, kisi aur ne, koi aur, usne, unhone
    # SUBSTITUTE_ITEM
    (SUBSTITUTE_ITEM, "te", ("లేదు",)),                                               # ledu
    (SUBSTITUTE_ITEM, "hi", ("नहीं है", "नही है", "नहीं मिला", "नहीं मिल रहा", "खत्म", "खाली")),  # nahi hai, nahi mila, khatam, khaali
    # CONFIRM_PICK
    (CONFIRM_PICK, "te", ("తీసుకున్నాను", "తీసుకున్నా", "తీసుకున్నాం", "దొరికింది", "అయింది")),  # teesukunnanu, teesukunna, teesukunnam, dorikindi, ayindi
    (CONFIRM_PICK, "hi", ("मिल गया", "मिल गयी", "मिल गई", "ले लिया", "हो गया", "कर लिया", "रख लिया")),  # mil gaya, le liya, ho gaya ...
]

# "This is not a clean command", whatever else the sentence contains. Any hit hands the whole
# utterance to the model. Covers hedging, questions, and the very common "no problem" phrases
# whose last word ("ledu" / "nahi") is otherwise a stock-out keyword.
_NOT_A_COMMAND = re.compile(
    r"\b("
    r"maybe|perhaps|not\s+sure|don'?t\s+know|dunno|i\s+think|wait|hold\s+on|what|why|how"
    r"|pata\s+(nahi|nahin|nhi)|maloom\s+nahi|shayad|kya|kyun|kaise"
    r"|teliyadu|telidu|emo|enti|yenti|emiti|parvaledu|parvaleedu"
    r"|(problem|issue|tension|dikkat|fikar|masla)\s+(ledu|ledhu|leedu|nahi|nahin|nhi)"
    r")\b",
    re.I,
)
_SCRIPT_NOT_A_COMMAND = (
    "సమస్య లేదు", "పర్వాలేదు", "తెలియదు", "ఏమో", "ఏంటి", "ఏమిటి",          # no problem, don't know, what
    "कोई बात नहीं", "समस्या नहीं", "दिक्कत नहीं", "पता नहीं", "शायद", "क्या", "क्यों", "कैसे",
)

# Extra blockers for CONFIRM_PICK only, because a false confirm is the silent mis-pick: the
# app records an item as in the bag that the picker never had. Negation, "not yet", damage.
_NOT_A_CONFIRM = re.compile(
    r"\b("
    r"not|no|never|yet|cannot|haven'?t|hasn'?t|didn'?t|don'?t|isn'?t|can'?t|couldn'?t|won'?t|wasn'?t"
    r"|damaged|broken|expired|leaking|wrong|problem|issue"
    r"|nahi|nahin|nhi|mat|kharab|kharaab|toot|galat"
    r"|inka|inkaa|kaadu|kadu|thappu|tappu"
    r")\b",
    re.I,
)
_SCRIPT_NOT_A_CONFIRM = (
    "नहीं", "नही", "मत", "खराब", "गलत",   # nahi, mat, kharab, galat
    "కాదు", "ఇంకా", "తప్పు",             # kaadu, inka, thappu
)

_EDGE_PUNCT = ".,!?;:\"'()[]{}-\u2026\u0964\u0965"   # includes the Devanagari danda
_INVISIBLE = re.compile("[\u200b\u200c\u200d\ufeff]")


def normalise(utterance):
    text = unicodedata.normalize("NFKC", str(utterance or "")).strip()
    text = _INVISIBLE.sub("", text)   # zero-width joiners some keyboards / recognisers insert
    return re.sub(r"\s+", " ", text)


def _tokens(text):
    return [t for t in (w.strip(_EDGE_PUNCT) for w in text.lower().split()) if t]


def _has_phrase(tokens, phrases):
    """Whole-word phrase match on a token list (so a keyword inside a longer word never counts)."""
    joined = " " + " ".join(tokens) + " "
    return any(" %s " % p in joined for p in phrases)


def classify(utterance):
    """Return {"action", "lang", "matched", "confident"} or None when the text is ambiguous.

    None is not a failure — it means "hand this to Bedrock", which is the whole point.
    """
    text = normalise(utterance)
    if not text:
        return None

    tokens = _tokens(text)
    if not tokens or len(tokens) > MAX_FAST_WORDS:
        return None
    if _NOT_A_COMMAND.search(text) or _has_phrase(tokens, _SCRIPT_NOT_A_COMMAND):
        return None

    match = None
    for action, lang, phrases in _SCRIPT_RULES:
        phrase = next((p for p in phrases if _has_phrase(tokens, (p,))), None)
        if phrase:
            match = {"action": action, "lang": lang, "matched": phrase, "confident": True}
            break

    if match is None:
        for action, lang, regex in _COMPILED:
            found = regex.search(text)
            if found:
                match = {"action": action, "lang": lang, "matched": found.group(0), "confident": True}
                break

    if match and match["action"] == CONFIRM_PICK:
        if _NOT_A_CONFIRM.search(text) or _has_phrase(tokens, _SCRIPT_NOT_A_CONFIRM):
            return None
    return match


# --- Canned spoken replies for the fast path -------------------------------------------------
# The fast path must still satisfy the same intent contract as Bedrock, including all three
# spoken responses, so these are written once here rather than scattered through the handler.
_SPEECH = {
    CONFIRM_PICK: {
        "te": "\u0c2c\u0c3e\u0c17\u0c41\u0c02\u0c26\u0c3f. \u0c24\u0c30\u0c4d\u0c35\u0c3e\u0c24 \u0c35\u0c38\u0c4d\u0c24\u0c41\u0c35\u0c41\u0c15\u0c3f \u0c35\u0c46\u0c33\u0c4d\u0c33\u0c02\u0c21\u0c3f.",
        "hi": "\u0920\u0940\u0915 \u0939\u0948\u0964 \u0905\u0917\u0932\u093e \u0938\u093e\u092e\u093e\u0928 \u0932\u0940\u091c\u093f\u092f\u0947\u0964",
        "en": "Picked. Move to the next item.",
    },
    SUBSTITUTE_ITEM: {
        "te": "\u0c38\u0c4d\u0c1f\u0c3e\u0c15\u0c4d \u0c32\u0c47\u0c26\u0c41. \u0c2c\u0c26\u0c41\u0c32\u0c41\u0c17\u0c3e {sub} \u0c24\u0c40\u0c38\u0c41\u0c15\u0c4b\u0c02\u0c21\u0c3f.",
        "hi": "\u0938\u094d\u091f\u0949\u0915 \u0928\u0939\u0940\u0902 \u0939\u0948\u0964 \u092c\u0926\u0932\u0947 \u092e\u0947\u0902 {sub} \u0932\u0940\u091c\u093f\u092f\u0947\u0964",
        "en": "That item is out of stock. Take {sub} instead.",
    },
    ITEM_ALREADY_TAKEN: {
        "te": "\u0c07\u0c26\u0c3f \u0c07\u0c02\u0c15\u0c4a\u0c15\u0c30\u0c41 \u0c24\u0c40\u0c38\u0c41\u0c15\u0c41\u0c28\u0c4d\u0c28\u0c3e\u0c30\u0c41. \u0c38\u0c4d\u0c1f\u0c4b\u0c30\u0c4d \u0c1a\u0c46\u0c15\u0c4d \u0c1a\u0c47\u0c38\u0c4d\u0c24\u0c41\u0c02\u0c26\u0c3f.",
        "hi": "\u092f\u0939 \u0938\u093e\u092e\u093e\u0928 \u0915\u093f\u0938\u0940 \u0928\u0947 \u092a\u0939\u0932\u0947 \u0939\u0940 \u0932\u0947 \u0932\u093f\u092f\u093e\u0964 \u0938\u094d\u091f\u094b\u0930 \u091c\u093e\u0901\u091a \u0930\u0939\u093e \u0939\u0948\u0964",
        "en": "Another picker has already taken this item. Checking the store now.",
    },
    FLAG_EXCEPTION: {
        "te": "\u0c05\u0c30\u0c4d\u0c25\u0c02 \u0c15\u0c3e\u0c32\u0c47\u0c26\u0c41. \u0c38\u0c4d\u0c15\u0c4d\u0c30\u0c40\u0c28\u0c4d \u0c2e\u0c40\u0c26 \u0c0e\u0c02\u0c1a\u0c41\u0c15\u0c4b\u0c02\u0c21\u0c3f.",
        "hi": "\u0938\u092e\u091d \u0928\u0939\u0940\u0902 \u0906\u092f\u093e\u0964 \u0938\u094d\u0915\u094d\u0930\u0940\u0928 \u092a\u0930 \u091a\u0941\u0928\u093f\u092f\u0947\u0964",
        "en": "I did not understand. Please choose on the screen.",
    },
}


def build_intent(action, current_line, substitute=None, matched=""):
    """Shape a fast-path match into the same intent dict Bedrock is required to return."""
    sub_name = (substitute or {}).get("name") or "the approved substitute"
    speech = _SPEECH.get(action, _SPEECH[FLAG_EXCEPTION])
    if action == SUBSTITUTE_ITEM and substitute:
        selected = substitute["sku_id"]
    else:
        selected = current_line.get("sku_id")
    return {
        "action": action,
        "selected_sku": selected,
        "spoken_response_telugu": speech["te"].format(sub=sub_name),
        "spoken_response_hindi": speech["hi"].format(sub=sub_name),
        "spoken_response_english": speech["en"].format(sub=sub_name),
        "reason": "Deterministic fast path matched %r." % matched,
    }


def fast_path(utterance, current_line, eligible_substitutes):
    """Full fast path: classify, then shape. Returns (intent, meta) or (None, None).

    A stock-out phrase with no eligible substitute is deliberately downgraded to
    FLAG_EXCEPTION rather than inventing a swap — policy would refuse it anyway.
    """
    match = classify(utterance)
    if not match:
        return None, None
    action = match["action"]
    substitute = None
    if action == SUBSTITUTE_ITEM:
        if eligible_substitutes:
            substitute = eligible_substitutes[0]
        else:
            action = FLAG_EXCEPTION
    intent = build_intent(action, current_line, substitute, match["matched"])
    meta = {
        "interpreter": "deterministic-fast-path",
        "detected_lang": match["lang"],
        "matched_phrase": match["matched"],
        "raw_action": match["action"],
    }
    return intent, meta
