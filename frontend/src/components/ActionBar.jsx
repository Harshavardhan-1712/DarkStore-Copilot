/** Voice is the fast path. Every action it can take is also a button, always. */
export default function ActionBar({ busy, blocked, onPicked, onMissing, onTaken, onActivity }) {
  return (
    <div className="actions">
      {blocked && (
        <div className="action-block">
          The visual check did not match this SKU. Retake the photo or report the item instead of
          confirming it.
        </div>
      )}
      <div className="row">
        <button className="btn go" disabled={busy || blocked} onClick={onPicked}>
          ✓ Got it
        </button>
        <button className="btn stop" disabled={busy} onClick={onMissing}>
          ! Not there
        </button>
      </div>
      <div className="row secondary">
        <button className="btn plain" disabled={busy} onClick={onTaken}>
          👤 Someone took it
        </button>
        <button className="btn plain" onClick={onActivity}>
          AI activity
        </button>
      </div>
    </div>
  );
}
