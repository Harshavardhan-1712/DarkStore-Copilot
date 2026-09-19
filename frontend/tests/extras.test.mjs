// Run with:  node --test frontend/tests/extras.test.mjs   (frontend/package.json needs "type": "module")
import test from "node:test";
import assert from "node:assert/strict";

import { describeItem, describeBatchStop, allDoneLine, locationPhrase } from "../src/readout.js";
import { classifyQuery, answerQuery, QUERY } from "../src/voiceQueries.js";
import { buildSubstitutionMessage, rupees } from "../src/customerMessage.js";

const MILK = { sku_id: "SKU_MILK", name: "Amul Milk 500ml", aisle: "2", shelf: "B", qty: 2, state: "PENDING" };
const RICE = { sku_id: "SKU_RICE", name: "Sona Rice 1kg", aisle: "5", shelf: "A", qty: 1, state: "PENDING" };
const EGGS = { sku_id: "SKU_EGGS", name: "Eggs 6", aisle: "7", shelf: "C", qty: 1, state: "PENDING" };

// --- read-out ----------------------------------------------------------------------------------
test("read-out names the item, where it is and how many, in each language", () => {
  assert.equal(
    describeItem(MILK, "en", "next"),
    "Next item: Amul Milk 500ml. Aisle 2, shelf B. Pick 2."
  );
  assert.equal(describeItem(MILK, "en", "first").startsWith("First item:"), true);
  assert.match(describeItem(MILK, "hi"), /^अगला सामान: Amul Milk 500ml।/);
  assert.match(describeItem(MILK, "hi"), /आइल 2, शेल्फ B।/);
  assert.match(describeItem(MILK, "te"), /^తదుపరి వస్తువు: Amul Milk 500ml\./);
  assert.match(describeItem(MILK, "te"), /2 తీసుకోండి\.$/);
});

test("read-out copes with a missing location and an unknown language", () => {
  assert.equal(locationPhrase({}, "en"), "");
  assert.equal(describeItem({ name: "Salt" }, "en"), "Next item: Salt. Pick 1.");
  assert.equal(describeItem(MILK, "xx"), describeItem(MILK, "en"));
  assert.equal(describeItem(null, "en"), "");
  assert.ok(allDoneLine("hi").includes("बैग"));
});

test("batch stop tells the picker which tote gets what", () => {
  const stop = {
    name: "Amul Milk", aisle: "2", shelf: "B", total_qty: 3,
    allocations: [{ tote: "A", qty: 1 }, { tote: "B", qty: 2 }],
  };
  assert.equal(describeBatchStop(stop, "en"), "Aisle 2, shelf B. Amul Milk, 3 units. tote A 1, tote B 2.");
  assert.match(describeBatchStop(stop, "hi"), /3 यूनिट/);
});

// --- query classification ----------------------------------------------------------------------
const cases = {
  [QUERY.REPEAT]: ["repeat", "say again", "once more", "malli", "dobara", "फिर से", "మళ్ళీ"],
  [QUERY.WHERE]: ["where is it", "which aisle", "shelf", "ekkada", "kahan hai", "कहाँ है", "ఎక్కడ"],
  [QUERY.QUANTITY]: ["how many", "quantity", "enni", "kitne", "कितने", "ఎన్ని"],
  [QUERY.NAME]: ["what item", "which item", "item name", "peru", "naam", "नाम", "పేరు"],
  [QUERY.NEXT]: ["what's next", "next", "next item", "agla", "अगला", "తర్వాత"],
  [QUERY.REMAINING]: ["how many left", "items left", "kitne bache", "कितने बचे", "ఇంకా ఎన్ని", "remaining"],
};
for (const [kind, phrases] of Object.entries(cases)) {
  test(`classifies ${kind}`, () => {
    for (const p of phrases) assert.equal(classifyQuery(p), kind, p);
  });
}

test("punctuation, case and zero-width characters do not matter", () => {
  assert.equal(classifyQuery("Where is it?"), QUERY.WHERE);
  assert.equal(classifyQuery("WHAT’S NEXT"), QUERY.NEXT);
  assert.equal(classifyQuery("कहाँ\u200b है।"), QUERY.WHERE);
});

test("commands are never mistaken for queries", () => {
  // Each of these is a real command (or near one) and must reach the normal intent path.
  for (const text of [
    "picked", "done", "got it", "out of stock", "milk ledu", "ledu", "nahi hai", "nahi mila",
    "teesukunnanu", "mil gaya", "le liya", "evaro teesukunnaru", "kisi ne le liya", "someone took it",
    "it is not there where is it", "already taken", "లేదు", "తీసుకున్నాను", "मिल गया", "नहीं है",
  ]) {
    assert.equal(classifyQuery(text), null, text);
  }
});

test("chatter, long sentences and empty input are ignored", () => {
  for (const text of ["", "   ", "hello there", "arre bhai wo customer ne bola tha kahan hai", "sare", "ok"]) {
    assert.equal(classifyQuery(text), null, text);
  }
});

// --- answers -----------------------------------------------------------------------------------
const ctx = (over = {}) => ({
  lang: "en", item: MILK, index: 0,
  items: [MILK, { ...RICE, state: "PICKED" }, EGGS], ...over,
});

test("answers come from the order the store returned", () => {
  assert.equal(answerQuery(QUERY.WHERE, ctx()), "Aisle 2, shelf B.");
  assert.equal(answerQuery(QUERY.QUANTITY, ctx()), "Pick 2 of Amul Milk 500ml.");
  assert.equal(answerQuery(QUERY.NAME, ctx()), "It is Amul Milk 500ml.");
  assert.equal(answerQuery(QUERY.REPEAT, ctx()), describeItem(MILK, "en", "again"));
});

test("next skips lines that are already closed", () => {
  assert.equal(answerQuery(QUERY.NEXT, ctx()), "After this: Eggs 6. Aisle 7, shelf C.");
});

test("remaining counts open lines including the current one", () => {
  assert.equal(answerQuery(QUERY.REMAINING, ctx()), "2 items left, including this one.");
});

test("the last item is announced as the last item", () => {
  const last = ctx({ item: EGGS, index: 2 });
  assert.equal(answerQuery(QUERY.NEXT, last), "This is the last item.");
  assert.equal(answerQuery(QUERY.REMAINING, ctx({ items: [{ ...MILK, state: "PICKED" }, EGGS], item: EGGS, index: 1 })), "This is the last item.");
});

test("answers exist in Hindi and Telugu and never come back empty", () => {
  for (const lang of ["hi", "te"]) {
    for (const kind of Object.values(QUERY)) {
      assert.ok(answerQuery(kind, ctx({ lang })).length > 0, `${lang} ${kind}`);
    }
  }
  assert.match(answerQuery(QUERY.WHERE, ctx({ lang: "hi" })), /आइल 2, शेल्फ B।/);
});

test("no current item and no location are handled", () => {
  assert.equal(answerQuery(QUERY.WHERE, ctx({ item: null })), "There is no item to read right now.");
  assert.equal(answerQuery(QUERY.WHERE, ctx({ item: { name: "Salt" } })), "I don't have a location for this item.");
});

// --- customer message --------------------------------------------------------------------------
const swap = (over = {}) => ({
  order: { order_id: "ORD-DEMO-101", invoice_total: "412", customer_name: "Priya Nair" },
  original: { name: "Amul Milk 500ml", unit_price: "30", qty: 2 },
  replacement: { name: "Nandini Milk 500ml", unit_price: "31", qty: 2 },
  previousTotal: "410",
  ...over,
});

test("substitution message explains the swap with figures from the order", () => {
  const msg = buildSubstitutionMessage(swap());
  assert.match(msg.text, /^Hi Priya, a quick update on order ORD-DEMO-101\./);
  assert.match(msg.text, /Amul Milk 500ml wasn't available, so we're sending Nandini Milk 500ml instead/);
  assert.match(msg.text, /Price: ₹30 → ₹31 each\./);
  assert.match(msg.text, /Your bill is now ₹412 \(\+₹2\)\./);
  assert.match(msg.text, /reply NO/);
  assert.equal(msg.delta, 2);
});

test("cheaper, same-price and unknown-price swaps read correctly", () => {
  assert.match(buildSubstitutionMessage(swap({ previousTotal: "415" })).text, /\(−₹3\)/);
  assert.match(buildSubstitutionMessage(swap({ previousTotal: "412" })).text, /Your bill stays at ₹412\./);
  const noPrices = buildSubstitutionMessage(swap({
    original: { name: "A" }, replacement: { name: "B" }, previousTotal: undefined,
  }));
  assert.doesNotMatch(noPrices.text, /Price:/);
  assert.match(noPrices.text, /Your bill is now ₹412\./);
});

test("no name means a plain greeting, and missing data means no message", () => {
  const anon = buildSubstitutionMessage(swap({ order: { order_id: "O1", invoice_total: 10 } }));
  assert.match(anon.text, /^Hi, a quick update on order O1\./);
  assert.equal(buildSubstitutionMessage(swap({ original: null })), null);
  assert.equal(buildSubstitutionMessage(swap({ order: {} })), null);
});

test("rupee formatting", () => {
  assert.equal(rupees(30), "₹30");
  assert.equal(rupees("62.5"), "₹62.50");
  assert.equal(rupees("abc"), "");
});
