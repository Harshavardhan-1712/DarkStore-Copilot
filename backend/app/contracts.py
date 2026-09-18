"""The Bedrock output contract and its guardrail.

Bedrock is a translation and reasoning layer. Anything it emits is untrusted until this module
says otherwise, and nothing here talks to the database — validation is pure and testable.
"""
import json
import re

from errors import GuardrailRejection

# ITEM_ALREADY_TAKEN is a first-class intent, not a flavour of FLAG_EXCEPTION. The model may
# report it; only the backend may conclude that the order's unit is actually gone.
VALID_ACTIONS = ("CONFIRM_PICK", "SUBSTITUTE_ITEM", "FLAG_EXCEPTION", "ITEM_ALREADY_TAKEN")
# Actions where selected_sku may be omitted: the backend already knows which line is open.
SKU_OPTIONAL_ACTIONS = ("FLAG_EXCEPTION", "ITEM_ALREADY_TAKEN")
REQUIRED_FIELDS = (
    "action",
    "selected_sku",
    "spoken_response_telugu",
    "spoken_response_hindi",
    "spoken_response_english",
    "reason",
)
MAX_SPOKEN_CHARS = 240
_SKU_RE = re.compile(r"^[A-Z0-9][A-Z0-9_\-]{1,39}$")

_FENCE_RE = re.compile(r"```(?:json)?(.*?)```", re.S)


def extract_json(raw_text):
    """Models sometimes wrap JSON in prose or fences. Pull out the first JSON object, or fail."""
    if raw_text is None:
        raise GuardrailRejection("Model returned no text.")
    text = raw_text.strip()
    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise GuardrailRejection("Model output contained no JSON object.", {"raw": raw_text[:500]})
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise GuardrailRejection("Model output was not valid JSON.", {"error": str(exc), "raw": raw_text[:500]})


def validate_intent(payload, allowed_skus):
    """Validate a parsed intent against the schema AND against SKUs that exist in this context.

    allowed_skus is the set of SKU_IDs the model was allowed to name: the current line plus its
    registered substitutes. A SKU outside that set is a hallucination, not a decision.
    """
    if not isinstance(payload, dict):
        raise GuardrailRejection("Intent was not a JSON object.")

    missing = [f for f in REQUIRED_FIELDS if f not in payload]
    if missing:
        raise GuardrailRejection("Intent is missing required fields.", {"missing": missing})

    unexpected = [k for k in payload if k not in REQUIRED_FIELDS]
    if unexpected:
        raise GuardrailRejection("Intent contained unexpected fields.", {"unexpected": unexpected})

    action = payload["action"]
    if action not in VALID_ACTIONS:
        raise GuardrailRejection("Unknown action.", {"action": str(action)[:64]})

    sku = payload["selected_sku"]
    if action in SKU_OPTIONAL_ACTIONS:
        sku = (sku or "").strip().upper() or None
    else:
        if not isinstance(sku, str) or not _SKU_RE.match(sku.strip().upper()):
            raise GuardrailRejection("selected_sku is not a well-formed SKU.", {"selected_sku": str(sku)[:64]})
        sku = sku.strip().upper()
        if sku not in allowed_skus:
            raise GuardrailRejection(
                "selected_sku is not in scope for this pick step.",
                {"selected_sku": sku, "allowed": sorted(allowed_skus)},
            )

    spoken = {}
    for field, lang in (("spoken_response_telugu", "te"), ("spoken_response_hindi", "hi"), ("spoken_response_english", "en")):
        value = payload[field]
        if not isinstance(value, str) or not value.strip():
            raise GuardrailRejection("%s must be non-empty text." % field)
        spoken[lang] = value.strip()[:MAX_SPOKEN_CHARS]

    reason = payload["reason"]
    if not isinstance(reason, str) or not reason.strip():
        raise GuardrailRejection("reason must be non-empty text.")

    return {
        "action": action,
        "selected_sku": sku,
        "spoken_response_telugu": spoken["te"],
        "spoken_response_hindi": spoken["hi"],
        "spoken_response_english": spoken["en"],
        "reason": reason.strip()[:MAX_SPOKEN_CHARS],
    }


def parse_and_validate(raw_text, allowed_skus):
    return validate_intent(extract_json(raw_text), allowed_skus)


def manual_fallback(current_line, note):
    """What the picker gets when the guardrail fires. Never an error screen — always guidance."""
    name = current_line.get("name", current_line.get("sku_id", "item"))
    return {
        "action": "FLAG_EXCEPTION",
        "selected_sku": current_line.get("sku_id"),
        "spoken_response_telugu": "\u0c05\u0c30\u0c4d\u0c25\u0c02 \u0c15\u0c3e\u0c32\u0c47\u0c26\u0c41. \u0c05\u0c30\u0c32\u0c4d\u0c32\u0c4b \u0c24\u0c28\u0c3f\u0c16\u0c40 \u0c1a\u0c47\u0c38\u0c3f \u0c1a\u0c47\u0c24\u0c3f\u0c24\u0c4b \u0c0e\u0c02\u0c1a\u0c41\u0c15\u0c4b\u0c02\u0c21\u0c3f.",
        "spoken_response_hindi": "\u0938\u092e\u091d \u0928\u0939\u0940\u0902 \u0906\u092f\u093e\u0964 \u0936\u0947\u0932\u094d\u092b \u092a\u0930 \u0926\u0947\u0916\u0915\u0930 \u0939\u093e\u0925 \u0938\u0947 \u091a\u0941\u0928\u0947\u0902\u0964",
        "spoken_response_english": "I did not understand. Please check the shelf and choose the item manually.",
        "reason": "Guardrail fallback for %s: %s" % (name, note),
        "fallback": True,
    }


# --- Bag verification contract --------------------------------------------------------------
VERDICTS = ("VERIFIED", "FAILED")
MIN_VERIFY_CONFIDENCE = 0.55


def validate_bag_verdict(payload, manifest_skus):
    """Vision output is advisory. It can fail a bag; it can only pass one with real confidence."""
    if not isinstance(payload, dict):
        raise GuardrailRejection("Bag verdict was not a JSON object.")

    verdict = payload.get("verdict")
    if verdict not in VERDICTS:
        raise GuardrailRejection("Unknown bag verdict.", {"verdict": str(verdict)[:64]})

    try:
        confidence = float(payload.get("confidence", 0))
    except (TypeError, ValueError):
        raise GuardrailRejection("confidence must be a number.")
    confidence = max(0.0, min(1.0, confidence))

    def _sku_list(key):
        raw = payload.get(key) or []
        if not isinstance(raw, list):
            raise GuardrailRejection("%s must be a list." % key)
        # Silently drop SKUs the model invented — the manifest is the only truth.
        return [s.strip().upper() for s in raw if isinstance(s, str) and s.strip().upper() in manifest_skus]

    missing = _sku_list("missing_skus")

    # Fail closed: a low-confidence pass is a fail, and a pass that names missing items is a fail.
    if verdict == "VERIFIED" and (confidence < MIN_VERIFY_CONFIDENCE or missing):
        verdict = "FAILED"

    return {
        "verdict": verdict,
        "missing_skus": missing,
        "confidence": round(confidence, 3),
        "notes": str(payload.get("notes") or payload.get("reason") or "")[:MAX_SPOKEN_CHARS],
        "spoken_response_telugu": str(payload.get("spoken_response_telugu") or "")[:MAX_SPOKEN_CHARS],
        "spoken_response_hindi": str(payload.get("spoken_response_hindi") or "")[:MAX_SPOKEN_CHARS],
    }

# --- Product visual verification contract --------------------------------------------------
PRODUCT_VERDICTS = ("MATCH", "MISMATCH", "UNCLEAR")

def validate_product_verdict(payload, expected_sku):
    if not isinstance(payload, dict):
        raise GuardrailRejection("Product verdict was not a JSON object.")
    verdict = payload.get("verdict")
    if verdict not in PRODUCT_VERDICTS:
        raise GuardrailRejection("Unknown product verdict.", {"verdict": str(verdict)[:64]})
    try:
        confidence = float(payload.get("confidence", 0))
    except (TypeError, ValueError):
        raise GuardrailRejection("confidence must be a number.")
    confidence = max(0.0, min(1.0, confidence))
    if verdict == "MATCH" and confidence < MIN_VERIFY_CONFIDENCE:
        verdict = "UNCLEAR"
    return {
        "verdict": verdict,
        "expected_sku": expected_sku,
        "detected_product": str(payload.get("detected_product") or "Unknown")[:160],
        "confidence": round(confidence, 3),
        "notes": str(payload.get("notes") or payload.get("reason") or "")[:MAX_SPOKEN_CHARS],
        "provider": str(payload.get("provider") or "amazon-bedrock")[:60],
    }
