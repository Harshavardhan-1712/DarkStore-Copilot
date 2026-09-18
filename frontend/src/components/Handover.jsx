import { useEffect, useRef } from "react";
import QRCode from "qrcode";

/** Rider scans this. The token is generated server-side only after a passing bag check. */
export default function Handover({ order, handover, busy, onDispatch }) {
  const canvasRef = useRef(null);

  useEffect(() => {
    if (canvasRef.current && handover?.qr_payload) {
      QRCode.toCanvas(canvasRef.current, handover.qr_payload, { width: 220, margin: 1 });
    }
  }, [handover]);

  return (
    <div className="center">
      <h1>Ready for pickup</h1>
      <p>
        {order.order_id} · {order.items.length} items · ₹{order.invoice_total}
      </p>
      <canvas ref={canvasRef} className="qr" aria-label="Handover QR code" />
      <div className="token">{handover?.token}</div>
      <button className="btn go" disabled={busy} onClick={onDispatch}>
        Rider has taken the bag
      </button>
    </div>
  );
}
