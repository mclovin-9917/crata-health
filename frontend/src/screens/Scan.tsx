import { Camera, Link2, ScanLine } from "lucide-react";
import QrScanner from "qr-scanner";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { ErrorNote, Note } from "../components";
import { go } from "../lib";

// A doctor scans a patient's emergency card QR (or a trusted person's short-lived code) inside
// the app. The QR holds a link like https://<app>/#/doctor/<patient>[/<code>]. Only the patient
// id and code are taken from it, and the app opens its own doctor page: a QR made on another
// address of the app (laptop vs phone) still works, and a QR pointing anywhere else is refused.

const DOCTOR_LINK = /#\/doctor\/([A-Za-z0-9_-]{1,64})(?:\/([A-Za-z0-9_-]{8,128}))?\/?$/;

/** "#/doctor/<patient>[/<code>]" from a scanned or pasted link, or null if it isn't one. */
export function doctorPath(text: string): string | null {
  const m = text.trim().match(DOCTOR_LINK);
  return m ? `/doctor/${m[1]}${m[2] ? `/${m[2]}` : ""}` : null;
}

export default function ScanScreen() {
  const video = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState<string | null>(null); // a real problem (red)
  const [noCamera, setNoCamera] = useState<string | null>(null); // the camera is not available (a plain grey note)
  const [starting, setStarting] = useState(true);
  const [pasted, setPasted] = useState("");

  useEffect(() => {
    if (!video.current) return;
    if (!window.isSecureContext) {
      setNoCamera("The camera needs a secure (HTTPS) address, so it is off here.");
      setStarting(false);
      return;
    }
    let done = false;
    const scanner = new QrScanner(
      video.current,
      (result) => {
        if (done) return;
        const path = doctorPath(result.data);
        if (path) {
          done = true;
          scanner.stop();
          go(path);
        } else {
          setError("That is not a Crata Health emergency QR code. Try the one on the card.");
        }
      },
      { preferredCamera: "environment", highlightScanRegion: true, maxScansPerSecond: 5, returnDetailedScanResult: true },
    );
    scanner.start().then(
      () => setStarting(false),
      (e: unknown) => {
        setStarting(false);
        const name = e instanceof Error ? e.name : String(e);
        setNoCamera(/NotAllowed|denied/i.test(name)
          ? "The camera is blocked. Allow it for this site in your browser settings."
          : "Could not start a camera on this device.");
      },
    );
    return () => {
      done = true;
      scanner.destroy();
    };
  }, []);

  const openPasted = (e: FormEvent) => {
    e.preventDefault();
    const path = doctorPath(pasted);
    if (path) go(path);
    else setError("That link is not a Crata Health emergency link.");
  };

  return (
    <div className="space-y-4">
      <div>
        <h1 className="title">Scan a QR code</h1>
        {!noCamera && (
          <p className="mt-1 text-ink-600">
            Point the camera at the QR code on the patient's emergency card, or the one a trusted person shows you.
          </p>
        )}
      </div>
      {noCamera && (
        <Note role="status" icon={<Camera size={16} aria-hidden />}>{noCamera}</Note>
      )}
      {/* The camera box stays mounted (the scanner needs the video), but is not shown when there is no camera. */}
      <div className={`card space-y-3 p-3 ${noCamera ? "hidden" : ""}`}>
        <div className="relative overflow-hidden rounded-2xl bg-ink-900">
          <video ref={video} className="aspect-square w-full object-cover" muted playsInline />
          {starting && (
            <p className="absolute inset-0 grid place-items-center text-sm text-white/80" role="status">
              <span className="flex items-center gap-2"><Camera size={18} aria-hidden /> Starting the camera…</span>
            </p>
          )}
        </div>
        <p className="flex items-center gap-2 px-1 text-sm text-ink-600">
          <ScanLine size={16} aria-hidden /> Each opening is logged and the patient gets an alert.
        </p>
      </div>
      <ErrorNote error={error} />
      <form className="card space-y-2" onSubmit={openPasted}>
        <label className="block" htmlFor="scan-link">
          <span className="label">{noCamera ? "Paste the link from the card" : "Or paste the link from the card"}</span>
          <input id="scan-link" className="input" inputMode="url" placeholder="https://…/#/doctor/…" value={pasted}
            onChange={(e) => setPasted(e.target.value)} />
        </label>
        <button className="btn-light" disabled={!pasted.trim()}><Link2 size={16} aria-hidden /> Open card</button>
        {noCamera && (
          <p className="flex items-center gap-2 px-1 text-sm text-ink-600">
            <ScanLine size={16} aria-hidden /> Each opening is logged and the patient gets an alert.
          </p>
        )}
      </form>
    </div>
  );
}
