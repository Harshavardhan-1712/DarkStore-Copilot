const KEY = "darkstore-copilot-offline-actions-v1";

function read() {
  try { return JSON.parse(localStorage.getItem(KEY) || "[]"); } catch (_) { return []; }
}
function write(items) { localStorage.setItem(KEY, JSON.stringify(items)); window.dispatchEvent(new Event("offline-queue-change")); }

export function getQueuedActions() { return read(); }
export function queueAction(action) {
  const items = read();
  if (!items.some((x) => x.client_action_id === action.client_action_id)) {
    items.push({ ...action, queued_at: Date.now() });
    write(items);
  }
  return items;
}
export function removeAction(clientActionId) {
  write(read().filter((x) => x.client_action_id !== clientActionId));
}
export function clearQueue() { write([]); }
