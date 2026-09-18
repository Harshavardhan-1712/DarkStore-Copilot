"""Runtime configuration. Everything tunable lives here, nothing is hardcoded deep in logic."""
import os

INVENTORY_TABLE = os.environ.get("INVENTORY_TABLE", "darkstore-inventory")
ORDERS_TABLE = os.environ.get("ORDERS_TABLE", "darkstore-orders")
AUDIT_TABLE = os.environ.get("AUDIT_TABLE", "darkstore-audit")

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

# Bedrock inference profile / model IDs. Set these in template.yaml or the Lambda console —
# the exact IDs differ per region and per account entitlement, so we never hardcode a guess.
BEDROCK_TEXT_MODEL = os.environ.get("BEDROCK_TEXT_MODEL", "")
BEDROCK_VISION_MODEL = os.environ.get("BEDROCK_VISION_MODEL", "")

# --- Store policy: the deterministic rules Bedrock is NOT allowed to overrule ---------------
# A substitute may cost at most this multiple of the original line price.
MAX_SUBSTITUTE_PRICE_RATIO = float(os.environ.get("MAX_SUBSTITUTE_PRICE_RATIO", "1.10"))
# A substitute may never be cheaper than this multiple (stops "swap ghee for water" nonsense).
MIN_SUBSTITUTE_PRICE_RATIO = float(os.environ.get("MIN_SUBSTITUTE_PRICE_RATIO", "0.60"))
# Categories that can never be auto-substituted (regulatory / allergen risk).
BLOCKED_SUBSTITUTION_CATEGORIES = set(
    c.strip().lower()
    for c in os.environ.get("BLOCKED_SUBSTITUTION_CATEGORIES", "pharma,baby_formula,alcohol").split(",")
    if c.strip()
)
# Substitutes must share the original's category.
REQUIRE_CATEGORY_MATCH = os.environ.get("REQUIRE_CATEGORY_MATCH", "true").lower() == "true"

SUPPORTED_LANGS = ("te", "hi", "en")


# Amazon Polly. Polly currently has no Telugu voice; Telugu uses browser TTS fallback.
POLLY_HI_VOICE = os.environ.get("POLLY_HI_VOICE", "Aditi")
POLLY_EN_VOICE = os.environ.get("POLLY_EN_VOICE", "Raveena")
