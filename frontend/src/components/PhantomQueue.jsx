import { useCallback, useEffect, useState } from "react";
import { extras } from "../hooks/extrasApi.js";

const money = (n) => `₹${Number(n || 0).toLocaleString("en-IN")}`;

/**
 * Inventory audit queue. Every "shelf is empty" report, by voice or button, lands here when the
 * system still shows stock. Nothing on this screen changes stock: a person recounts first.
 */
export default function PhantomQueue({ onBack }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setData(await extras.phantomQueue());
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const s = data?.summary;

  return (
    <div className="app">
      <div className="topbar">
        <button className="topbar-back" onClick={onBack} aria-label="Back">
          ←
        </button>
        <span>
          <b>Inventory audit queue</b>
        </span>
        <span className="topbar-links">
          <button onClick={load} disabled={loading}>
            {loading ? "…" : "Refresh"}
          </button>
        </span>
      </div>

      <div className="picker-body x-page">
        <p className="x-lede">
          Pickers found these shelves empty while the system still shows stock. Each report came
          free with a normal picking action.
        </p>

        {error && <p className="x-error">{error}</p>}

        {s && (
          <div className="x-stats">
            <div><b>{s.skus_flagged}</b><span>SKUs flagged</span></div>
            <div><b>{s.recount_now}</b><span>recount now</span></div>
            <div><b>{s.voice_reports}<small>/{s.reports}</small></b><span>found by voice</span></div>
            <div><b>{money(s.value_at_risk)}</b><span>on the books, not on shelf</span></div>
          </div>
        )}

        {data && data.queue.length === 0 && !error && (
          <p className="x-empty">Nothing to recount. Empty-shelf reports will appear here as pickers make them.</p>
        )}

        <ul className="x-queue">
          {(data?.queue || []).map((row) => (
            <li key={row.sku_id} data-status={row.status}>
              <div>
                <b>{row.name || row.sku_id}</b>
                <small>
                  {row.aisle ? `Aisle ${row.aisle}` : ""}
                  {row.shelf ? ` · shelf ${row.shelf}` : ""}
                </small>
              </div>
              <div className="x-queue-nums">
                <span>{row.reports} report{row.reports === 1 ? "" : "s"}</span>
                <span>system shows {row.system_qty}</span>
                <span>{money(row.value_at_risk)}</span>
              </div>
              <em>{row.status === "RECOUNT_NOW" ? "Recount now" : "Watch"}</em>
            </li>
          ))}
        </ul>

        {data && !data.durable && (
          <p className="x-note">Signals are held in memory on this server. Set PHANTOM_TABLE to keep them.</p>
        )}
      </div>
    </div>
  );
}
