import { useRef, useState } from "react";
import { fileToBase64 } from "../lib/image";

export default function ProductVerify({ item, result, busy, onVerify, onClose }) {
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
      setLocalError("This image could not be read. Try a JPG/PNG photo.");
    } finally {
      e.target.value = "";
    }
  }

  const matched = result?.verdict === "MATCH";
  const failed = result?.verdict === "MISMATCH" || result?.verdict === "FAILED";
  const unclear = result?.verdict === "UNCLEAR";

  return (
    <div className="scrim" onClick={onClose}>
      <div className="sheet product-sheet" onClick={(e) => e.stopPropagation()}>
        <div className="verify-head">
          <div>
            <h2>📷 Verify product</h2>
            <p className="lede">Point the camera at the product label or upload a photo. The visual model checks it against the expected SKU.</p>
          </div>
          <button className="icon-close" onClick={onClose} aria-label="Close">×</button>
        </div>

        <div className="expected-product">
          <div className="product-icon">📦</div>
          <div>
            <small>Expected product</small>
            <strong>{item.name}</strong>
            <span>{item.sku_id} · {item.category || "store item"} · expected quantity ×{item.qty}</span>
          </div>
        </div>

        {preview ? <img className="product-preview" src={preview} alt="Uploaded product" /> : (
          <div className="upload-zone" onClick={() => inputRef.current?.click()}>
            <div className="camera-icon">⌾</div>
            <strong>Upload product photo</strong>
            <span>Camera on phone · file upload on desktop</span>
          </div>
        )}

        {result && (
          <div className="verify-result" data-result={matched ? "match" : failed ? "failed" : "info"}>
            <div className="result-icon">{matched ? "✓" : failed ? "✕" : "?"}</div>
            <div>
              <strong>
                {matched ? "Match" : failed ? "Mismatch — do not confirm this item" : "Unclear — take another photo"}
              </strong>
              <p>
                {failed
                  ? "This is not the expected product. Put it back and report the line instead of confirming it."
                  : unclear
                  ? "There was not enough visible evidence to judge this safely. Move closer to the label, steady the camera and shoot again."
                  : result.notes}
              </p>
              {result.detected_product && (
                <small>
                  Detected: <b>{result.detected_product}</b>
                </small>
              )}
              <small>
                Confidence: <b>{Math.round((result.confidence || 0) * 100)}%</b> · {result.provider || "visual model"}
              </small>
            </div>
          </div>
        )}

        {localError && <div className="inline-error">{localError}</div>}

        <input ref={inputRef} type="file" accept="image/*" capture="environment" hidden onChange={handleFile} />
        <div className="verify-actions">
          <button className="btn go" disabled={busy} onClick={() => inputRef.current?.click()}>
            {busy ? "Checking image…" : unclear ? "↻ Take another photo" : preview ? "Choose another photo" : "📷 Take / upload photo"}
          </button>
          {matched && <button className="btn plain" disabled={busy} onClick={onClose}>✓ Continue to pick</button>}
          {failed && <button className="btn stop" disabled={busy} onClick={onClose}>Back — report this line</button>}
        </div>
        <p className="verify-foot">
          The visual model reads the image and fails closed on low confidence. It cannot change
          the order: the store backend stays the source of truth for SKU, stock and order state.
          {result?.provider === "local-demo" && " Running the local demo adapter — Bedrock performs the real comparison once deployed."}
        </p>
      </div>
    </div>
  );
}
