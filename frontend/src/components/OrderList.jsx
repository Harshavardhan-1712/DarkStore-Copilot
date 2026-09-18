import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";

const STATUS_LABELS = {
  CREATED: "Queued",
  PICKING: "Picking",
  BAG_VERIFICATION: "Bag check",
  READY_FOR_PICKUP: "Ready for rider",
  DISPATCHED: "Dispatched",
  CANCELLED: "Cancelled",
};

const EXCEPTION_COPY = {
  OOS: "stockout",
  CONTESTED: "already taken",
  EXCEPTION: "flagged",
};

/** Shift board. Every row is a real order from the store backend. */
export default function OrderList({ onOpen, onBack, busy }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setError("");
      const res = await api.listOrders();
      setRows(res.orders || []);
    } catch (e) {
      setError(e.message || "Could not load orders.");
      setRows([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="orders-view">
      <header className="orders-head">
        <div>
          <h1>Shift board</h1>
          <p>Open an order to start picking.</p>
        </div>
        <button className="back" onClick={onBack}>
          ← Home
        </button>
      </header>

      {error && <div className="inline-error">{error}</div>}
      {!rows && !error && <div className="dash-loading">Loading orders…</div>}
      {rows?.length === 0 && (
        <div className="empty">
          <b>No orders in the store yet.</b>
          <span>Open the demo control center and reset the demo to seed them.</span>
        </div>
      )}

      <div className="order-rows">
        {(rows || []).map((o) => (
          <button
            key={o.order_id}
            className="order-row"
            disabled={busy || o.status === "DISPATCHED"}
            onClick={() => onOpen(o.order_id)}
          >
            <div className="order-id">
              <b>{o.order_id}</b>
              <small>{o.customer_ref || "Store order"}</small>
            </div>
            <div className="order-mid">
              <span className="status-pill" data-status={o.status}>
                {STATUS_LABELS[o.status] || o.status}
              </span>
              {o.exception_states?.map((s) => (
                <span key={s} className="ex-pill" data-kind={s}>
                  {EXCEPTION_COPY[s] || s}
                </span>
              ))}
            </div>
            <div className="order-progress">
              <b>
                {o.items_done}/{o.items_total}
              </b>
              <span>
                <i style={{ width: `${o.items_total ? (o.items_done / o.items_total) * 100 : 0}%` }} />
              </span>
              <small>{o.open_item ? `next: ${o.open_item}` : "all lines closed"}</small>
            </div>
          </button>
        ))}
      </div>

      <button className="btn plain" style={{ width: "100%", marginTop: 12 }} onClick={load}>
        Refresh board
      </button>
    </div>
  );
}
