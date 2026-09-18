import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";

const ICONS = {
  ORDER_PICKING_STARTED: "▶",
  ITEM_PICKED: "✓",
  ITEM_OOS: "✕",
  ITEM_EXCEPTION: "⚠",
  ITEM_CONCURRENT_PICK: "👤",
  CONCURRENT_PICK_DUPLICATE_BLOCKED: "⛔",
  MANUAL_RESOLUTION_REQUIRED: "🛠",
  SUBSTITUTION_APPLIED: "↔",
  SUBSTITUTION_REJECTED: "🛡",
  GUARDRAIL_REJECTED: "🛡",
  BEDROCK_UNAVAILABLE: "⚡",
  PRODUCT_VERIFY_MATCH: "📷",
  PRODUCT_VERIFY_FAILED: "📷",
  ITEM_VERIFY_GUARDRAIL: "📷",
  BAG_VERIFY_VERIFIED: "👜",
  BAG_VERIFY_FAILED: "👜",
  DISPATCHED: "🚴",
};

/** Plain-language line for one audit record. Derived from the record, never invented. */
function describe(entry) {
  const d = entry.detail || {};
  switch (entry.event) {
    case "ORDER_PICKING_STARTED":
      return "Picking started";
    case "ITEM_PICKED":
      return `Picked ${d.sku_id} × ${d.qty ?? 1}${d.source === "voice" ? " by voice" : ""}`;
    case "ITEM_OOS":
      return `Stockout reported on ${d.sku_id}`;
    case "ITEM_EXCEPTION":
      return `Exception on ${d.sku_id}`;
    case "ITEM_CONCURRENT_PICK":
      return `AI → ITEM_ALREADY_TAKEN · policy → CONCURRENT_PICK · duplicate pick prevented`;
    case "CONCURRENT_PICK_DUPLICATE_BLOCKED":
      return `Repeat report on a ${d.line_state} line — no mutation applied`;
    case "MANUAL_RESOLUTION_REQUIRED":
      return `No approved substitute for ${d.sku_id} — flagged for manual resolution`;
    case "SUBSTITUTION_APPLIED":
      return `Substitution committed: ${d.original_sku} → ${d.substitute_sku} (₹${d.invoice_delta} on the bill)`;
    case "SUBSTITUTION_REJECTED":
      return `Policy refused ${d.attempted}: ${d.reason}`;
    case "GUARDRAIL_REJECTED":
      return `AI proposal blocked — ${d.reason}`;
    case "BEDROCK_UNAVAILABLE":
      return "Voice model unavailable — manual fallback offered";
    case "PRODUCT_VERIFY_MATCH":
      return `Product visual check matched (${Math.round((d.confidence || 0) * 100)}%)`;
    case "PRODUCT_VERIFY_FAILED":
      return `Product visual check did not match — ${d.verdict}`;
    case "ITEM_VERIFY_GUARDRAIL":
      return "Visual model output refused — retake requested";
    case "BAG_VERIFY_VERIFIED":
      return `Bag verified on attempt ${d.attempt}`;
    case "BAG_VERIFY_FAILED":
      return `Bag check failed on attempt ${d.attempt} — missing ${(d.missing_skus || []).join(", ") || "item"}`;
    case "DISPATCHED":
      return "Handed to the rider";
    default:
      return entry.event.replaceAll("_", " ").toLowerCase();
  }
}

const time = (ts) =>
  ts ? new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";

/** AI activity for the open order, straight from the backend audit trail. */
export default function ActivityFeed({ orderId, refreshKey, onClose }) {
  const [entries, setEntries] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!orderId) return;
    try {
      setError("");
      const res = await api.orderAudit(orderId);
      setEntries(res.audit || []);
    } catch (e) {
      setError(e.message || "Activity feed unavailable.");
    }
  }, [orderId]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  return (
    <div className="scrim" onClick={onClose}>
      <div className="sheet activity-sheet" onClick={(e) => e.stopPropagation()}>
        <div className="verify-head">
          <div>
            <h2>AI activity</h2>
            <p className="lede">Every AI and policy decision recorded against {orderId}.</p>
          </div>
          <button className="icon-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        {error && <div className="inline-error">{error}</div>}
        {!entries && !error && <div className="dash-loading">Loading activity…</div>}
        {entries?.length === 0 && (
          <div className="empty">
            <b>No activity yet.</b>
            <span>Pick an item or speak a command and it will appear here.</span>
          </div>
        )}

        {entries?.length > 0 && (
          <ol className="feed">
            {[...entries].reverse().map((e) => (
              <li key={e.audit_id} data-actor={e.actor === "bedrock" ? "ai" : "store"}>
                <span className="feed-time">{time(e.ts)}</span>
                <span className="feed-icon">{ICONS[e.event] || "·"}</span>
                <span className="feed-body">
                  <b>{e.event.replaceAll("_", " ")}</b>
                  <i>{describe(e)}</i>
                </span>
              </li>
            ))}
          </ol>
        )}

        <button className="btn plain" style={{ width: "100%", marginTop: 12 }} onClick={load}>
          Refresh activity
        </button>
      </div>
    </div>
  );
}
