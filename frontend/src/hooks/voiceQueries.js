// Voice queries: "where is it?", "how many?", "what's next?", "say again".
//
// A query is read-only. It is answered from the order the store last returned, so nothing is
// sent to the backend, no stock/price/order state can change, and it works with no connection.
//
// The risk is a query swallowing a real command ("milk ledu, where is the other one"), so
// classification is strict: short utterances only, and anything containing a command marker
// (picked / out of stock / someone took it, in all three languages) is left to the normal
// intent path instead.
import { describeItem, locationPhrase } from "./readout.js";

export const QUERY = Object.freeze({
  REPEAT: "REPEAT",
  REMAINING: "REMAINING",
  NEXT: "NEXT",
  WHERE: "WHERE",
  QUANTITY: "QUANTITY",
  NAME: "NAME",
});

const MAX_QUERY_WORDS = 6;
const CLOSED = new Set(["PICKED", "SUBSTITUTED", "REMOVED"]);

const INVISIBLE = /[\u200b\u200c\u200d\ufeff]/g;
const EDGE = /^[.,!?;:"'()[\]{}\-\u2026\u0964\u0965]+|[.,!?;:"'()[\]{}\-\u2026\u0964\u0965]+$/g;

const nfkc = (s) => s.normalize("NFKC");

function normalise(text) {
  return nfkc(String(text ?? ""))
    .replace(/[\u2018\u2019]/g, "'")
    .replace(INVISIBLE, "")
    .replace(/\s+/g, " ")
    .trim();
}

function tokens(text) {
  return text
    .toLowerCase()
    .split(" ")
    .map((w) => w.replace(EDGE, ""))
    .filter(Boolean);
}

/** Whole-word phrase match on a token list, so a keyword inside a longer word never counts. */
function hasPhrase(toks, phrases) {
  const joined = ` ${toks.join(" ")} `;
  return phrases.some((p) => joined.includes(` ${p} `));
}

// Latin-script (romanised) patterns are regexes; native-script keywords are phrase lists, because
// \b does not work on Telugu / Devanagari.
const RULES = [
  [
    QUERY.REPEAT,
    /\b(repeat|again|once more|pardon|malli|inkosari|inkosaari|marosari|dobara|dubara|phir se|fir se|ek baar aur)\b/,
    ["మళ్ళీ", "మళ్లీ", "మరోసారి", "ఇంకోసారి", "दोबारा", "फिर से", "एक बार और"],
  ],
  [
    QUERY.REMAINING,
    /\b(items? left|lines? left|how many (?:items?|lines?|more)? ?(?:left|remaining|to go)|what'?s left|remaining|kitne (?:bache|bacche|baaki|aur)|baaki|bache|bacche|inka enni|inkenni|migilina|migilindi)\b/,
    ["ఇంకా ఎన్ని", "మిగిలిన", "మిగిలింది", "कितने बचे", "कितने बाकी", "कितने और", "बाकी", "बचे"],
  ],
  [
    QUERY.NEXT,
    /\b(what'?s next|next(?: item| one)?|after this|tarvata|tarvatha|taruvata|agla|agle)\b/,
    ["తర్వాత", "తరువాత", "अगला", "अगले"],
  ],
  [
    QUERY.WHERE,
    /\b(where|which (?:aisle|shelf)|what (?:aisle|shelf)|aisle|shelf|location|ekkada|ekkade|kahan|kahaan|kaha|kidhar)\b/,
    ["ఎక్కడ", "कहाँ", "कहां", "किधर"],
  ],
  [
    QUERY.QUANTITY,
    /\b(how many|how much|quantity|qty|enni|entha|enta|kitne|kitna|kitni)\b/,
    ["ఎన్ని", "ఎంత", "कितने", "कितना", "कितनी"],
  ],
  [
    QUERY.NAME,
    /\b(what(?:'?s| is)? (?:this |the )?(?:item|product)|which (?:item|product)|item name|peru|naam|kaun ?sa (?:item|saman|samaan)|item enti)\b/,
    ["పేరు", "ఏ వస్తువు", "नाम", "कौन सा", "कौनसा"],
  ],
].map(([kind, re, native]) => [kind, re, native.map(nfkc)]);

// Words that mean the picker is issuing a command, not asking a question. Mirrors the fast-path
// vocabulary in backend/app/voice_lexicon.py. Any hit hands the whole utterance to that path.
const COMMAND_MARKER =
  /\b(picked|got ?it|have it|found it|done|taken|took|in the bag|out of stock|not available|unavailable|no stock|empty|not there|nothing here|ledu|ledhu|leedu|khaali|khali|dorakadu|dorakatledu|nahi|nahin|nhi|khatam|th?ee?sukunna\w*|tisukunna\w*|evaro|inkokaru|marokaru|kisi|koi aur|usne|unhone|mil ?gay[ai]|le ?liya|ho ?gaya|kar ?liya|rakh ?liya|dorikindi|ayindi|someone|somebody|another picker|already)\b/;
const NATIVE_COMMAND_MARKERS = [
  "లేదు", "తీసుకున్నాను", "తీసుకున్నా", "తీసుకున్నాం", "తీసుకున్నారు", "దొరికింది", "అయింది", "ఎవరో", "ఇంకొకరు", "మరొకరు",
  "नहीं", "नही", "खत्म", "खाली", "मिल गया", "मिल गयी", "मिल गई", "ले लिया", "हो गया", "कर लिया", "रख लिया",
  "किसी ने", "किसी और ने", "कोई और", "उसने", "उन्होंने",
].map(nfkc);

/** Returns a QUERY kind, or null when the utterance is not clearly a question. */
export function classifyQuery(utterance) {
  const text = normalise(utterance);
  if (!text) return null;
  const toks = tokens(text);
  if (!toks.length || toks.length > MAX_QUERY_WORDS) return null;

  const lower = toks.join(" ");
  if (COMMAND_MARKER.test(lower) || hasPhrase(toks, NATIVE_COMMAND_MARKERS)) return null;

  for (const [kind, re, native] of RULES) {
    if (re.test(lower) || hasPhrase(toks, native)) return kind;
  }
  return null;
}

// --- answers ---------------------------------------------------------------------------------
const pick = (lang) => (["en", "hi", "te"].includes(lang) ? lang : "en");
const say = (lang, en, hi, te) => ({ en, hi, te })[pick(lang)];
const dot = (lang) => (pick(lang) === "hi" ? "।" : ".");

function openLines(items) {
  return (items || [])
    .map((item, i) => ({ item, i }))
    .filter(({ item }) => item && !CLOSED.has(item.state));
}

const NO_ITEM = {
  en: "There is no item to read right now.",
  hi: "अभी पढ़ने के लिए कोई सामान नहीं है।",
  te: "ప్రస్తుతం చదవడానికి వస్తువు లేదు.",
};
const LAST_ITEM = {
  en: "This is the last item.",
  hi: "यह आख़िरी सामान है।",
  te: "ఇదే చివరి వస్తువు.",
};
const NO_LOCATION = {
  en: "I don't have a location for this item.",
  hi: "इस सामान की जगह मेरे पास नहीं है।",
  te: "ఈ వస్తువు స్థానం నా దగ్గర లేదు.",
};

/**
 * ctx: { lang, item, index, items } straight from the store's last response
 * (current_item, current_index, order.items).
 */
export function answerQuery(kind, { lang = "te", item, index, items = [] } = {}) {
  const l = pick(lang);
  if (!item) return NO_ITEM[l];
  const name = item.name || "";
  const end = dot(l);

  switch (kind) {
    case QUERY.REPEAT:
      return describeItem(item, l, "again");

    case QUERY.WHERE: {
      const where = locationPhrase(item, l);
      return where ? where + end : NO_LOCATION[l];
    }

    case QUERY.QUANTITY: {
      const qty = item.qty ?? 1;
      return say(l, `Pick ${qty} of ${name}.`, `${name}, ${qty} लीजिए।`, `${name}, ${qty} తీసుకోండి.`);
    }

    case QUERY.NAME:
      return say(l, `It is ${name}.`, `यह ${name} है।`, `ఇది ${name}.`);

    case QUERY.NEXT: {
      const next = openLines(items).find(({ i }) => i > index);
      if (!next) return LAST_ITEM[l];
      const where = locationPhrase(next.item, l);
      const tail = where ? ` ${where}${end}` : "";
      return say(
        l,
        `After this: ${next.item.name}.${tail}`,
        `इसके बाद: ${next.item.name}।${tail}`,
        `దీని తర్వాత: ${next.item.name}.${tail}`
      );
    }

    case QUERY.REMAINING: {
      const n = openLines(items).length;
      if (n <= 1) return LAST_ITEM[l];
      return say(
        l,
        `${n} items left, including this one.`,
        `इसे मिलाकर ${n} सामान बाकी हैं।`,
        `ఇది కలిపి ${n} వస్తువులు మిగిలాయి.`
      );
    }

    default:
      return "";
  }
}