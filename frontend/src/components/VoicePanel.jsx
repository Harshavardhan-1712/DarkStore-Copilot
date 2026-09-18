import { useVoice } from "../hooks/useVoice.js";
import { STATE_COPY, VOICE_STATES } from "../hooks/useVoiceMachine.js";
import PolicyChain from "./PolicyChain.jsx";

const LABELS = { te: "తెలుగు", hi: "हिंदी", en: "English" };

const PHRASEBOOK = {
  te: ["teesukunnanu", "milk ledu", "evaro teesukunnaru"],
  hi: ["mil gaya", "nahi hai", "kisi ne le liya"],
  en: ["picked", "out of stock", "someone already took this"],
};

/**
 * Voice Copilot. The state machine is the interface: the picker can always see which stage
 * the utterance is at, and after every action the decision trail below shows what the AI
 * proposed and what store policy did with it.
 */
export default function VoicePanel({
  lang,
  setLang,
  busy,
  machine,
  lastDecision,
  onUtterance,
  onRetry,
}) {
  const { listening, supported, start, stop } = useVoice(
    lang,
    (text) => onUtterance(text),
    (message) => machine.fail(message)
  );
  const copy = STATE_COPY[machine.state] || STATE_COPY.READY;
  const active = machine.state !== "READY" && machine.state !== "ERROR";
  const stageIndex = VOICE_STATES.indexOf(machine.state);

  const toggle = () => {
    if (listening) {
      stop();
      machine.to("READY");
    } else {
      machine.reset();
      machine.to("LISTENING");
      start();
    }
  };

  return (
    <section className="voice">
      <div className="voice-head">
        <h3>Voice copilot</h3>
        <div className="langs">
          {Object.entries(LABELS).map(([code, label]) => (
            <button key={code} aria-pressed={lang === code} onClick={() => setLang(code)}>
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="voice-state" data-state={machine.state}>
        <span className="voice-dot">{copy.dot}</span>
        <span className="voice-label">{copy.label}</span>
        <span className="voice-hint">{machine.state === "ERROR" ? machine.error : copy.hint}</span>
      </div>

      <div className="voice-track" role="presentation">
        {VOICE_STATES.map((s, i) => (
          <span
            key={s}
            data-done={stageIndex >= i && machine.state !== "ERROR"}
            data-current={machine.state === s}
            title={STATE_COPY[s].label}
          />
        ))}
      </div>

      {machine.transcript && (
        <div className="voice-transcript">
          <small>You said</small>
          <p>“{machine.transcript}”</p>
        </div>
      )}

      {(machine.state === "UNDERSTANDING" || machine.state === "POLICY_CHECK") && (
        <div className="voice-flow">
          <b data-on={true}>AI</b>
          <i>→</i>
          <b data-on={machine.state === "POLICY_CHECK"}>POLICY</b>
          <i>→</i>
          <b data-on={false}>STORE</b>
        </div>
      )}

      <button
        type="button"
        className="mic"
        data-listening={listening}
        disabled={busy || !supported}
        onClick={toggle}
        aria-label={listening ? "Stop listening" : "Start voice input"}
      >
        <span>
          {listening ? "Listening — tap to stop" : supported ? "🎙 Tap to speak" : "Mic unavailable"}
          <small>
            {supported
              ? `Speak ${LABELS[lang]}. Every action also has a button.`
              : "Browser speech is unavailable here. Use the buttons below."}
          </small>
        </span>
      </button>

      {machine.state === "ERROR" && (
        <div className="voice-error">
          <b>Voice did not go through</b>
          <p>{machine.error}</p>
          <div className="row">
            <button className="btn plain compact" onClick={() => machine.reset()}>
              Dismiss
            </button>
            {onRetry && machine.transcript && (
              <button className="btn go compact" onClick={onRetry} disabled={busy}>
                Send “{machine.transcript.slice(0, 22)}” again
              </button>
            )}
          </div>
        </div>
      )}

      {!active && (
        <div className="phrasebook">
          <small>Try saying</small>
          {PHRASEBOOK[lang].map((p) => (
            <code key={p}>{p}</code>
          ))}
        </div>
      )}

      {lastDecision?.chain?.length > 0 && (
        <PolicyChain chain={lastDecision.chain} interpretation={lastDecision.interpretation} />
      )}
    </section>
  );
}
