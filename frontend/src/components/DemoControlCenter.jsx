import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";

const ICONS = {
  main: "🧺",
  voice_picking: "🎙",
  stockout: "📦",
  concurrent_pick: "👤",
  product_verify: "📷",
  guardrail: "🛡",
  bag_failed: "👜",
  ready_for_rider: "🚴",
  dispatched: "✓",
};

/**
 * Demo Control Center.
 *
 * Each button rebuilds that scenario on the backend by replaying real API calls, then opens
 * the resulting order. Nothing is staged in the browser: after a click the order, its line
 * states, its invoice and its audit trail are all genuinely in that state, which is why the
 * control tower numbers move too.
 */
export default function DemoControlCenter({ open, onToggle, onOpenOrder, onOpenView, flash }) {
  const [scenarios, setScenarios] = useState([]);
  const [error, setError] = useState("");
  const [busyKey, setBusyKey] = useState("");

  const load = useCallback(async () => {
    try {
      setError("");
      const res = await api.demoScenarios();
      setScenarios(res.scenarios || []);
    } catch (e) {
      setError(e.message || "Demo scenarios unavailable.");
    }
  }, []);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  const runScenario = async (key) => {
    setBusyKey(key);
    try {
      const res = await api.demoScenario(key);
      onOpenOrder(res);
      flash(`${res.title} is ready.`);
      onToggle(false);
    } catch (e) {
      setError(e.message || "Could not prepare that scenario.");
    } finally {
      setBusyKey("");
    }
  };

  const reset = async () => {
    setBusyKey("__reset");
    try {
      const res = await api.demoReset();
      flash(`Demo reset — ${res.seeded.length} orders rebuilt.`);
      await load();
    } catch (e) {
      setError(e.message || "Reset failed.");
    } finally {
      setBusyKey("");
    }
  };

  if (!open) {
    return (
      <button className="demo-fab" onClick={() => onToggle(true)} aria-label="Open demo control center">
        ⚙ Demo
      </button>
    );
  }

  return (
    <div className="scrim" onClick={() => onToggle(false)}>
      <div className="sheet demo-center" onClick={(e) => e.stopPropagation()}>
        <div className="verify-head">
          <div>
            <h2>Demo control center</h2>
            <p className="lede">
              Each scenario is rebuilt on the backend through the real API, then opened here.
            </p>
          </div>
          <button className="icon-close" onClick={() => onToggle(false)} aria-label="Close">
            ×
          </button>
        </div>

        {error && <div className="inline-error">{error}</div>}

        <div className="demo-grid">
          {scenarios.map((s) => (
            <button
              key={s.scenario}
              className="demo-card"
              disabled={!!busyKey}
              onClick={() => runScenario(s.scenario)}
            >
              <span className="demo-icon">{ICONS[s.scenario] || "▸"}</span>
              <b>{s.title}</b>
              <i>{s.summary}</i>
              <em>{busyKey === s.scenario ? "Preparing…" : s.order_id}</em>
            </button>
          ))}
        </div>

        <div className="demo-views">
          <button
            className="demo-card wide"
            onClick={() => {
              onOpenView("ops");
              onToggle(false);
            }}
          >
            <span className="demo-icon">📊</span>
            <b>Operations control tower</b>
            <i>Live state: active picks, exceptions, AI and policy decisions.</i>
          </button>
          <button
            className="demo-card wide"
            onClick={() => {
              onOpenView("analysis");
              onToggle(false);
            }}
          >
            <span className="demo-icon">📈</span>
            <b>Analysis workspace</b>
            <i>Historical view: cycle time, cause mix, verification, picker performance.</i>
          </button>
        </div>

        <button className="btn stop reset-demo" disabled={!!busyKey} onClick={reset}>
          {busyKey === "__reset" ? "Rebuilding every scenario…" : "↻ Reset demo"}
        </button>
        <p className="verify-foot">
          Reset restores inventory, orders, line states and audit records to the same known
          state. Safe to run mid-demo.
        </p>
      </div>
    </div>
  );
}
