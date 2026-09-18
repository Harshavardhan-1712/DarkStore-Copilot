import { useEffect, useState } from "react";

/* Stage insurance. A dark store on demo day has no mic-friendly silence and no reliable wifi,
   so these send a pre-baked intent straight to the backend. It still runs the same guardrail,
   so what the audience sees is the real validation path, not a mocked screen. */
const CANNED = (item, substitute) => [
  {
    label: 'Picker: "idi teesukunnanu" (picked it)',
    utterance: "idi teesukunnanu",
    intent: {
      action: "CONFIRM_PICK",
      selected_sku: item?.sku_id,
      spoken_response_telugu: "బాగుంది, తర్వాత వస్తువుకి వెళ్లండి.",
      spoken_response_hindi: "ठीक है, अगला सामान लीजिए।",
      spoken_response_english: "Picked. Move to the next item.",
      reason: "Picker confirmed the item is in hand.",
    },
  },
  {
    label: 'Picker: "Amul milk ledu" (out of stock)',
    utterance: "Amul milk ledu",
    intent: substitute && {
      action: "SUBSTITUTE_ITEM",
      selected_sku: substitute.sku_id,
      spoken_response_telugu: "అముల్ లేదు. బదులుగా నందిని పాలు తీసుకోండి.",
      spoken_response_hindi: "अमूल नहीं है। बदले में नंदिनी दूध लीजिए।",
      spoken_response_english: "Amul is unavailable. Take Nandini milk instead.",
      reason: "Item is out of stock; nearest approved substitute offered.",
    },
  },
  {
    label: 'Picker: "evaro teesukunnaru" (someone already took it)',
    utterance: "evaro teesukunnaru",
    intent: {
      action: "ITEM_ALREADY_TAKEN",
      selected_sku: item?.sku_id,
      spoken_response_telugu: "ఇది ఇంకొకరు తీసుకున్నారు. స్టోర్ చెక్ చేస్తుంది.",
      spoken_response_hindi: "यह सामान किसी ने पहले ही ले लिया। स्टोर जाँच रहा है।",
      spoken_response_english: "Another picker has already taken this item. Checking the store now.",
      reason: "Picker reported another picker took this order's unit.",
    },
  },
  {
    label: "Model returns a SKU that does not exist",
    utterance: "test guardrail",
    intent: {
      action: "SUBSTITUTE_ITEM",
      selected_sku: "SKU_TOTALLY_MADE_UP",
      spoken_response_telugu: "ఇది తీసుకోండి.",
      spoken_response_hindi: "यह लीजिए।",
      spoken_response_english: "Take this one instead.",
      reason: "Hallucinated SKU — the backend must refuse this.",
    },
  },
  {
    label: "Model returns malformed JSON",
    utterance: "test malformed",
    intent: { action: "SUBSTITUTE_ITEM", selected_sku: 42 },
  },
];

export default function DemoPanel({ item, substitute, onSimulate, forceOpen }) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onKey = (e) => {
      if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === "d") setOpen((v) => !v);
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (forceOpen) setOpen(true);
  }, [forceOpen]);

  if (!open) return null;

  return (
    <div className="demo">
      <div className="panel">
        <div style={{ opacity: 0.7, marginBottom: 4 }}>
          Simulated voice — stage fallback (Ctrl+Shift+D)
        </div>
        {CANNED(item, substitute)
          .filter((c) => c.intent)
          .map((c) => (
            <button key={c.label} onClick={() => onSimulate(c.utterance, c.intent)}>
              {c.label}
            </button>
          ))}
        <button onClick={() => setOpen(false)} style={{ opacity: 0.7 }}>
          Close
        </button>
      </div>
    </div>
  );
}
