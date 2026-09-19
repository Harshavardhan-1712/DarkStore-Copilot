// What the app says out loud about a pick line. Pure functions, no browser APIs, so they run
// under node tests. The text is handed to the existing speak()/Polly path in App.jsx.

const INTRO = {
  en: { first: "First item:", next: "Next item:", again: "Current item:" },
  hi: { first: "पहला सामान:", next: "अगला सामान:", again: "मौजूदा सामान:" },
  te: { first: "మొదటి వస్తువు:", next: "తదుపరి వస్తువు:", again: "ప్రస్తుత వస్తువు:" },
};
const AISLE = { en: "Aisle", hi: "आइल", te: "ఐల్" };
const SHELF = { en: "shelf", hi: "शेल्फ", te: "షెల్ఫ్" };
const TOTE = { en: "tote", hi: "टोट", te: "టోట్" };
const STOP = { en: ".", hi: "।", te: "." };

const ALL_DONE = {
  en: "All items are picked. Photograph the bag to verify.",
  hi: "सभी सामान हो गया। बैग की फोटो लेकर जाँच कीजिए।",
  te: "అన్ని వస్తువులు అయ్యాయి. బ్యాగ్ ఫోటో తీసి ధృవీకరించండి.",
};

const has = (v) => v !== undefined && v !== null && String(v).trim() !== "";
const pick = (table, lang) => (table[lang] ? lang : "en");

export function locationPhrase(item, lang = "te") {
  const l = pick(AISLE, lang);
  const parts = [];
  if (has(item?.aisle)) parts.push(`${AISLE[l]} ${item.aisle}`);
  if (has(item?.shelf)) parts.push(`${SHELF[l]} ${item.shelf}`);
  return parts.join(", ");
}

export function quantityPhrase(item, lang = "te") {
  const qty = has(item?.qty) ? item.qty : 1;
  const l = pick(AISLE, lang);
  if (l === "hi") return `${qty} लीजिए।`;
  if (l === "te") return `${qty} తీసుకోండి.`;
  return `Pick ${qty}.`;
}

/** kind: "first" | "next" | "again". Reads name, location and quantity, nothing else. */
export function describeItem(item, lang = "te", kind = "next") {
  if (!item) return "";
  const l = pick(INTRO, lang);
  const stop = STOP[l];
  const head = `${INTRO[l][kind] || INTRO[l].next} ${item.name || ""}`.trim() + stop;
  const where = locationPhrase(item, l);
  return [head, where && where + stop, quantityPhrase(item, l)].filter(Boolean).join(" ");
}

export function allDoneLine(lang = "te") {
  return ALL_DONE[pick(ALL_DONE, lang)];
}

const UNITS = {
  en: (n) => `${n} unit${Number(n) === 1 ? "" : "s"}`,
  hi: (n) => `${n} यूनिट`,
  te: (n) => `${n} యూనిట్లు`,
};

/** One shelf stop of a batch walk: where to go, how many in total, and which tote gets what. */
export function describeBatchStop(stop, lang = "te") {
  if (!stop) return "";
  const l = pick(UNITS, lang);
  const end = STOP[l];
  const where = locationPhrase(stop, l);
  const totes = (stop.allocations || [])
    .map((a) => `${TOTE[l]} ${a.tote} ${a.qty}`)
    .join(", ");
  const total = stop.total_qty ?? (stop.allocations || []).reduce((s, a) => s + Number(a.qty || 0), 0);
  return [
    where && where + end,
    `${stop.name || ""}, ${UNITS[l](total)}${end}`.trim(),
    totes && totes + end,
  ]
    .filter(Boolean)
    .join(" ");
}
