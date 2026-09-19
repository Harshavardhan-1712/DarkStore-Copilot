// Auto customer message for a substitution.
//
// By the time this runs the backend has already accepted the swap, which means store policy has
// checked category, live stock and price bounds. So the message only has to explain it. Every
// figure comes from the order the store just returned; nothing here is generated or guessed.

const LOG_KEY = "darkstore-customer-messages";
const LOG_CAP = 50;

const num = (v) => {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

const round2 = (n) => Math.round(n * 100) / 100;

export function rupees(value) {
  const n = num(value);
  if (n === null) return "";
  const r = round2(n);
  return `₹${Number.isInteger(r) ? r : r.toFixed(2)}`;
}

/**
 * order:         the order as returned after the swap (order_id, invoice_total, customer_name?)
 * original:      the line as it was before the swap (name, unit_price, qty)
 * replacement:   the same line after the swap (name, unit_price, qty)
 * previousTotal: invoice_total before the swap
 * Returns null when there is nothing safe to say.
 */
export function buildSubstitutionMessage({ order, original, replacement, previousTotal }) {
  if (!order?.order_id || !original?.name || !replacement?.name) return null;

  const first = String(order.customer_name || "").trim().split(/\s+/)[0];
  const oldUnit = num(original.unit_price);
  const newUnit = num(replacement.unit_price);
  const total = num(order.invoice_total);
  const before = num(previousTotal);
  const delta = total !== null && before !== null ? round2(total - before) : null;

  const lines = [
    `Hi${first ? " " + first : ""}, a quick update on order ${order.order_id}.`,
    `${original.name} wasn't available, so we're sending ${replacement.name} instead, a like-for-like swap.`,
  ];

  if (oldUnit !== null && newUnit !== null) {
    lines.push(`Price: ${rupees(oldUnit)} → ${rupees(newUnit)} each.`);
  }
  if (total !== null) {
    if (delta === null) lines.push(`Your bill is now ${rupees(total)}.`);
    else if (delta === 0) lines.push(`Your bill stays at ${rupees(total)}.`);
    else lines.push(`Your bill is now ${rupees(total)} (${delta > 0 ? "+" : "−"}${rupees(Math.abs(delta))}).`);
  }
  lines.push("If you'd rather not have it, reply NO and we'll take it off your bill.");

  return {
    text: lines.join("\n"),
    order_id: order.order_id,
    to: order.customer_name || null,
    original_name: original.name,
    replacement_name: replacement.name,
    delta,
    total,
    channel: "demo",
    created_at: new Date().toISOString(),
  };
}

/** Keeps the last few messages on this device so the demo can show what was sent. */
export function logCustomerMessage(message) {
  try {
    const log = JSON.parse(localStorage.getItem(LOG_KEY) || "[]");
    log.push(message);
    localStorage.setItem(LOG_KEY, JSON.stringify(log.slice(-LOG_CAP)));
  } catch (_) {
    /* storage unavailable: the on-screen card is still shown */
  }
}