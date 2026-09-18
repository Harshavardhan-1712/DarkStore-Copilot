"""Deterministic multilingual fast path for obvious picker utterances.

Pickers say the same six or seven things all day. Sending those to Bedrock costs money,
adds 1-3 seconds in an aisle, and introduces a chance of misclassification for phrases that
are completely unambiguous. So this module resolves the obvious cases locally and Bedrock is
reserved for genuinely natural / ambiguous language.

This module is pure: it maps text -> (action, language, matched_phrase) and nothing else.
It never decides whether the action is allowed — handler.py + policy do that, as always.

Ordering matters. "evaro teesukunnaru" (someone else took it) contains "teesukunnaru", which
looks like a confirmation. ITEM_ALREADY_TAKEN is therefore matched before CONFIRM_PICK.
"""
import re
import unicodedata

CONFIRM_PICK = "CONFIRM_PICK"
ITEM_ALREADY_TAKEN = "ITEM_ALREADY_TAKEN"
SUBSTITUTE_ITEM = "SUBSTITUTE_ITEM"
FLAG_EXCEPTION = "FLAG_EXCEPTION"

# (action, language, compiled pattern). Evaluated top to bottom; first match wins.
_RULES = [
    # --- ITEM_ALREADY_TAKEN / concurrent pick. Checked first: see note above. -------------
    (ITEM_ALREADY_TAKEN, "en", r"\bsome\s*one\s+(else\s+)?(has\s+)?(already\s+)?(took|taken|picked|grabbed)\b"),
    (ITEM_ALREADY_TAKEN, "en", r"\banother\s+(picker|person|guy)\b"),
    (ITEM_ALREADY_TAKEN, "en", r"\balready\s+(taken|picked|gone|collected)\b"),
    (ITEM_ALREADY_TAKEN, "en", r"\bsomebody\s+(took|taken|picked)\b"),
    (ITEM_ALREADY_TAKEN, "te", r"\b(evaro|evvaro|yevaro|inkokaru|inkokaru|inkevaro|maro\s*picker)\b"),
    (ITEM_ALREADY_TAKEN, "te", r"\balready\s+(teesukunnaru|tisukunnaru|teeskunnaru)\b"),
    (ITEM_ALREADY_TAKEN, "te", r"\b(teesukunnaru|tisukunnaru|teeskunnaru|theesukunnaru)\b"),
    (ITEM_ALREADY_TAKEN, "hi", r"\b(kisi|kisine|koi)\s*(ne|aur)?\s*(le\s*liya|le\s*gaya|utha\s*liya)\b"),
    (ITEM_ALREADY_TAKEN, "hi", r"\bkoi\s+aur\b"),
    (ITEM_ALREADY_TAKEN, "hi", r"\bpehle\s+se\s+(le\s*liya|nahi\s*hai)\b"),

    # --- Stock-out / not available ---------------------------------------------------------
    (SUBSTITUTE_ITEM, "en", r"\b(out\s+of\s+stock|not\s+available|unavailable|no\s+stock|shelf\s+is\s+empty|not\s+there|nothing\s+here)\b"),
    (SUBSTITUTE_ITEM, "te", r"\b(ledu|ledhu|leedu|stock\s*lo\s*ledu|ippudu\s*ledu|khaali)\b"),
    (SUBSTITUTE_ITEM, "hi", r"\b(nahi\s*hai|nahin\s*hai|khatam\s*(hai|ho\s*gaya)|stock\s*nahi|khali\s*hai)\b"),

    # --- Confirmation ----------------------------------------------------------------------
    (CONFIRM_PICK, "en", r"\b(picked|got\s+it|gotit|have\s+it|done|taken\s+it|in\s+the\s+bag|okay\s+picked)\b"),
    (CONFIRM_PICK, "te", r"\b(t[ei]+s+u?kunna(nu)?|theesukunnanu|teeskunna(nu)?|sare|ayindi|ipoyindi)\b"),
    (CONFIRM_PICK, "hi", r"\b(mil\s*gaya|le\s*liya|ho\s*gaya|kar\s*liya|rakh\s*liya)\b"),
]

_COMPILED = [(action, lang, re.compile(pattern, re.I)) for action, lang, pattern in _RULES]

# Devanagari / Telugu script keywords, so a picker whose phone transcribes in native script
# still gets the fast path instead of falling through to the model.
_SCRIPT_RULES = [
    (ITEM_ALREADY_TAKEN, "te", ("\u0c0e\u0c35\u0c30\u0c4b", "\u0c07\u0c02\u0c15\u0c4a\u0c15\u0c30\u0c41")),
    (ITEM_ALREADY_TAKEN, "hi", ("\u0915\u093f\u0938\u0940 \u0928\u0947", "\u0915\u094b\u0908 \u0914\u0930")),
    (SUBSTITUTE_ITEM, "te", ("\u0c32\u0c47\u0c26\u0c41",)),
    (SUBSTITUTE_ITEM, "hi", ("\u0928\u0939\u0940\u0902 \u0939\u0948", "\u0916\u0924\u094d\u092e")),
    (CONFIRM_PICK, "te", ("\u0c24\u0c40\u0c38\u0c41\u0c15\u0c41\u0c28\u0c4d\u0c28\u0c3e\u0c28\u0c41",)),
    (CONFIRM_PICK, "hi", ("\u092e\u093f\u0932 \u0917\u092f\u093e", "\u0932\u0947 \u0932\u093f\u092f\u093e")),
]


def normalise(utterance):
    text = unicodedata.normalize("NFKC", str(utterance or "")).strip()
    return re.sub(r"\s+", " ", text)


def classify(utterance):
    """Return {"action", "lang", "matched", "confident"} or None when the text is ambiguous.

    None is not a failure — it means "hand this to Bedrock", which is the whole point.
    """
    text = normalise(utterance)
    if not text:
        return None

    for action, lang, phrases in _SCRIPT_RULES:
        for phrase in phrases:
            if phrase in text:
                return {"action": action, "lang": lang, "matched": phrase, "confident": True}

    for action, lang, regex in _COMPILED:
        match = regex.search(text)
        if match:
            return {"action": action, "lang": lang, "matched": match.group(0), "confident": True}

    return None


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
