import QRCode from "qrcode";
import { Copy, Info, QrCode } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import { useMe } from "../App";
import { EmergencyCard, ErrorNote, Timeline } from "../components";
import { useLoad } from "../lib";

export default function EmergencyScreen() {
  const me = useMe();
  const { data, error } = useLoad(() => api.emergency(me.id), [me.id]);
  const doctorUrl = `${window.location.origin}${window.location.pathname}#/doctor/${me.id}`;
  // A QR made on localhost points a scanning phone at itself, so it cannot open (found in testing).
  const onThisComputerOnly = /^(localhost|127\.0\.0\.1|\[::1\])$/.test(window.location.hostname);
  const [qr, setQr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    QRCode.toDataURL(doctorUrl, { margin: 1, width: 360 }).then(setQr, () => setQr(null));
  }, [doctorUrl]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(doctorUrl);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  if (error) return <ErrorNote error={error} />;
  if (!data) return <p className="text-ink-500">Loading…</p>;
  return (
    <div className="space-y-6">
      <div>
        <h1 className="title">Emergency card</h1>
        <p className="mt-1 text-ink-600">What a doctor sees in an emergency. Only facts you confirmed are on it.</p>
      </div>

      <EmergencyCard summary={data} showTimeline={false} />

      <section className="card-solid space-y-4">
        <div className="flex items-center gap-3">
          <span className="tile [background:var(--grad-emergency)]"><QrCode size={20} aria-hidden /></span>
          <div>
            <h2 className="section-title">QR code for a doctor</h2>
            <p className="text-sm text-ink-600">Save it as a photo or print it for your wallet.</p>
          </div>
        </div>
        <div className="flex flex-col items-center gap-5 sm:flex-row sm:items-start">
          {qr && (
            <img src={qr} alt="QR code that opens your emergency card for a doctor" className="size-44 rounded-2xl bg-white p-2 ring-1 ring-ink-200" />
          )}
          <p className="text-sm text-ink-700">
            When a doctor scans it, the card opens and you and your trusted people get an alert.
          </p>
        </div>
        {onThisComputerOnly && (
          <p className="flex gap-2 rounded-2xl bg-ink-100 px-4 py-3 text-sm text-ink-700">
            <Info size={16} className="mt-0.5 shrink-0" aria-hidden />
            <span>
              You opened the app at {window.location.host}, which only this computer can reach. This QR code will not
              open on a phone. To try it on a phone, open the app through its shared (Tailscale) address.
            </span>
          </p>
        )}
        <div className="flex flex-wrap items-center gap-2 rounded-2xl bg-ink-50/80 px-3 py-2 ring-1 ring-ink-100">
          <code className="min-w-0 flex-1 truncate text-xs text-ink-600">{doctorUrl}</code>
          <button className="btn-light py-1.5" onClick={copy}>
            <Copy size={14} aria-hidden /> {copied ? "Copied" : "Copy link"}
          </button>
        </div>
      </section>

      <Timeline entries={data.timeline} />
    </div>
  );
}
