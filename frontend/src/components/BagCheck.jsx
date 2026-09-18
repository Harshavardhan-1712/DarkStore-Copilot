import { useRef, useState } from "react";
import { fileToBase64 } from "../lib/image";

export default function BagCheck({ items, verdict, busy, onVerify, onBack, onReview }) {
  const inputRef = useRef(null);
  const [preview, setPreview] = useState(null);
  const [localError, setLocalError] = useState("");

  async function handleFile(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    setLocalError("");
    try {
      const { base64, mime } = await fileToBase64(file);
      setPreview(`data:${mime};base64,${base64}`);
      onVerify(base64);
    } catch (_) {
      setLocalError("That image could not be read. Take a JPG or PNG photo and try again.");
    } finally {
      e.target.value = "";
    }
  }

  const missing = new Set(verdict?.missing_skus || []);
  const passed = verdict?.verdict === "VERIFIED";
  const failed = verdict && !passed;

  return (
    <div className="bag-view">
      <div className="bag-head">
        <h2>👜 Bag verification</h2>
        <p className="lede">
          Photograph the packed bag top-down with the whole bag in frame. The check runs against
          the order manifest below.
        </p>
      </div>

      {verdict && (
        <div className="bag-verdict" data-ok={passed}>
          <div className="bag-verdict-top">
            <strong>{passed ? "✓ Verified" : "✕ Failed"}</strong>
            <span>
              Expected: {verdict.expected_units ?? items.length} items · Detected:{" "}
              {verdict.detected_units ?? "—"}
            </span>
          </div>
          <p>{verdict.notes}</p>
          {verdict.missing_items?.length > 0 && (
            <div className="bag-missing">
              <small>Missing</small>
              {verdict.missing_items.map((m) => (
                <b key={m.sku_id}>
                  {m.name} × {m.qty}
                </b>
              ))}
            </div>
          )}
          {failed && (
            <p className="bag-warn">
              Handover stays locked until a scan passes. Add the missing item to the bag and scan
              again.
            </p>
          )}
        </div>
      )}

      {preview && <img className="bag-photo" src={preview} alt="Bag photo just taken" />}
      {localError && <div className="inline-error">{localError}</div>}

      <div className="list">
        {items.map((i) => (
          <div key={i.sku_id} data-missing={missing.has(i.sku_id)}>
            {missing.has(i.sku_id) ? "▢" : "▣"} {i.name} <i>× {i.qty}</i>
          </div>
        ))}
      </div>

      <input ref={inputRef} type="file" accept="image/*" capture="environment" hidden onChange={handleFile} />
      <button className="btn go" disabled={busy} onClick={() => inputRef.current?.click()}>
        {busy ? "Checking the bag…" : failed ? "↻ Retry scan" : verdict ? "Retake photo" : "📷 Take photo"}
      </button>
      {failed && (
        <button className="btn plain" disabled={busy} onClick={onReview}>
          Review order lines
        </button>
      )}
      <button className="btn plain" onClick={onBack}>
        Back to picking
      </button>
    </div>
  );
}
