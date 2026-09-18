import PolicyChain from "./PolicyChain.jsx";

/**
 * Item already taken by another picker. Distinct from a stockout: the store may well have
 * stock, but this order's unit is gone, so the fix is a substitute or a manual resolution
 * rather than a shelf replenishment.
 */
export default function ConcurrentPick({ info, chain, interpretation, eligible, busy, onSubstitute, onFlag, onClose }) {
  if (!info) return null;
  const hasSwap = eligible?.length > 0;

  return (
    <div className="scrim" onClick={onClose}>
      <div className="sheet contested-sheet" onClick={(e) => e.stopPropagation()}>
        <div className="contested-head">
          <span className="contested-badge">⚠ Item unavailable</span>
          <button className="icon-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <h2>
          {info.name} <i>× {info.qty}</i>
        </h2>
        <p className="lede">{info.reason}</p>
        {info.store_stock > 0 ? (
          <p className="contested-note">
            The store still shows {info.store_stock} on hand elsewhere, but this order's unit has
            been taken. Operations sees this as a picker coordination issue, not a stockout.
          </p>
        ) : (
          <p className="contested-note">No units remain on hand for this SKU.</p>
        )}

        <PolicyChain chain={chain} interpretation={interpretation} />

        <div className="contested-actions">
          {hasSwap ? (
            <>
              <div className="swap-offer">
                <small>Policy-approved substitute</small>
                <strong>{eligible[0].name}</strong>
                <span>
                  ₹{eligible[0].price} · aisle {eligible[0].aisle} shelf {eligible[0].shelf} ·{" "}
                  {eligible[0].stock_qty} on hand
                </span>
              </div>
              <button className="btn go" disabled={busy} onClick={() => onSubstitute(eligible[0].sku_id)}>
                {busy ? "Applying" : "Apply substitute"}
              </button>
              <button className="btn plain" disabled={busy} onClick={onClose}>
                See all substitutes
              </button>
            </>
          ) : (
            <>
              <div className="swap-offer" data-empty="true">
                <small>No approved substitute</small>
                <strong>Manual resolution needed</strong>
                <span>Nothing registered for this SKU passes category, stock and price rules.</span>
              </div>
              <button className="btn stop" disabled={busy} onClick={onFlag}>
                Flag for manual resolution
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
