import { useEffect, useRef } from "react";

const SHOW_MS = 14000;

/**
 * The message that goes to the customer after a substitution. Composed from the figures the
 * store just confirmed, so it explains the swap rather than deciding it.
 */
export default function CustomerMessage({ msg, onClose }) {
  // onClose is a new function on every App render; a ref keeps the timer from restarting each time.
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const t = setTimeout(() => closeRef.current(), SHOW_MS);
    return () => clearTimeout(t);
  }, [msg]);

  return (
    <aside className="x-msg" role="status" aria-live="polite">
      <header>
        <b>Auto message to customer</b>
        <span>{msg.to ? `to ${msg.to}` : `order ${msg.order_id}`}</span>
        <button type="button" onClick={onClose} aria-label="Close message preview">
          ✕
        </button>
      </header>
      <p className="x-msg-bubble">{msg.text}</p>
      <small>Demo channel: shown here and kept on this device, not sent to a real phone.</small>
    </aside>
  );
}