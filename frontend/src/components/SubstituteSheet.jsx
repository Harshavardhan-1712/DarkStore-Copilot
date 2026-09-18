/** Shown when a line is out of stock. Only backend-approved swaps are tappable. */
export default function SubstituteSheet({ original, eligible, rejected, busy, onChoose, onSkip, onClose }) {
  return (
    <div className="scrim" onClick={onClose}>
      <div className="sheet" onClick={(e) => e.stopPropagation()}>
        <h2>{original?.name || "This item"} is not on the shelf</h2>
        <p className="lede">
          {eligible.length
            ? "These swaps passed the store's category, stock and price rules. Pick one and the bill updates with it."
            : "Nothing here passes the store's swap rules, so this line comes off the order."}
        </p>

        {eligible.map((c) => (
          <button key={c.sku_id} className="option" disabled={busy} onClick={() => onChoose(c.sku_id)}>
            <strong>{c.name}</strong>
            <span className="price">₹{c.price}</span>
            <span>
              Aisle {c.aisle} · shelf {c.shelf} · {c.stock_qty} on hand
            </span>
          </button>
        ))}

        {rejected?.length > 0 && (
          <>
            <p className="lede" style={{ marginTop: 14 }}>Blocked by store policy</p>
            {rejected.map((r) => (
              <div key={r.sku_id || r.code} className="option" data-blocked="true">
                <strong>{r.name || r.sku_id || "Unknown SKU"}</strong>
                <span />
                <span>{r.message}</span>
              </div>
            ))}
          </>
        )}

        <button className="btn plain" style={{ width: "100%", marginTop: 10 }} disabled={busy} onClick={onSkip}>
          Skip this line and tell the customer
        </button>
      </div>
    </div>
  );
}
