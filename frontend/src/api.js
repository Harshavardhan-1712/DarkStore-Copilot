const BASE = import.meta.env.VITE_API_BASE || "";
export const PICKER_ID = import.meta.env.VITE_PICKER_ID || "PICKER-07";

class ApiError extends Error {
  constructor(payload, status) {
    super(payload?.error?.message || "Request failed");
    this.code = payload?.error?.code || "NETWORK";
    this.detail = payload?.error?.detail || {};
    this.status = status;
  }
}

/** Every mutating call carries one of these so a retry can never execute twice. */
export function newActionId(kind = "action") {
  return `${PICKER_ID}-${kind}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
}

async function call(path, { method = "GET", body, timeout = 20000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  let res;
  try {
    res = await fetch(BASE + path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
  } catch (err) {
    throw new ApiError(
      {
        error: {
          message:
            err?.name === "AbortError"
              ? "The store server did not respond in time. Check the connection and try again."
              : "No connection to the store server. Start the local API, or check Wi-Fi.",
        },
      },
      0
    );
  } finally {
    clearTimeout(timer);
  }
  const payload = await res.json().catch(() => ({}));
  if (!res.ok || payload.ok === false) throw new ApiError(payload, res.status);
  return payload;
}

export const api = {
  createOrder: (order) => call("/orders", { method: "POST", body: order }),
  listOrders: () => call("/orders"),
  getOrder: (id) => call(`/orders/${id}`),
  start: (id) => call(`/orders/${id}/start`, { method: "POST", body: { picker_id: PICKER_ID } }),
  pick: (id, index, sku, clientActionId) =>
    call(`/orders/${id}/pick`, {
      method: "POST",
      body: { index, sku_id: sku, picker_id: PICKER_ID, client_action_id: clientActionId },
    }),
  exception: (id, index, kind, note, clientActionId) =>
    call(`/orders/${id}/exception`, {
      method: "POST",
      body: { index, kind, note, picker_id: PICKER_ID, client_action_id: clientActionId },
    }),
  substitutes: (id) => call(`/orders/${id}/substitutes`),
  substitute: (id, index, sku, clientActionId) =>
    call(`/orders/${id}/substitute`, {
      method: "POST",
      body: { index, substitute_sku: sku, picker_id: PICKER_ID, client_action_id: clientActionId },
    }),
  intent: (id, index, utterance, lang, simulated, clientActionId) =>
    call(`/orders/${id}/intent`, {
      method: "POST",
      body: {
        index,
        utterance,
        lang,
        picker_id: PICKER_ID,
        simulated_intent: simulated,
        client_action_id: clientActionId,
      },
    }),
  verifyItem: (id, index, sku, imageBase64, format) =>
    call(`/orders/${id}/verify-item`, {
      method: "POST",
      body: { index, sku_id: sku, image_base64: imageBase64, image_format: format },
      timeout: 35000,
    }),
  verifyBag: (id, imageBase64, format) =>
    call(`/orders/${id}/verify-bag`, {
      method: "POST",
      body: { image_base64: imageBase64, image_format: format },
      timeout: 35000,
    }),
  dispatch: (id, token) =>
    call(`/orders/${id}/dispatch`, { method: "POST", body: { handover_token: token } }),
  orderAudit: (id) => call(`/orders/${id}/audit`),
  dashboard: () => call("/dashboard"),
  analysis: () => call("/analysis"),
  inventory: () => call("/inventory"),
  speech: (text, lang) => call("/speech", { method: "POST", body: { text, lang } }),
  demoScenarios: () => call("/demo/scenarios"),
  demoReset: () => call("/demo/reset", { method: "POST", body: {}, timeout: 40000 }),
  demoScenario: (scenario) =>
    call("/demo/scenario", { method: "POST", body: { scenario }, timeout: 30000 }),
};

export { ApiError };
