import { useCallback, useEffect, useRef, useState } from "react";

const LOCALE = { te: "te-IN", hi: "hi-IN", en: "en-IN" };

// Chrome scores every transcript 0..1 (0 = "the engine did not say", so it is treated as unknown).
// Below this the text is more likely aisle noise or a colleague talking than a command; the
// picker gets a one-tap "send it anyway" instead of an order being changed.
const MIN_CONFIDENCE = 0.45;
// A noisy aisle can keep the recogniser open. One capture is capped so chatter cannot pile up.
const MAX_LISTEN_MS = 8000;

/** Android Chrome sometimes reports the same phrase twice ("teesukunnanu teesukunnanu"). */
function collapseRepeats(text) {
  const words = text.split(/\s+/).filter(Boolean);
  const half = words.length / 2;
  if (
    words.length >= 2 &&
    Number.isInteger(half) &&
    words.slice(0, half).join(" ").toLowerCase() === words.slice(half).join(" ").toLowerCase()
  ) {
    return words.slice(0, half).join(" ");
  }
  return words.join(" ");
}

/**
 * Browser speech capture.
 * Click-to-toggle instead of press-and-hold, because pointerup can fire before SpeechRecognition
 * has produced a result, especially on desktop.
 *
 * Each capture is a "session" that ends exactly once: with a result, an error, or a silent end.
 * That is what keeps the panel from parking on LISTENING when the browser stops without saying why.
 *
 * onError(message, heardText?) - heardText is set when something was heard but not trusted, so the
 * caller can offer to send it anyway.
 */
export function useVoice(lang, onResult, onError) {
  const [listening, setListening] = useState(false);
  const [supported, setSupported] = useState(true);
  const recRef = useRef(null);
  const sessionRef = useRef({ settled: true, cancelled: false });
  const timerRef = useRef(null);
  const resultRef = useRef(onResult);
  const errorRef = useRef(onError);
  resultRef.current = onResult;
  errorRef.current = onError;

  useEffect(() => {
    const Ctor = window.SpeechRecognition || window.webkitSpeechRecognition;
    const secure = window.isSecureContext || window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";

    if (!Ctor || !secure) {
      setSupported(false);
      errorRef.current?.(
        !secure
          ? "Voice input needs HTTPS (or localhost). Open the deployed app over HTTPS."
          : "This browser does not provide Speech Recognition. Use Chrome/Edge or the tap buttons."
      );
      return;
    }

    const rec = new Ctor();
    rec.lang = LOCALE[lang] || LOCALE.te;
    rec.interimResults = false;
    rec.maxAlternatives = 1;
    rec.continuous = false;

    rec.onstart = () => setListening(true);

    rec.onresult = (e) => {
      const session = sessionRef.current;
      // One capture -> one decision. Ignore repeats from the same session and anything cancelled.
      if (session.settled || session.cancelled) return;
      session.settled = true;

      const results = Array.from(e.results);
      const text = collapseRepeats(results.map((r) => r[0].transcript).join(" ")).trim();
      const scored = results.map((r) => r[0].confidence).filter((c) => c > 0);
      const confidence = scored.length ? Math.min(...scored) : null;

      if (text.replace(/[\s.,!?\u0964]/g, "").length < 2) {
        errorRef.current?.("I didn't catch that. Please speak again.");
      } else if (confidence !== null && confidence < MIN_CONFIDENCE) {
        errorRef.current?.(
          `I'm not sure I heard that right (“${text}”). Say it again, or send it as is.`,
          text
        );
      } else {
        resultRef.current(text);
      }
    };

    rec.onerror = (e) => {
      setListening(false);
      const session = sessionRef.current;
      if (session.settled || session.cancelled) return; // already answered, or we aborted it ourselves
      session.settled = true;
      const messages = {
        "not-allowed": "Microphone permission was denied. Allow microphone access for this site and try again.",
        "service-not-allowed": "Browser speech recognition is blocked. Try Chrome/Edge over HTTPS.",
        "no-speech": "No speech detected. Tap the mic and speak clearly.",
        "audio-capture": "No microphone was found. Check the microphone selected by your browser.",
        network: "Speech recognition needs an internet connection. Check Wi-Fi and try again.",
        aborted: "Voice capture stopped. Tap the mic to try again.",
      };
      errorRef.current?.(messages[e.error] || `Voice error: ${e.error || "unknown"}`);
    };

    rec.onend = () => {
      clearTimeout(timerRef.current);
      setListening(false);
      // The browser stopped without a result or an error (very short audio, dropped connection).
      // Without this the panel stays on "Listening" with the mic already closed.
      const session = sessionRef.current;
      if (!session.settled && !session.cancelled) {
        session.settled = true;
        errorRef.current?.("I didn't catch that. Tap the mic and speak again.");
      }
    };
    recRef.current = rec;

    return () => {
      clearTimeout(timerRef.current);
      sessionRef.current = { settled: true, cancelled: true };
      rec.onresult = rec.onerror = rec.onend = rec.onstart = null;
      try { rec.abort(); } catch (_) {}
      recRef.current = null;
      // Switching language mid-capture used to leave `listening` stuck true, which blocked the mic.
      setListening(false);
    };
  }, [lang]);

  const start = useCallback(() => {
    const rec = recRef.current;
    if (!rec || listening) return;
    stopSpeaking(); // never listen over our own reply: "Picked. Move to the next item." contains "picked"
    sessionRef.current = { settled: false, cancelled: false };
    try {
      rec.lang = LOCALE[lang] || LOCALE.te;
      rec.start();
      clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        try { rec.stop(); } catch (_) {}
      }, MAX_LISTEN_MS);
    } catch (err) {
      // InvalidStateError means the browser still considers recognition active.
      if (err?.name !== "InvalidStateError") {
        sessionRef.current.settled = true;
        errorRef.current?.("Could not start the microphone. Please try again.");
      }
    }
  }, [lang, listening]);

  /** "I'm done talking": recognise what was said so far and send it. */
  const stop = useCallback(() => {
    try { recRef.current?.stop(); } catch (_) {}
  }, []);

  /** "Forget it": nothing is sent. Used when the language changes mid-capture. */
  const cancel = useCallback(() => {
    sessionRef.current = { settled: true, cancelled: true };
    clearTimeout(timerRef.current);
    try { recRef.current?.abort(); } catch (_) {}
    setListening(false);
  }, []);

  return { listening, supported, start, stop, cancel };
}

// --- Speech output ---------------------------------------------------------------------------
let currentAudio = null; // the Polly clip that is playing, if any
let epoch = 0;           // bumped whenever speech is stopped, so a slow reply can tell it was superseded

/** Changes each time speech is stopped. Compare before/after an await to see if you were superseded. */
export function speechEpoch() {
  return epoch;
}

/** Stops the browser voice and any Polly clip. Safe to call at any time. */
export function stopSpeaking() {
  epoch += 1;
  try { window.speechSynthesis?.cancel(); } catch (_) {}
  if (currentAudio) {
    try { currentAudio.pause(); } catch (_) {}
    currentAudio = null;
  }
}

/** Registers a Polly clip as the one playing (stopping any previous one, so replies never overlap). */
export function registerAudio(audio) {
  stopSpeaking();
  currentAudio = audio;
  audio.addEventListener("ended", () => {
    if (currentAudio === audio) currentAudio = null;
  });
  return audio;
}

/** Chrome returns an empty voice list on first use; wait (briefly) for it to load. */
function loadVoices(timeoutMs = 800) {
  return new Promise((resolve) => {
    const synth = window.speechSynthesis;
    const ready = synth.getVoices();
    if (ready.length) return resolve(ready);
    let timer;
    const done = () => {
      clearTimeout(timer);
      synth.removeEventListener("voiceschanged", done);
      resolve(synth.getVoices());
    };
    timer = setTimeout(done, timeoutMs);
    synth.addEventListener("voiceschanged", done);
  });
}

/** Best installed voice for the language. Android reports "te_IN", desktop "te-IN". */
function pickVoice(voices, target) {
  const norm = (l) => (l || "").toLowerCase().replace("_", "-");
  const want = norm(target);
  const exact = voices.filter((v) => norm(v.lang) === want);
  const family = voices.filter((v) => norm(v.lang).startsWith(want.slice(0, 2)));
  const pool = exact.length ? exact : family;
  return pool.find((v) => /google|natural|neural/i.test(v.name)) || pool[0] || null;
}

/**
 * Speaks the reply in the picker's language. Resolves true once speech has started (or was
 * superseded by something newer) and false when the device cannot speak it, so the caller can
 * show the text instead. It never speaks Telugu/Hindi with an English voice, which reads as noise.
 */
export async function speak(text, lang) {
  if (!text || !window.speechSynthesis) return false;
  const synth = window.speechSynthesis;
  const target = LOCALE[lang] || LOCALE.te;
  const myEpoch = epoch;

  const voice = pickVoice(await loadVoices(), target);
  if (epoch !== myEpoch) return true; // the mic was tapped or a newer reply started while voices loaded
  if (!voice && lang !== "en") return false;

  return new Promise((resolve) => {
    const u = new SpeechSynthesisUtterance(text);
    u.lang = target;
    if (voice) u.voice = voice;
    u.rate = 0.95;

    let settled = false;
    const settle = (ok) => {
      if (!settled) {
        settled = true;
        resolve(ok);
      }
    };
    u.onstart = () => settle(true);
    u.onend = () => settle(true);
    u.onerror = (e) => settle(e.error === "interrupted" || e.error === "canceled"); // cut off by a newer reply: fine

    window.__dsUtterance = u; // Chrome can garbage-collect an utterance mid-speech and drop its events
    synth.cancel();
    setTimeout(() => synth.speak(u), 60); // speak() in the same tick as cancel() is silently dropped
    setTimeout(() => settle(false), 4000); // never hang the caller if speech never starts
  });
}
