import { Loader2, Mic, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";
import { ErrorNote } from "./components";

// The server accepts 95 s; the screen stops a little earlier so the upload never goes over.
const MAX_SECONDS = 85;
const COULD_NOT_HEAR = "Couldn't hear that. Please type it instead.";

/** "Speak instead": records up to 85 s, sends it for writing down, and hands the text back.
 * The text is only added to the box; nothing is sent until the person presses the main button. */
export function VoiceButton({ patientId, onText, disabled }: {
  patientId: string;
  onText: (text: string) => void;
  disabled?: boolean;
}) {
  const [state, setState] = useState<"idle" | "starting" | "recording" | "writing">("idle");
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const unmounted = useRef(false);

  useEffect(() => {
    if (state !== "recording") return;
    const t = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [state]);

  useEffect(() => {
    if (state === "recording" && seconds >= MAX_SECONDS) stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seconds, state]);

  // Leaving the screen mid-recording (or while the browser asks for the microphone) must release it.
  useEffect(() => {
    unmounted.current = false;
    return () => {
      unmounted.current = true;
      const rec = recorder.current;
      if (rec) rec.onstop = null;
      if (rec && rec.state !== "inactive") rec.stop();
      stream.current?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  const start = async () => {
    if (state !== "idle") return; // ignore taps while the microphone is being asked for
    setError(null);
    setState("starting");
    let s: MediaStream;
    try {
      s = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      if (!unmounted.current) {
        setState("idle");
        setError("We could not use your microphone. Check that the browser is allowed to use it, or type instead.");
      }
      return;
    }
    if (unmounted.current) {
      s.getTracks().forEach((t) => t.stop());
      return;
    }
    stream.current = s;
    const rec = new MediaRecorder(s);
    const parts: Blob[] = [];
    rec.ondataavailable = (e) => parts.push(e.data);
    rec.onstop = async () => {
      s.getTracks().forEach((t) => t.stop());
      const blob = new Blob(parts, { type: rec.mimeType || "audio/webm" });
      if (blob.size === 0) {
        setState("idle");
        setError(COULD_NOT_HEAR);
        return;
      }
      setState("writing");
      try {
        const { text } = await api.voice(patientId, blob);
        if (unmounted.current) return;
        if (text.trim()) onText(text.trim());
        else setError(COULD_NOT_HEAR);
      } catch (e) {
        if (unmounted.current) return;
        // The server's own words for a limit (429), a file too big (413) or unusable audio (422).
        const status = e instanceof ApiError ? e.status : 0;
        setError([413, 422, 429].includes(status) ? (e as Error).message : COULD_NOT_HEAR);
      } finally {
        if (!unmounted.current) setState("idle");
      }
    };
    rec.start();
    recorder.current = rec;
    setSeconds(0);
    setState("recording");
  };

  function stop() {
    const rec = recorder.current;
    if (rec && rec.state !== "inactive") rec.stop();
  }

  return (
    <div className="space-y-2">
      {state === "recording" ? (
        <button
          type="button"
          className="btn"
          onClick={stop}
          aria-label={`Stop recording, ${seconds} of ${MAX_SECONDS} seconds`}
        >
          <Square size={16} aria-hidden /> Stop ({seconds} of {MAX_SECONDS} s)
        </button>
      ) : state === "writing" || state === "starting" ? (
        <p className="inline-flex min-h-11 items-center gap-2 text-sm text-ink-600" role="status">
          <Loader2 size={16} className="animate-spin" aria-hidden />
          {state === "writing" ? "Writing it down…" : "Waiting for the microphone…"}
        </p>
      ) : (
        <button
          type="button"
          className="btn-light"
          onClick={start}
          disabled={disabled}
          aria-label="Speak instead of typing"
        >
          <Mic size={16} aria-hidden /> Speak instead
        </button>
      )}
      <ErrorNote error={error} />
    </div>
  );
}

/** Adds spoken text after what is already typed. */
export const appendText = (current: string, add: string) => (current.trim() ? `${current.trimEnd()} ${add}` : add);
