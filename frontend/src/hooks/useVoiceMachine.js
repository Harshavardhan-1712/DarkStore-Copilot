import { useCallback, useRef, useState } from "react";

/**
 * The voice pipeline as an explicit state machine.
 *
 * READY -> LISTENING -> HEARD -> UNDERSTANDING -> POLICY_CHECK -> ACTION -> RESPONDING -> DONE
 * and from any state -> ERROR.
 *
 * UNDERSTANDING and POLICY_CHECK both happen inside the single /intent request: the backend
 * interprets first, then validates against inventory and policy. The machine advances between
 * them on a short timer while that request is in flight, so the picker can see which half of
 * the work is happening, and lands on ACTION only when the server has actually answered.
 */
export const VOICE_STATES = [
  "READY",
  "LISTENING",
  "HEARD",
  "UNDERSTANDING",
  "POLICY_CHECK",
  "ACTION",
  "RESPONDING",
  "DONE",
];

export const STATE_COPY = {
  READY: { dot: "○", label: "Ready", hint: "Tap the mic and speak" },
  LISTENING: { dot: "●", label: "Listening", hint: "Speak now" },
  HEARD: { dot: "◉", label: "Heard you", hint: "Sending to the store" },
  UNDERSTANDING: { dot: "◐", label: "Understanding", hint: "AI is reading the utterance" },
  POLICY_CHECK: { dot: "◑", label: "Policy check", hint: "Store rules and inventory" },
  ACTION: { dot: "◆", label: "Action", hint: "Backend applying the decision" },
  RESPONDING: { dot: "♪", label: "Responding", hint: "Speaking the reply" },
  DONE: { dot: "✓", label: "Done", hint: "Ready for the next item" },
  ERROR: { dot: "✕", label: "Error", hint: "Nothing was changed" },
};

export function useVoiceMachine() {
  const [state, setState] = useState("READY");
  const [transcript, setTranscript] = useState("");
  const [error, setError] = useState("");
  const timers = useRef([]);

  const clearTimers = useCallback(() => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
  }, []);

  const to = useCallback(
    (next) => {
      clearTimers();
      setState(next);
      if (next === "READY" || next === "LISTENING") setError("");
    },
    [clearTimers]
  );

  const heard = useCallback(
    (text) => {
      clearTimers();
      setTranscript(text);
      setState("HEARD");
      // The request is already on the wire; walk the two server-side phases so the picker
      // can see that interpretation and policy validation are separate steps.
      timers.current.push(setTimeout(() => setState("UNDERSTANDING"), 180));
      timers.current.push(setTimeout(() => setState("POLICY_CHECK"), 700));
    },
    [clearTimers]
  );

  const fail = useCallback(
    (message) => {
      clearTimers();
      setError(message || "Voice failed. Use the buttons below.");
      setState("ERROR");
    },
    [clearTimers]
  );

  const finish = useCallback(
    (speaking) => {
      clearTimers();
      setState("ACTION");
      timers.current.push(setTimeout(() => setState(speaking ? "RESPONDING" : "DONE"), 320));
      if (speaking) timers.current.push(setTimeout(() => setState("DONE"), 1900));
      // Never leave the panel parked on DONE forever; it returns to READY on its own.
      timers.current.push(setTimeout(() => setState("READY"), 7000));
    },
    [clearTimers]
  );

  const reset = useCallback(() => {
    clearTimers();
    setTranscript("");
    setError("");
    setState("READY");
  }, [clearTimers]);

  return { state, transcript, error, to, heard, finish, fail, reset, setTranscript };
}