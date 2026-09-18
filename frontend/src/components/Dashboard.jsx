import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api.js";

const STATUS = ["CREATED", "PICKING", "BAG_VERIFICATION", "READY_FOR_PICKUP", "DISPATCHED"];
const STATUS_LABELS = { CREATED: "Queued", PICKING: "Picking", BAG_VERIFICATION: "Bag check", READY_FOR_PICKUP: "Ready", DISPATCHED: "Dispatched" };
const money = (v) => `₹${Number(v || 0).toLocaleString("en-IN")}`;

function Metric({ label, value, note, tone = "default", icon }) {
  return <div className={`ops-metric ${tone}`}><div className="metric-top"><span>{label}</span><i>{icon}</i></div><strong>{value ?? "—"}</strong>{note && <small>{note}</small>}</div>;
}
function StatusPill({ status }) {
  const safeStatus = String(status || "UNKNOWN");
  return (
    <span className="status-pill" data-status={safeStatus}>
      {STATUS_LABELS[safeStatus] || safeStatus.replaceAll("_", " ")}
    </span>
  );
}
function formatTime(ts) { return ts ? new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—"; }

const SEV = { red: "🔴", orange: "🟠", yellow: "🟡", blue: "🔵" };

function LiveExceptions({ rows, onOpen }) {
  if (!rows?.length)
    return <div className="empty"><b>No open exceptions.</b><span>Every active line is clear.</span></div>;
  return <div className="exception-list">
    {rows.map((r, i) => <button className="exception-row" key={i} data-sev={r.severity} onClick={() => onOpen?.(r.order_id)}>
      <span className="ex-sev">{SEV[r.severity] || "•"}</span>
      <span className="ex-body"><b>{r.order_id}</b><i>{r.title}</i>{r.detail && <em>{r.detail}</em>}</span>
      <span className="ex-kind">{r.kind.replaceAll("_", " ")}</span>
    </button>)}
  </div>;
}

function DecisionFeed({ rows }) {
  if (!Array.isArray(rows) || rows.length === 0) {
    return (
      <div className="empty">
        <b>No decisions recorded yet.</b>
        <span>Speak a command or pick an item.</span>
      </div>
    );
  }

  return (
    <ol className="decision-log">
      {rows.map((d, i) => {
        const safeOutcome = String(d?.outcome || "UNKNOWN");
        const safeLabel = String(d?.label || "Backend decision");

        return (
          <li
            key={d?.decision_id || d?.audit_id || i}
            data-outcome={safeOutcome}
          >
            <span className="dl-outcome">
              {safeOutcome.replaceAll("_", " ")}
            </span>

            <span className="dl-body">
              <b>{safeLabel}</b>
              <i>
                {formatTime(d?.ts)}
                {" · "}
                {d?.order_id || "—"}
                {d?.sku_id ? ` · ${d.sku_id}` : ""}
              </i>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export default function Dashboard({ onBack, onStartDemo, onOpenOrder, onOpenAnalysis }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => { try { setError(""); setData(await api.dashboard()); } catch (e) { setError(e.message || "Dashboard unavailable"); } }, []);
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, [load]);
  const queue = useMemo(() => data?.recent_orders || [], [data]);

  if (error && !data) return <div className="dashboard"><button className="back" onClick={onBack}>← Picker</button><div className="dash-error">{error}</div><button className="btn go compact" onClick={load}>Try again</button></div>;
  if (!data) return <div className="dashboard"><button className="back" onClick={onBack}>← Picker</button><div className="dash-loading">Loading store operations…</div></div>;

  const m = data.metrics;
  const totalPipeline = STATUS.reduce((sum, s) => sum + (data.status_counts[s] || 0), 0) || 1;
  const hotspots = data.aisle_load || [];
  const productRate = m.product_verify_rate != null ? `${m.product_verify_rate}%` : "—";
  const bottleneck = hotspots[0];

  return <div className="dashboard">
    <header className="ops-header">
      <div>
        <div className="brand-line"><span className="live-dot" /> LIVE STORE · SHIFT A</div>
        <h1>DarkStore Control Tower</h1>
        <p>See what is moving, what is blocked, and where the next operational decision is needed.</p>
      </div>
      <div className="header-actions"><div className="refresh-chip">● Live · 10s</div>{onOpenAnalysis && <button className="back" onClick={onOpenAnalysis}>Analysis →</button>}<button className="back" onClick={onBack}>← Picker</button></div>
    </header>
    {error && <div className="dash-warning">Live refresh failed. Showing the last good snapshot.</div>}

    <section className="ops-metrics">
      <Metric label="Active picks" value={m.active_picks} note={`${m.units_pending} units remaining`} tone="blue" icon="↗" />
      <Metric label="Ready for rider" value={m.ready_for_pickup} note={`${m.dispatched_today} dispatched today`} tone="green" icon="→" />
      <Metric label="Pick cycle" value={m.avg_pick_seconds != null ? `${m.avg_pick_seconds}s` : "—"} note="start → bag check" tone="dark" icon="◷" />
      <Metric label="OOS recovery" value={m.oos_resolution_rate != null ? `${m.oos_resolution_rate}%` : "—"} note={`${m.ai_resolved_substitutions} voice-assisted`} tone="amber" icon="↔" />
      <Metric label="Item visual checks" value={productRate} note={`${m.product_verify_matches} matches · ${m.product_verify_attempts} attempts`} tone="blue" icon="⌾" />
      <Metric label="Bag accuracy" value={m.bag_verification_rate != null ? `${m.bag_verification_rate}%` : "—"} note={`${m.bag_failures} failed checks`} tone="green" icon="✓" />
      <Metric label="Concurrent picks" value={m.concurrent_pick_events} note={`${m.duplicate_picks_prevented} duplicate mutations prevented`} tone="amber" icon="👤" />
      <Metric label="Manual resolution" value={m.manual_resolution_required} note="no approved substitute available" tone="dark" icon="🛠" />
    </section>

    <div className="ops-alert-strip">
      <div><span className="alert-dot" /> <b>{m.oos_events}</b> stockout events need a decision</div>
      <div><span className="alert-dot blue" /> <b>{m.guardrail_rejections}</b> AI proposals blocked by policy</div>
      <div><span className="alert-dot amber" /> <b>{m.concurrent_pick_events}</b> concurrent picks · <b>{m.duplicate_picks_prevented}</b> duplicates prevented</div>
      <div><span className="alert-dot amber" /> <b>{m.bag_failures}</b> bag checks need attention</div>
      {bottleneck && <div><span className="alert-dot" /> <b>Aisle {bottleneck.aisle}</b> has the highest pending load</div>}
    </div>

    <div className="tower-grid">
      <section className="ops-card pipeline-card">
        <div className="section-head"><div><span className="section-kicker">FULFILLMENT CONTROL</span><h2>Order flow right now</h2></div><span className="section-note">{m.orders_today} orders today</span></div>
        <div className="pipeline">
          {STATUS.map((status) => { const count = data.status_counts[status] || 0; const width = Math.max(count ? 6 : 0, Math.round(count / totalPipeline * 100)); return <div className="pipeline-row" key={status}><div className="pipeline-label"><span>{STATUS_LABELS[status]}</span><b>{count}</b></div><div className="pipeline-track"><span data-status={status} style={{ width: `${width}%` }} /></div></div>; })}
        </div>
        <div className="pipeline-foot"><span>Active pickers <b>{m.active_pickers}</b></span><span>Orders in system <b>{m.orders_total}</b></span><span>Bag attempts <b>{m.bag_attempts}</b></span></div>
      </section>

      <section className="ops-card decision-card">
        <div className="section-head"><div><span className="section-kicker">AI + POLICY</span><h2>Decision quality</h2></div><span className="section-note">guardrails on</span></div>
        <div className="decision-score"><div><strong>{m.oos_resolution_rate != null ? `${m.oos_resolution_rate}%` : "—"}</strong><span>stockout recovery</span></div><div><strong>{productRate}</strong><span>item visual match</span></div></div>
        <div className="decision-row"><span>AI-assisted substitutions</span><b>{m.ai_resolved_substitutions}</b></div>
        <div className="decision-row"><span>Policy-blocked AI proposals</span><b>{m.guardrail_rejections}</b></div>
        <div className="decision-row"><span>Product visual attempts</span><b>{m.product_verify_attempts}</b></div>
        <div className="decision-note">AI proposes and interprets. Policy validates. Inventory and order state stay server-side.</div>
      </section>
    </div>

    <div className="tower-grid lower">
      <section className="ops-card">
        <div className="section-head"><div><span className="section-kicker">LIVE EXCEPTIONS</span><h2>Blocked right now</h2></div><span className="section-note">stockout and concurrent pick counted apart</span></div>
        <LiveExceptions rows={data.live_exceptions} onOpen={onOpenOrder} />
      </section>

      <section className="ops-card">
        <div className="section-head"><div><span className="section-kicker">RECENT AI / POLICY DECISIONS</span><h2>What the guardrails did</h2></div></div>
        <DecisionFeed rows={data.recent_decisions} />
      </section>
    </div>

    <div className="tower-grid lower">
      <section className="ops-card queue-card">
        <div className="section-head"><div><span className="section-kicker">LIVE PICKER QUEUE</span><h2>Orders that need attention</h2></div><span className="section-note">updated {formatTime(data.generated_at)}</span></div>
        {queue.length ? <div className="queue-table">
          <div className="queue-head"><span>Order / destination</span><span>Picker</span><span>Progress</span><span>Status</span></div>
          {queue.map((o) => { const total = o.items?.length || 0; const done = (o.items || []).filter((i) => ["PICKED", "SUBSTITUTED", "REMOVED"].includes(i.state)).length; return <div className="queue-row clickable" key={o.order_id} onClick={() => onOpenOrder?.(o.order_id)}>
            <div><b>{o.order_id}</b><small>{o.customer_ref || "Store order"} · {money(o.invoice_total)}</small></div><span className="picker-tag">{o.picker_id || "Unassigned"}</span>
            <div className="progress-cell"><b>{done}/{total}</b><span><i style={{ width: `${total ? done / total * 100 : 0}%` }} /></span></div><StatusPill status={o.status} />
          </div>; })}
        </div> : <div className="empty"><b>No live orders yet.</b><span>Launch the demo to populate the control tower.</span>{onStartDemo && <button className="btn go compact" onClick={onStartDemo}>Start demo order</button>}</div>}
      </section>

      <section className="ops-card route-card">
        <div className="section-head"><div><span className="section-kicker">PICKING HEATMAP</span><h2>Where workload is building</h2></div></div>
        {hotspots.length ? hotspots.map((a) => <div className="aisle-row" key={a.aisle}><div><b>Aisle {a.aisle}</b><small>{a.units} units · {a.orders} orders</small></div><div className="aisle-bar"><span style={{ width: `${a.percent}%` }} /></div></div>) : <div className="empty">Aisle load appears as orders enter the queue.</div>}
        <div className="route-note"><b>Route engine:</b> aisle-aware deterministic ordering. Use this panel to spot workload concentration, not to claim a global shortest path.</div>
      </section>
    </div>

    <footer className="tower-footer"><span><b>Operational principle:</b> AI interprets · policy decides · store backend owns truth.</span><span>DarkStore Copilot · AWS prototype</span></footer>
  </div>;
}
