import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";

const pct = (v) => (v == null ? "—" : `${v}%`);
const secs = (v) => (v == null ? "—" : `${v}s`);

function Stat({ label, value, note }) {
  return (
    <div className="an-stat">
      <span>{label}</span>
      <strong>{value ?? "—"}</strong>
      {note && <small>{note}</small>}
    </div>
  );
}

function Bar({ label, value, total, note, tone }) {
  const width = total ? Math.round((value / total) * 100) : 0;
  return (
    <div className="an-bar">
      <div className="an-bar-top">
        <span>{label}</span>
        <b>{value}</b>
      </div>
      <div className="an-bar-track">
        <i style={{ width: `${width}%` }} data-tone={tone} />
      </div>
      {note && <small>{note}</small>}
    </div>
  );
}

/**
 * Analysis workspace. The control tower answers "what needs attention now"; this answers
 * "what has been happening". Every figure is computed by the backend from the same orders
 * and audit records the picker app produced.
 */
export default function Analysis({ onBack }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setError("");
      setData(await api.analysis());
    } catch (e) {
      setError(e.message || "Analysis unavailable.");
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [load]);

  if (error && !data)
    return (
      <div className="dashboard">
        <button className="back" onClick={onBack}>
          ← Picker
        </button>
        <div className="dash-error">{error}</div>
        <button className="btn go compact" onClick={load}>
          Try again
        </button>
      </div>
    );
  if (!data)
    return (
      <div className="dashboard">
        <button className="back" onClick={onBack}>
          ← Picker
        </button>
        <div className="dash-loading">Building the analysis…</div>
      </div>
    );

  const { unavailability: un, recovery, verification: ver, ai_policy: ai, pick_cycle: cycle } = data;

  return (
    <div className="dashboard analysis">
      <header className="ops-header">
        <div>
          <div className="brand-line">
            <span className="live-dot" /> Analysis workspace
          </div>
          <h1>What has been happening</h1>
          <p>
Operational intelligence across {data.throughput?.orders_total ?? 0} orders and{" "}
            {data.dataset.audit_records} audit records.
          </p>
        </div>
        <div className="header-actions">
          <button className="back" onClick={onBack}>
            ← Picker
          </button>
        </div>
      </header>

      {data.dataset.is_demo_data && (
        <div className="dash-warning">
          {data.dataset.label}: {data.dataset.orders} seeded orders. The shape of the analysis is
          real; the sample is small by design, so read directions rather than absolute rates.
        </div>
      )}
      {error && <div className="dash-warning">Refresh failed. Showing the last good snapshot.</div>}

      <section className="an-stats">
        <Stat label="Items picked" value={data.throughput.items_picked} note={`${data.throughput.dispatched} orders dispatched`} />
        <Stat label="Average pick cycle" value={secs(cycle.avg_seconds)} note={`${cycle.samples} completed orders`} />
        <Stat label="Fastest / slowest" value={`${secs(cycle.fastest_seconds)} / ${secs(cycle.slowest_seconds)}`} note="start → bag check" />
        <Stat label="Recovery rate" value={pct(recovery.recovery_rate)} note={`${recovery.substitutions_applied} substitutions applied`} />
      </section>

      <div className="tower-grid">
        <section className="ops-card">
          <div className="section-head">
            <div>
              <h2>Why items were unavailable</h2>
              <span className="section-note">
                Shortage and coordination failures need different fixes, so they are counted apart.
              </span>
            </div>
          </div>
          <Bar label="Shelf stockout" value={un.stockout} total={un.total || 1} tone="red"
               note="Replenishment problem: the store has none on hand." />
          <Bar label="Concurrent pick" value={un.concurrent_pick} total={un.total || 1} tone="orange"
               note="Coordination problem: another picker took this order's unit." />
          <div className="an-rows">
            <div>
              <span>Duplicate picks prevented</span>
              <b>{un.duplicate_prevented}</b>
            </div>
            <div>
              <span>Manual resolutions required</span>
              <b>{recovery.manual_resolution_required}</b>
            </div>
            <div>
              <span>Substitutions refused by policy</span>
              <b>{recovery.substitutions_rejected}</b>
            </div>
          </div>
        </section>

        <section className="ops-card">
          <div className="section-head">
            <div>
              <h2>AI and policy decisions</h2>
              <span className="section-note">{ai.total_decisions} decisions recorded</span>
            </div>
          </div>
          <div className="decision-score">
            <div>
              <strong>{ai.approved}</strong>
              <span>approved and committed</span>
            </div>
            <div>
              <strong>{ai.blocked}</strong>
              <span>blocked by policy</span>
            </div>
          </div>
          <div className="decision-row">
            <span>Guardrail rejections (invalid AI output)</span>
            <b>{ai.guardrail_rejections}</b>
          </div>
          {(Array.isArray(ai.interpreters) ? ai.interpreters : []).map((i) => (
            <div className="decision-row" key={i.source}>
              <span>
                Actions via{" "}
                {i.source === "deterministic-fast-path"
                  ? "on-device fast path"
                  : i.source === "amazon-bedrock"
                  ? "Amazon Bedrock"
                  : i.source}
              </span>
              <b>{i.count}</b>
            </div>
          ))}
          <div className="decision-note">
            The AI interprets and proposes. Policy validates against category, stock, price and
            order state. Only the backend commits.
          </div>
        </section>
      </div>

      <div className="tower-grid lower">
        <section className="ops-card">
          <div className="section-head">
            <div>
              <h2>Verification quality</h2>
            </div>
          </div>
          <div className="an-rows">
            <div>
              <span>Product visual checks</span>
              <b>
                {ver.product_matches}/{ver.product_attempts} matched ({pct(ver.product_match_rate)})
              </b>
            </div>
            <div>
              <span>Bag checks passed</span>
              <b>
                {ver.bag_passes}/{ver.bag_attempts} ({pct(ver.bag_first_pass_rate)})
              </b>
            </div>
          </div>
          <div className="section-head" style={{ marginTop: 16 }}>
            <div>
              <h2>Aisle workload</h2>
            </div>
          </div>
         {(Array.isArray(data.aisle_workload) ? data.aisle_workload : []).map((a) => (
            <div className="aisle-row" key={a.aisle}>
              <div>
                <b>Aisle {a.aisle}</b>
                <small>
                  {a.units} units · {a.lines} lines · {a.exceptions} exceptions
                </small>
              </div>
              <div className="aisle-bar">
                <span style={{ width: `${a.percent}%` }} />
              </div>
            </div>
          ))}
        </section>

        <section className="ops-card">
          <div className="section-head">
            <div>
              <h2>Picker performance</h2>
              <span className="section-note">from audit actors</span>
            </div>
          </div>
          <div className="queue-table">
            <div className="queue-head picker-head">
              <span>Picker</span>
              <span>Picks</span>
              <span>Swaps</span>
              <span>Exceptions</span>
              <span>Taken</span>
            </div>
            {(Array.isArray(data.picker_performance) ? data.picker_performance : []).map((p) => (
              <div className="queue-row picker-row" key={p.picker_id}>
                <span className="picker-tag">{p.picker_id}</span>
                <b>{p.picks}</b>
                <b>{p.substitutions}</b>
                <b>{p.exceptions}</b>
                <b>{p.concurrent_reports}</b>
              </div>
            ))}
          </div>
          {cycle.per_order.length > 0 && (
            <>
              <div className="section-head" style={{ marginTop: 16 }}>
                <div>
                  <h2>Fastest orders</h2>
                </div>
              </div>
              <div className="an-rows">
                {cycle.per_order.map((c) => (
                  <div key={c.order_id}>
                    <span>{c.order_id}</span>
                    <b>{c.seconds}s</b>
                  </div>
                ))}
              </div>
            </>
          )}
        </section>
      </div>

      <section className="ops-card">
        <div className="section-head">
          <div>
            <h2>Decision log</h2>
            <span className="section-note">newest first</span>
          </div>
        </div>
       <ol className="decision-log">
  {(Array.isArray(data.decisions) ? data.decisions : []).map((d, i) => {
    const safeOutcome = String(d?.outcome || "UNKNOWN");

    return (
      <li
        key={d?.decision_id || d?.audit_id || i}
        data-outcome={safeOutcome}
      >
        <span className="dl-outcome">
          {safeOutcome.replaceAll("_", " ")}
        </span>

        <span className="dl-body">
          <b>{d?.label || "Backend decision"}</b>
          <i>
            {d?.order_id || "—"}
            {d?.sku_id ? ` · ${d.sku_id}` : ""}
            {d?.note ? ` · ${d.note}` : ""}
          </i>
        </span>
      </li>
    );
  })}
</ol>
      </section>

      <footer className="tower-footer">
        <span>
          <b>Data source:</b> order records and the append-only audit trail. No figure on this page
          is hard-coded.
        </span>
        <button className="btn plain compact" onClick={load}>
          Refresh
        </button>
      </footer>
    </div>
  );
}
