/**
 * The AI -> POLICY -> ACTION chain for one decision.
 *
 * Every row comes from the backend's policy_chain: these are the checks that actually ran,
 * in the order they ran. ok=true passed, ok=false blocked, ok=null is context rather than a
 * check. Nothing here is generated in the browser.
 */
export default function PolicyChain({ chain, interpretation, compact }) {
  if (!chain?.length) return null;
  const blocked = chain.some((step) => step.ok === false);

  return (
    <div className={`chain ${compact ? "compact" : ""}`} data-blocked={blocked}>
      <div className="chain-head">
        <span>{blocked ? "🛡 Policy blocked this" : "Decision trail"}</span>
        {interpretation?.interpreter && (
          <em title="Which layer read the utterance">
            {interpretation.interpreter === "deterministic-fast-path"
              ? "on-device fast path"
              : interpretation.interpreter === "simulated-intent"
              ? "simulated intent"
              : "Amazon Bedrock"}
            {interpretation.detected_lang ? ` · ${interpretation.detected_lang}` : ""}
          </em>
        )}
      </div>
      <ol>
        {chain.map((step, i) => (
          <li key={i} data-ok={step.ok === true ? "yes" : step.ok === false ? "no" : "info"}>
            <span className="chain-mark">{step.ok === true ? "✓" : step.ok === false ? "✕" : "·"}</span>
            <span className="chain-body">
              <b>{step.label}</b>
              {step.detail && <i>{step.detail}</i>}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
