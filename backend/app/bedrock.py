"""Amazon Bedrock adapter.

Bedrock never reads or writes state. It receives a snapshot the Lambda has already validated,
and returns text that the guardrails in contracts.py must approve before anything happens.
"""
import json

import boto3
from botocore.config import Config as BotoConfig

import config
import contracts

_runtime = None

# Short timeouts: a picker standing in an aisle cannot wait. On timeout we fall back to manual.
_BOTO_CFG = BotoConfig(connect_timeout=3, read_timeout=12, retries={"max_attempts": 1})

INTENT_SYSTEM = """You are the voice layer of a dark-store picking app in India.
Pickers speak Telugu, Hindi or English, often mixed, in a noisy aisle. You receive text from a
phone's speech recogniser, so it may be misspelt, in Telugu or Devanagari script or romanised,
cut off mid-sentence, or include a colleague talking nearby.

Your ONLY job is to map one spoken utterance to one action, and to answer the picker in their
own language. You do not manage stock, price or order state — the backend owns all of that.

Rules:
- Reply with a single JSON object and nothing else. No prose, no markdown fences.
- Keys, exactly: action, selected_sku, spoken_response_telugu, spoken_response_hindi, spoken_response_english, reason.
- action is one of CONFIRM_PICK, SUBSTITUTE_ITEM, ITEM_ALREADY_TAKEN, FLAG_EXCEPTION.
- selected_sku must be copied verbatim from the SKUs given to you. Never invent one.
- CONFIRM_PICK: the picker clearly says THEY have the item in hand. selected_sku = the current item.
  Never use it for a negation or a delay ("not picked", "haven't taken it yet", "nahi mila"),
  a question, a damaged or wrong item, or a sentence about someone else.
- SUBSTITUTE_ITEM: the item is missing or damaged and an eligible substitute is offered.
  selected_sku = the substitute the picker named, or the first eligible one if they said only
  that the item is unavailable. "No problem" / "problem ledu" / "koi dikkat nahi" is NOT a
  stock-out.
- ITEM_ALREADY_TAKEN: the picker says someone else, or another picker, already took this item
  ("evaro teesukunnaru", "kisi ne le liya", "someone took it"). selected_sku = the current item.
- FLAG_EXCEPTION: anything else, including confusion, background chatter, no eligible substitute,
  or an unclear utterance. When unsure, choose this. Guessing costs more than asking.
- Spoken responses: one short sentence each, plain spoken register, under 20 words. They are read
  aloud by a text-to-speech engine, so write Telugu in Telugu script and Hindi in Devanagari
  script (never romanised), English in plain English, and use no emoji, markdown or symbols.
- reason: one short English sentence for the audit log."""

VISION_SYSTEM = """You verify a packed grocery bag against an order manifest from one photo.

Reply with a single JSON object and nothing else, with keys: verdict, missing_skus, confidence,
notes, spoken_response_telugu, spoken_response_hindi.
- verdict is VERIFIED or FAILED.
- missing_skus lists manifest SKU_IDs you cannot see in the photo. Use the given SKU_IDs only.
- confidence is 0 to 1, how sure you are of the verdict overall.
- Items may overlap or be partly hidden; judge what is visible, and lower confidence when the
  photo is blurred, dark, or shot at an angle where items are hidden.
- notes: one short English sentence.
- Spoken responses: one short sentence each, Telugu and Hindi, telling the picker what to fix."""


def _client():
    global _runtime
    if _runtime is None:
        _runtime = boto3.client("bedrock-runtime", region_name=config.AWS_REGION, config=_BOTO_CFG)
    return _runtime


def _converse(model_id, system, content_blocks, max_tokens=512):
    if not model_id:
        raise contracts.GuardrailRejection("No Bedrock model configured for this call.")
    resp = _client().converse(
        modelId=model_id,
        system=[{"text": system}],
        messages=[{"role": "user", "content": content_blocks}],
        inferenceConfig={"maxTokens": max_tokens, "temperature": 0.0, "topP": 0.9},
    )
    parts = resp.get("output", {}).get("message", {}).get("content", [])
    return "".join(p.get("text", "") for p in parts)


def interpret_utterance(utterance, current_line, eligible_substitutes, lang_hint="te"):
    """Map speech to an action. Returns a validated intent dict, or raises GuardrailRejection."""
    context = {
        "picker_language_hint": lang_hint,
        "current_item": {
            "sku_id": current_line.get("sku_id"),
            "name": current_line.get("name"),
            "qty": current_line.get("qty"),
            "aisle": current_line.get("aisle"),
            "shelf": current_line.get("shelf"),
        },
        "eligible_substitutes": [
            {"sku_id": s["sku_id"], "name": s.get("name"), "price": str(s.get("price"))}
            for s in eligible_substitutes
        ],
        "picker_said": utterance,
    }
    allowed = {current_line.get("sku_id")} | {s["sku_id"] for s in eligible_substitutes}
    raw = _converse(
        config.BEDROCK_TEXT_MODEL,
        INTENT_SYSTEM,
        [{"text": json.dumps(context, ensure_ascii=False)}],
    )
    return contracts.parse_and_validate(raw, allowed)


def verify_bag(image_bytes, image_format, manifest, context=None):
    """Compare one top-down photo against the manifest. Returns a validated verdict dict.

    `context` carries order_id and attempt number. Bedrock does not need them to judge a
    photo, but the local demo adapter uses them to stay deterministic across a demo run.
    """
    manifest_text = json.dumps(
        {"expected_items": [{"sku_id": m["sku_id"], "name": m.get("name"), "qty": m.get("qty")} for m in manifest]},
        ensure_ascii=False,
    )
    raw = _converse(
        config.BEDROCK_VISION_MODEL,
        VISION_SYSTEM,
        [
            {"image": {"format": image_format, "source": {"bytes": image_bytes}}},
            {"text": manifest_text},
        ],
        max_tokens=600,
    )
    payload = contracts.extract_json(raw)
    return contracts.validate_bag_verdict(payload, {m["sku_id"] for m in manifest})


PRODUCT_SYSTEM = """You are a product visual verification layer for a dark-store picking app.
You receive one product photo and the backend's expected SKU metadata. Determine whether the
visible product appears to be the expected item. Do not invent a SKU and do not decide stock or
order state.

Reply with one JSON object only with keys: verdict, detected_product, confidence, notes.
- verdict: MATCH, MISMATCH, or UNCLEAR.
- MATCH only when visible packaging/label/brand/size provide enough evidence.
- MISMATCH when the visible product is clearly a different item.
- UNCLEAR when the image is blurry, dark, occluded, or the label cannot be read reliably.
- confidence: 0 to 1.
- notes: one short English sentence.
"""

def verify_product(image_bytes, image_format, expected):
    expected_text = json.dumps({
        "expected_sku": expected.get("sku_id"),
        "expected_name": expected.get("name"),
        "expected_category": expected.get("category"),
        "expected_qty": expected.get("qty"),
    }, ensure_ascii=False)
    raw = _converse(
        config.BEDROCK_VISION_MODEL, PRODUCT_SYSTEM,
        [{"image": {"format": image_format, "source": {"bytes": image_bytes}}}, {"text": expected_text}],
        max_tokens=300,
    )
    payload = contracts.extract_json(raw)
    return contracts.validate_product_verdict(payload, expected.get("sku_id"))

def synthesize_speech(text, lang="hi"):
    """Amazon Polly TTS for supported Indian languages; caller may fall back for Telugu."""
    import base64
    if lang == "te":
        return {"supported": False, "reason": "Amazon Polly does not currently provide a Telugu voice."}
    voice_id = config.POLLY_HI_VOICE if lang == "hi" else config.POLLY_EN_VOICE
    language_code = "hi-IN" if lang == "hi" else "en-IN"
    polly = boto3.client("polly", region_name=config.AWS_REGION, config=_BOTO_CFG)
    resp = polly.synthesize_speech(
        Text=text[:3000],
        OutputFormat="mp3",
        VoiceId=voice_id,
        LanguageCode=language_code,
        Engine="standard",
    )
    audio = resp["AudioStream"].read()
    return {
        "supported": True,
        "provider": "amazon-polly",
        "audio_base64": base64.b64encode(audio).decode("ascii"),
        "audio_format": "audio/mpeg",
        "voice_id": voice_id,
        "language_code": language_code,
    }
