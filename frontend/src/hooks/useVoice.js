import { useCallback, useEffect, useRef, useState } from "react";

const LOCALE = { te: "te-IN", hi: "hi-IN", en: "en-IN" };

/**
 * Browser speech capture.
 * Uses click-to-toggle instead of press-and-hold because pointerup can fire
 * before SpeechRecognition has produced a result, especially on desktop.
 */
export function useVoice(lang, onResult, onError) {
  const [listening, setListening] = useState(false);
  const [supported, setSupported] = useState(true);
  const recRef = useRef(null);
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
      const text = Array.from(e.results).map((r) => r[0].transcript).join(" ").trim();
      if (text) {
        resultRef.current(text);
      } else {
        errorRef.current?.("I didn't catch that. Please speak again.");
      }
    };
    rec.onerror = (e) => {
      setListening(false);
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
    rec.onend = () => setListening(false);
    recRef.current = rec;

    return () => {
      rec.onresult = rec.onerror = rec.onend = rec.onstart = null;
      try { rec.abort(); } catch (_) {}
      recRef.current = null;
    };
  }, [lang]);

  const start = useCallback(() => {
    const rec = recRef.current;
    if (!rec || listening) return;
    try {
      rec.lang = LOCALE[lang] || LOCALE.te;
      rec.start();
    } catch (err) {
      // InvalidStateError means the browser still considers recognition active.
      if (err?.name !== "InvalidStateError") {
        errorRef.current?.("Could not start the microphone. Please try again.");
      }
    }
  }, [lang, listening]);

  const stop = useCallback(() => {
    const rec = recRef.current;
    if (!rec) return;
    try { rec.stop(); } catch (_) {}
  }, []);

  return { listening, supported, start, stop };
}

/** Speaks the response in the picker's language when the device has a matching voice. */
export function speak(text, lang) {
  if (!text || !window.speechSynthesis) return false;

  const target = LOCALE[lang] || LOCALE.te;
  const voices = window.speechSynthesis.getVoices();
  const exact = voices.find((v) => v.lang?.toLowerCase() === target.toLowerCase());
  const family = voices.find((v) => v.lang?.toLowerCase().startsWith(target.slice(0, 2).toLowerCase()));
  const u = new SpeechSynthesisUtterance(text);
  u.lang = target;
  u.voice = exact || family || null;
  u.rate = 1;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(u);
  return true;
}
