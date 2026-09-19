// Client for the additive endpoints served by backend/app/extras.py. Same base URL as api.js
// (VITE_API_BASE); everything else about the existing API is left alone.
const BASE = (import.meta.env?.VITE_API_BASE ?? "").replace(/\/+$/, "");

async function request(method, path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try {
    data = await res.json();
  } catch (_) {
    /* empty or non-JSON body */
  }
  if (!res.ok) throw new Error(data?.error || data?.message || `Request failed (${res.status})`);
  return data;
}

export const extras = {
  /**
   * The picker found the shelf empty. Logged as an inventory-audit signal; changes no stock.
   * { sku_id, lines: [{ order_id, index }], source: "voice" | "button" | "batch", picker_id, reason? }
   */
  reportOutOfStock: (payload) => request("POST", "/phantom-stock/report", payload),

  /** Recount queue: SKUs the system shows in stock but pickers found empty. */
  phantomQueue: () => request("GET", "/phantom-stock"),

  /** Read-only walk plan across open orders. { order_ids?, max_orders? } */
  planBatch: (payload = {}) => request("POST", "/batches/plan", payload),
};