import { useCallback, useEffect, useState } from "react";
import { api, ApiError, PICKER_ID, newActionId } from "./api.js";
import { speak } from "./hooks/useVoice.js";
import { useVoiceMachine } from "./hooks/useVoiceMachine.js";
import { getQueuedActions, queueAction, removeAction } from "./offlineQueue.js";
import ProgressRail from "./components/ProgressRail.jsx";
import RackTicket from "./components/RackTicket.jsx";
import ActionBar from "./components/ActionBar.jsx";
import VoicePanel from "./components/VoicePanel.jsx";
import SubstituteSheet from "./components/SubstituteSheet.jsx";
import ConcurrentPick from "./components/ConcurrentPick.jsx";
import ProductVerify from "./components/ProductVerify.jsx";
import BagCheck from "./components/BagCheck.jsx";
import Handover from "./components/Handover.jsx";
import ActivityFeed from "./components/ActivityFeed.jsx";
import OrderList from "./components/OrderList.jsx";
import DemoControlCenter from "./components/DemoControlCenter.jsx";
import DemoPanel from "./components/DemoPanel.jsx";
import Dashboard from "./components/Dashboard.jsx";
import Analysis from "./components/Analysis.jsx";

const MAIN_DEMO_ORDER = "ORD-DEMO-101";

/**
 * Actions that are safe to queue while offline: a pick or an exception is the picker
 * reporting something they can see with their own eyes, and the backend replays it under the
 * same client_action_id when the connection returns.
 *
 * Substitutions, AI visual decisions and anything else that depends on current stock, price
 * or another picker's actions are never queued. They need the store's current truth, and a
 * stale decision applied ten minutes later is worse than no decision.
 */
const OFFLINE_SAFE = new Set(["pick", "exception"]);

export default function App() {
  const [view, setView] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem("darkstore-active-order") || "null");
    } catch (_) {
      return null;
    }
  });
  const [screen, setScreen] = useState("home"); // home | orders | picker | ops | analysis
  const [lang, setLang] = useState("te");
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState(null);
  const [subs, setSubs] = useState(null);
  const [contested, setContested] = useState(null);
  const [lastDecision, setLastDecision] = useState(null);
  const [verdict, setVerdict] = useState(null);
  const [handover, setHandover] = useState(null);
  const [demoTaps, setDemoTaps] = useState(0);
  const [demoOpen, setDemoOpen] = useState(false);
  const [showActivity, setShowActivity] = useState(false);
  const [activityKey, setActivityKey] = useState(0);
  const [productVerification, setProductVerification] = useState(null);
  const [productResult, setProductResult] = useState(null);
  const [online, setOnline] = useState(navigator.onLine);
  const [syncing, setSyncing] = useState(false);
  const [pendingActions, setPendingActions] = useState(getQueuedActions());

  const machine = useVoiceMachine();

  const orderId = view?.order?.order_id;
  const item = view?.current_item;
  const index = view?.current_index;

  const flash = useCallback((message, kind = "info") => {
    setToast({ message, kind });
    setTimeout(() => setToast(null), 3600);
  }, []);

  /** Every backend call funnels through here: one place for busy state and error copy. */
  const run = useCallback(
    async (fn, { onResult, onError } = {}) => {
      setBusy(true);
      try {
        const res = await fn();
        if (res.order) setView(res);
        setActivityKey((k) => k + 1);
        onResult?.(res);
        return res;
      } catch (err) {
        if (err instanceof ApiError && err.code === "CONFLICT" && orderId) {
          flash("This order moved on. Reloading the current state.", "error");
          try {
            setView(await api.getOrder(orderId));
          } catch (_) {
            /* keep the last known view rather than blanking the screen */
          }
        } else {
          flash(err.message, "error");
        }
        onError?.(err);
        return null;
      } finally {
        setBusy(false);
      }
    },
    [flash, orderId]
  );

  useEffect(() => {
    if (view) localStorage.setItem("darkstore-active-order", JSON.stringify(view));
    else localStorage.removeItem("darkstore-active-order");
  }, [view]);

  // A seeded order that is already verified carries its handover token on the record, so the
  // QR screen works even when this browser never saw the bag check run.
  useEffect(() => {
    const token = view?.order?.handover_token;
    if (token && handover?.token !== token) {
      setHandover({ token, qr_payload: `darkstore://handover/${view.order.order_id}/${token}` });
    }
  }, [view?.order?.handover_token, view?.order?.order_id, handover?.token]);

  useEffect(() => {
    const update = () => setOnline(navigator.onLine);
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    const queueUpdate = () => setPendingActions(getQueuedActions());
    window.addEventListener("offline-queue-change", queueUpdate);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
      window.removeEventListener("offline-queue-change", queueUpdate);
    };
  }, []);

  const syncOfflineActions = useCallback(async () => {
    if (!navigator.onLine) return;
    const queued = getQueuedActions();
    if (!queued.length) return;
    setSyncing(true);
    let failures = 0;
    for (const action of queued) {
      try {
        if (action.type === "pick")
          await api.pick(action.order_id, action.index, action.sku_id, action.client_action_id);
        if (action.type === "exception")
          await api.exception(
            action.order_id,
            action.index,
            action.kind,
            action.note,
            action.client_action_id
          );
        removeAction(action.client_action_id);
      } catch (_) {
        failures += 1; // leave it queued; the store may be temporarily unreachable
      }
    }
    setPendingActions(getQueuedActions());
    setSyncing(false);
    if (orderId) {
      try {
        setView(await api.getOrder(orderId));
      } catch (_) {}
    }
    if (failures)
      flash(`${failures} queued action(s) could not sync yet. They are still saved here.`, "error");
    else flash("Sync complete. Queued actions are recorded in the store.");
  }, [flash, orderId]);

  useEffect(() => {
    window.addEventListener("online", syncOfflineActions);
    syncOfflineActions();
    return () => window.removeEventListener("online", syncOfflineActions);
  }, [syncOfflineActions]);

  const queueOffline = (kind, payload) => {
    if (!OFFLINE_SAFE.has(kind)) {
      flash("That decision needs the store's current stock and price, so it needs a connection.", "error");
      return;
    }
    queueAction({ type: kind, client_action_id: newActionId(kind), ...payload });
    setPendingActions(getQueuedActions());
    flash("Saved on this device. It will sync automatically when the connection returns.");
  };

  const resetPanels = () => {
    setSubs(null);
    setContested(null);
    setVerdict(null);
    setLastDecision(null);
    machine.reset();
  };

  // --- order loading ----------------------------------------------------
  const openOrder = (id) =>
    run(() => api.getOrder(id), {
      onResult: () => {
        setScreen("picker");
        resetPanels();
      },
    });

  const startDemo = async () => {
    const res = await run(() => api.getOrder(MAIN_DEMO_ORDER), {
      onError: () =>
        flash("Seeded orders are missing. Open the demo control center and reset the demo.", "error"),
    });
    if (res) {
      setScreen("picker");
      resetPanels();
    }
  };

  // --- picker actions ---------------------------------------------------
  const confirmPick = () => {
    if (productResult && productResult.verdict !== "MATCH") {
      flash("The visual check did not match. Retake the photo or report this line.", "error");
      return;
    }
    if (!online) {
      queueOffline("pick", { order_id: orderId, index, sku_id: item.sku_id });
      return;
    }
    return run(() => api.pick(orderId, index, item.sku_id, newActionId("pick")));
  };

  const flagMissing = () => {
    if (!online) {
      queueOffline("exception", {
        order_id: orderId,
        index,
        kind: "OOS",
        note: "Picker reported the shelf empty",
      });
      return;
    }
    return run(
      () => api.exception(orderId, index, "OOS", "Picker reported the shelf empty", newActionId("oos")),
      {
        onResult: (res) =>
          setSubs({
            original: item,
            eligible: res.eligible_substitutes || [],
            rejected: res.rejected_substitutes || [],
          }),
      }
    );
  };

  const chooseSubstitute = (sku) => {
    if (!online) {
      flash("A substitution needs live stock and price from the store.", "error");
      return;
    }
    return run(() => api.substitute(orderId, index, sku, newActionId("sub")), {
      onResult: (res) => {
        setSubs(null);
        setContested(null);
        setLastDecision({ chain: res.policy_chain, interpretation: null });
        flash("Substitute applied and the bill updated.");
      },
    });
  };

  const skipLine = () =>
    run(
      () =>
        api.exception(
          orderId,
          index,
          "EXCEPTION",
          "No eligible substitute; flagged for manual resolution",
          newActionId("skip")
        ),
      {
        onResult: () => {
          setSubs(null);
          setContested(null);
          flash("Line flagged for manual resolution.");
        },
      }
    );

  const playResponse = async (text) => {
    if (!text) return false;
    // Polly is the cloud path for Hindi and English; Telugu falls back to the browser because
    // Polly has no Telugu voice. If speech fails, never block picking.
    if (lang !== "te") {
      try {
        const audio = await api.speech(text, lang);
        if (audio?.supported && audio.audio_base64) {
          const bytes = Uint8Array.from(atob(audio.audio_base64), (c) => c.charCodeAt(0));
          const url = URL.createObjectURL(new Blob([bytes], { type: audio.audio_format || "audio/mpeg" }));
          const player = new Audio(url);
          player.onended = () => URL.revokeObjectURL(url);
          await player.play();
          return true;
        }
      } catch (_) {}
    }
    return speak(text, lang);
  };

  const handleUtterance = (utterance, simulated) => {
    if (!utterance) return;
    if (!online) {
      machine.fail("Voice decisions need a live store connection. Use the buttons above.");
      return;
    }
    machine.heard(utterance);
    return run(() => api.intent(orderId, index, utterance, lang, simulated, newActionId("voice")), {
      onResult: async (res) => {
        setLastDecision({ chain: res.policy_chain, interpretation: res.interpretation });

        if (res.executed === "DUPLICATE_IGNORED") {
          machine.finish(false);
          flash("That command was already recorded. Nothing changed.");
          return;
        }

        const spoken =
          lang === "hi"
            ? res.intent?.spoken_response_hindi
            : lang === "en"
            ? res.intent?.spoken_response_english || res.intent?.spoken_response_hindi
            : res.intent?.spoken_response_telugu;

        machine.finish(!!spoken);
        playResponse(spoken);

        if (res.executed === "ITEM_ALREADY_TAKEN") {
          setContested({
            info: res.concurrent_pick,
            chain: res.policy_chain,
            interpretation: res.interpretation,
            eligible: res.eligible_substitutes || [],
          });
          return;
        }
        if (res.executed === "CONCURRENT_PICK_NO_CHANGE") {
          flash("That line was already resolved. Duplicate pick prevented.");
          return;
        }
        if (res.executed === "AWAITING_PICKER_CHOICE" || res.executed === "FLAG_EXCEPTION") {
          setSubs({
            original: item,
            eligible: res.eligible_substitutes || [],
            rejected: res.rejected_substitutes || [],
          });
          if (res.intent?.fallback) flash("Voice was unclear. Choose on screen.", "error");
        }
      },
      onError: (err) => machine.fail(err.message),
    });
  };

  /** Button equivalent of the concurrent-pick utterance, for noisy aisles. */
  const reportTaken = () => handleUtterance("someone already took this");

  const verifyProduct = (b64) => {
    if (!online) {
      flash("The visual check needs a connection to the store service.", "error");
      return;
    }
    setProductResult(null);
    run(() => api.verifyItem(orderId, index, item.sku_id, b64, "jpeg"), {
      onResult: (res) => {
        setProductResult(res.product_verification);
        const v = res.product_verification?.verdict;
        if (v === "MATCH") flash("Product matched. You can confirm the pick.");
        if (v === "MISMATCH") flash("Product mismatch. Do not confirm this item.", "error");
        if (v === "UNCLEAR") flash("Image was unclear. Take another photo.", "error");
      },
    });
  };

  const verifyBag = (b64) => {
    if (!online) {
      flash("Bag verification needs a live connection to the visual service.", "error");
      return;
    }
    return run(() => api.verifyBag(orderId, b64, "jpeg"), {
      onResult: (res) => {
        setVerdict(res.verification);
        if (res.handover) setHandover(res.handover);
        if (res.verification?.verdict === "VERIFIED") flash("Bag verified. Handover unlocked.");
        else flash("Bag check failed. Add the missing item and scan again.", "error");
      },
    });
  };

  const dispatch = () => {
    if (!handover?.token) {
      flash("No handover token on this order. Run the bag check first.", "error");
      return;
    }
    return run(() => api.dispatch(orderId, handover.token), {
      onResult: () => flash("Dispatched to the rider."),
    });
  };

  useEffect(() => {
    setProductVerification(null);
    setProductResult(null);
  }, [orderId, index, item?.sku_id]);

  // Re-fetch when the tab comes back: a picker's phone sleeps mid-aisle constantly.
  useEffect(() => {
    if (!orderId || screen !== "picker") return;
    const onVisible = () =>
      document.visibilityState === "visible" && api.getOrder(orderId).then(setView).catch(() => {});
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [orderId, screen]);

  const demoCentre = (
    <DemoControlCenter
      open={demoOpen}
      onToggle={setDemoOpen}
      onOpenOrder={(res) => {
        setView(res);
        setScreen("picker");
        resetPanels();
      }}
      onOpenView={(v) => setScreen(v)}
      flash={flash}
    />
  );

  const toastEl = toast && (
    <div className="toast" data-kind={toast.kind}>
      {toast.message}
    </div>
  );

  // --- render -----------------------------------------------------------
  if (screen === "ops") {
    return (
      <>
        <Dashboard
          onBack={() => setScreen(view ? "picker" : "home")}
          onStartDemo={() => {
            setScreen("home");
            startDemo();
          }}
          onOpenOrder={(id) => openOrder(id)}
          onOpenAnalysis={() => setScreen("analysis")}
        />
        {demoCentre}
        {toastEl}
      </>
    );
  }

  if (screen === "analysis") {
    return (
      <>
        <Analysis onBack={() => setScreen(view ? "picker" : "home")} />
        {demoCentre}
        {toastEl}
      </>
    );
  }

  if (screen === "orders") {
    return (
      <div className="app wide">
        <OrderList onOpen={openOrder} onBack={() => setScreen("home")} busy={busy} />
        {demoCentre}
        {toastEl}
      </div>
    );
  }

  if (screen === "home" || !view) {
    return (
      <div className="app">
        <div className="center">
          <div className="home-brand">
            <span className="live-dot" /> DarkStore Copilot
          </div>
          <h1>
            Pick faster.
            <br />
            Decide safely.
            <br />
            Verify before handover.
          </h1>
          <p>
            Voice-first picking for dark stores. The AI interprets Telugu, Hindi and English and
            proposes an action; store policy decides whether it is allowed; the backend is the only
            thing that changes stock, price or order state.
          </p>
          <div className="home-pillars">
            <span>🎙 Speak</span>
            <span>🛡 Policy</span>
            <span>📷 Verify</span>
          </div>
          <button className="btn go" disabled={busy} onClick={startDemo}>
            {busy ? "Opening order…" : "Open the demo order"}
          </button>
          <button className="btn plain" onClick={() => setScreen("orders")}>
            Shift board — all orders
          </button>
          <button className="btn plain" onClick={() => setScreen("ops")}>
            Operations control tower
          </button>
          <button className="btn plain" onClick={() => setScreen("analysis")}>
            Analysis workspace
          </button>
          <div className="home-foot">
            <span>Picker: {PICKER_ID}</span>
            <span>{online ? "● Online" : "○ Offline · queued actions enabled"}</span>
          </div>
        </div>
        {demoCentre}
        {toastEl}
      </div>
    );
  }

  const order = view.order;
  const status = order.status;
  const total = order.items.length;
  const done = order.items.filter((i) => ["PICKED", "SUBSTITUTED", "REMOVED"].includes(i.state)).length;

  const shell = (children) => (
    <div className="app">
      <div className="topbar">
        <button className="topbar-back" onClick={() => setScreen("orders")} aria-label="Back to the shift board">
          ←
        </button>
        <span>
          <b>{order.order_id}</b> · ₹{order.invoice_total}
        </span>
        <span className="topbar-links">
          <button onClick={() => setScreen("ops")}>Ops</button>
          <button onClick={() => setScreen("analysis")}>Analysis</button>
        </span>
      </div>
      <div className={`network-banner ${online ? "online" : "offline"}`}>
        <span>
          {syncing
            ? "↻ Online — syncing queued actions"
            : online
            ? "● Store connection online"
            : "○ Offline — pick confirmations are saved on this device"}
        </span>
        {pendingActions.length > 0 && <b>{pendingActions.length} queued</b>}
      </div>
      {children}
      {demoCentre}
      {toastEl}
    </div>
  );

  if (status === "DISPATCHED") {
    return shell(
      <div className="center">
        <h1>Handed to the rider</h1>
        <p>
          {order.order_id} is on its way · {total} lines · ₹{order.invoice_total}
        </p>
        <button className="btn go" onClick={() => setScreen("orders")}>
          Next order
        </button>
        <button className="btn plain" onClick={() => setScreen("ops")}>
          See the impact in the control tower
        </button>
      </div>
    );
  }

  if (status === "READY_FOR_PICKUP") {
    return shell(
      <>
        <Handover order={order} handover={handover} busy={busy} onDispatch={dispatch} />
        {showActivity && (
          <ActivityFeed orderId={orderId} refreshKey={activityKey} onClose={() => setShowActivity(false)} />
        )}
      </>
    );
  }

  if (status === "BAG_VERIFICATION" || (index === null && status === "PICKING")) {
    return shell(
      <>
        <BagCheck
          items={order.items}
          verdict={verdict}
          busy={busy}
          onVerify={verifyBag}
          onBack={() => api.getOrder(orderId).then(setView).catch(() => {})}
          onReview={() => setShowActivity(true)}
        />
        {showActivity && (
          <ActivityFeed orderId={orderId} refreshKey={activityKey} onClose={() => setShowActivity(false)} />
        )}
      </>
    );
  }

  return shell(
    <>
      <ProgressRail items={order.items} currentIndex={index} onSecretTap={() => setDemoTaps((t) => t + 1)} />

      <div className="picker-body">
        <RackTicket
          item={item}
          position={index + 1}
          total={total}
          productResult={productResult}
          onVerifyProduct={() => {
            setProductResult(null);
            setProductVerification(item);
          }}
        />

        <ActionBar
          busy={busy}
          blocked={!!productResult && productResult.verdict !== "MATCH"}
          onPicked={confirmPick}
          onMissing={flagMissing}
          onTaken={reportTaken}
          onActivity={() => setShowActivity(true)}
        />

        <VoicePanel
          lang={lang}
          setLang={setLang}
          busy={busy}
          machine={machine}
          lastDecision={lastDecision}
          onUtterance={(text) => handleUtterance(text, null)}
          onRetry={() => handleUtterance(machine.transcript, null)}
        />

        <div className="picker-foot">
          {done} of {total} lines closed · picker {order.picker_id || PICKER_ID}
        </div>
      </div>

      {contested && (
        <ConcurrentPick
          info={contested.info}
          chain={contested.chain}
          interpretation={contested.interpretation}
          eligible={contested.eligible}
          busy={busy}
          onSubstitute={chooseSubstitute}
          onFlag={skipLine}
          onClose={() => {
            setSubs({ original: item, eligible: contested.eligible, rejected: [] });
            setContested(null);
          }}
        />
      )}

      {subs && !contested && (
        <SubstituteSheet
          original={subs.original}
          eligible={subs.eligible}
          rejected={subs.rejected}
          busy={busy}
          onChoose={chooseSubstitute}
          onSkip={skipLine}
          onClose={() => setSubs(null)}
        />
      )}

      {productVerification && (
        <ProductVerify
          item={productVerification}
          result={productResult}
          busy={busy}
          onVerify={verifyProduct}
          onClose={() => setProductVerification(null)}
        />
      )}

      {showActivity && (
        <ActivityFeed orderId={orderId} refreshKey={activityKey} onClose={() => setShowActivity(false)} />
      )}

      <DemoPanel
        item={item}
        substitute={subs?.eligible?.[0] || contested?.eligible?.[0]}
        forceOpen={demoTaps >= 4}
        onSimulate={(utterance, intent) => handleUtterance(utterance, intent)}
      />
    </>
  );
}
