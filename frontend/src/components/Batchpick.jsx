import { useCallback, useEffect, useRef, useState } from "react";
import { api, newActionId, PICKER_ID } from "../api.js";
import { extras } from "../hooks/extrasApi.js";
import { describeBatchStop } from "../hooks/readout.js";

/**
 * Batch picking: one walk, several orders.
 *
 * The plan is read-only (POST /batches/plan). Confirming a stop records one ordinary pick per
 * order line through the same api.pick the single-order screen uses, so the state machine, stock
 * and audit trail behave exactly as before. A shelf that turns out empty is not resolved here:
 * it is reported (an inventory-audit signal) and handed back to the normal order screen, where
 * substitution and the customer message already live.
 */
export default function BatchPick({ onBack, onOpenOrder, flash, lang, online, speak }) {
  const [plan, setPlan] = useState(null);
  const [orderIds, setOrderIds] = useState(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const [skipped, setSkipped] = useState([]);
  const [deferred, setDeferred] = useState([]); // lines reported empty: [{ order_id, index, sku_id, name }]
  const spoken = useRef(null);

  const load = useCallback(async (ids) => {
    setLoading(true);
    setError("");
    try {
      const next = await extras.planBatch(ids ? { order_ids: ids } : {});
      setPlan(next);
      if (!ids) setOrderIds(next.orders.map((o) => o.order_id));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(null);
  }, [load]);

  // Lines already reported empty stay out of the walk even though the order still lists them.
  const deferredKeys = new Set(deferred.map((d) => `${d.order_id}:${d.index}`));
  const stops = (plan?.stops || [])
    .map((s) => {
      const allocations = s.allocations.filter((a) => !deferredKeys.has(`${a.order_id}:${a.index}`));
      return { ...s, allocations, total_qty: allocations.reduce((n, a) => n + Number(a.qty || 0), 0) };
    })
    .filter((s) => s.allocations.length > 0);
  const ordered = [...stops.filter((s) => !skipped.includes(s.sku_id)), ...stops.filter((s) => skipped.includes(s.sku_id))];
  const stop = ordered[0];

  // Say where to go when the stop changes. Only when read-aloud is on (speak is null otherwise).
  useEffect(() => {
    if (!speak || !stop || spoken.current === stop.sku_id) return;
    spoken.current = stop.sku_id;
    Promise.resolve(speak(describeBatchStop(stop, lang))).catch(() => {});
  }, [speak, stop, lang]);

  const pickStop = async () => {
    if (!stop || working) return;
    if (!online) {
      flash("Batch picking needs a live store connection.", "error");
      return;
    }
    setWorking(true);
    const failed = [];
    for (const a of stop.allocations) {
      try {
        await api.pick(a.order_id, a.index, stop.sku_id, newActionId("batch"));
      } catch (err) {
        failed.push(`${a.order_id}: ${err.message}`);
      }
    }
    setWorking(false);
    if (failed.length) flash(`Not recorded. ${failed.join("; ")}`, "error");
    await load(orderIds); // whatever did not go through is still open in the fresh plan
  };

  const shelfEmpty = async () => {
    if (!stop || working) return;
    setWorking(true);
    try {
      const out = await extras.reportOutOfStock({
        sku_id: stop.sku_id,
        lines: stop.allocations.map(({ order_id, index }) => ({ order_id, index })),
        source: "batch",
        picker_id: PICKER_ID,
      });
      if (out?.finding === "PHANTOM_STOCK_SUSPECT" && !out.deduped) {
        flash(`Inventory audit: the system shows ${out.system_qty} of ${out.name}. Logged for a recount.`);
      }
    } catch (_) {
      /* reporting must never block picking */
    }
    setDeferred((d) => [...d, ...stop.allocations.map((a) => ({ ...a, sku_id: stop.sku_id, name: stop.name }))]);
    setWorking(false);
  };

  const skip = () => setSkipped((s) => [...s.filter((id) => id !== stop.sku_id), stop.sku_id]);

  const deferredByOrder = deferred.reduce((acc, d) => {
    (acc[d.order_id] = acc[d.order_id] || []).push(d.name);
    return acc;
  }, {});
  const totals = plan?.totals;

  return (
    <div className="app">
      <div className="topbar">
        <button className="topbar-back" onClick={onBack} aria-label="Back">
          ←
        </button>
        <span>
          <b>Batch pick</b> · {plan?.orders?.length ?? 0} orders
        </span>
        <span className="topbar-links">
          <button onClick={() => load(orderIds)} disabled={loading || working}>
            {loading ? "…" : "Refresh"}
          </button>
        </span>
      </div>

      <div className="picker-body x-page">
        {error && <p className="x-error">{error}</p>}
        {loading && !plan && <p className="x-lede">Planning the walk…</p>}

        {plan && (
          <>
            <div className="x-totes">
              {plan.orders.map((o) => (
                <span key={o.order_id} className="x-tote">
                  <b>{o.tote}</b> {o.order_id}
                  <small>{o.open_lines} left</small>
                </span>
              ))}
            </div>

            {totals && totals.lines > 0 && (
              <p className="x-lede">
                {totals.lines} lines become {totals.stops} shelf stops
                {totals.visits_saved > 0 ? `, ${totals.visits_saved} fewer trips` : ""}.
              </p>
            )}
          </>
        )}

        {stop && (
          <section className="x-stop" aria-live="polite">
            <div className="x-loc">
              Aisle {stop.aisle ?? "?"} · Shelf {stop.shelf ?? "?"}
            </div>
            <h2>{stop.name}</h2>
            <div className="x-total">×{stop.total_qty}</div>
            <ul className="x-alloc">
              {stop.allocations.map((a) => (
                <li key={`${a.order_id}:${a.index}`}>
                  <b>Tote {a.tote}</b>
                  <span>{a.order_id}</span>
                  <em>×{a.qty}</em>
                </li>
              ))}
            </ul>
            {stop.short && (
              <p className="x-warn">
                The system shows {stop.stock_qty} on the shelf and this walk needs {stop.total_qty}.
              </p>
            )}
            <button className="btn go" disabled={working || !online} onClick={pickStop}>
              {working ? "Recording…" : `Picked all ${stop.total_qty}`}
            </button>
            <div className="x-row">
              <button className="btn plain" disabled={working} onClick={shelfEmpty}>
                Shelf is empty
              </button>
              {ordered.length > 1 && (
                <button className="btn plain" disabled={working} onClick={skip}>
                  Skip for now
                </button>
              )}
            </div>
          </section>
        )}

        {plan && !stop && !loading && (
          <section className="x-stop">
            <h2>{plan.orders.length ? "Walk complete" : "No open orders to batch"}</h2>
            <p className="x-lede">
              {plan.orders.length
                ? "Every line that could be picked has been recorded. Open each order to verify the bag."
                : "Open orders will appear here as they arrive."}
            </p>
            {plan.orders.map((o) => (
              <button key={o.order_id} className="btn plain" onClick={() => onOpenOrder(o.order_id)}>
                Open {o.order_id} (tote {o.tote})
              </button>
            ))}
          </section>
        )}

        {Object.keys(deferredByOrder).length > 0 && (
          <section className="x-deferred">
            <h3>Reported empty: finish these in the order screen</h3>
            {Object.entries(deferredByOrder).map(([orderId, names]) => (
              <button key={orderId} className="btn plain compact" onClick={() => onOpenOrder(orderId)}>
                {orderId}: {names.join(", ")}
              </button>
            ))}
          </section>
        )}

        {ordered.length > 1 && (
          <section className="x-later">
            <h3>Then</h3>
            <ul>
              {ordered.slice(1, 6).map((s) => (
                <li key={s.sku_id}>
                  <span>
                    Aisle {s.aisle ?? "?"} · {s.shelf ?? "?"}
                  </span>
                  <b>{s.name}</b>
                  <em>×{s.total_qty}</em>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  );
}