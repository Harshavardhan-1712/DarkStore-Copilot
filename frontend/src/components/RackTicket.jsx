const STATE_COPY = {
  OOS: "Reported out of stock",
  CONTESTED: "Another picker took this",
  EXCEPTION: "Flagged for a decision",
};

/**
 * The picker's current task. One item, its location, the quantity, and the verification
 * affordance. Everything secondary lives elsewhere on the screen so this stays scannable at
 * arm's length in a cold aisle.
 */
export default function RackTicket({ item, position, total, onVerifyProduct, productResult }) {
  const verified = productResult?.verdict === "MATCH";
  const mismatch = productResult?.verdict === "MISMATCH";
  const unclear = productResult?.verdict === "UNCLEAR";
  const flag = STATE_COPY[item.state];

  return (
    <div className="ticket">
      <div className="pick-counter">
        Pick item <b>{position}</b> of {total}
      </div>

      <div className="plate">
        <div className="where">Aisle</div>
        <div className="aisle">{item.aisle || "??"}</div>
        <div className="shelf">Shelf {item.shelf || "unknown"}</div>
      </div>

      <div className="item-main">
        <div className="name">{item.name}</div>
        <div className="qty">
          <b>×{item.qty}</b> to pick
        </div>
        <div className="sku-line">{item.sku_id}</div>

        {flag && <div className="line-flag" data-state={item.state}>{flag}</div>}
        {item.substituted_from && (
          <div className="swapped">Swapped in for {item.substituted_from}. The bill is already updated.</div>
        )}

        <button
          className="verify-item-btn"
          data-verified={verified}
          data-failed={mismatch}
          onClick={onVerifyProduct}
        >
          <span>📷</span>
          <span>
            <b>
              {verified
                ? "Product verified"
                : mismatch
                ? "Do not confirm — product mismatch"
                : unclear
                ? "Image unclear — retake the photo"
                : "Verify product"}
            </b>
            <small>
              {verified
                ? `${productResult.detected_product} · ${Math.round(productResult.confidence * 100)}% confidence`
                : mismatch
                ? `Detected ${productResult.detected_product}`
                : "Check the label against the expected SKU before you confirm"}
            </small>
          </span>
        </button>
      </div>
    </div>
  );
}
