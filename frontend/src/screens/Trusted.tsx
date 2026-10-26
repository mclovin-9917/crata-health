import QRCode from "qrcode";
import { ChevronLeft, Clock, Droplet, ShieldAlert, Siren, UserMinus } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type TrustedEmergency, type TrustedPerson } from "../api";
import { useMe } from "../App";
import { ErrorNote } from "../components";
import { useLoad } from "../lib";

/** The trusted person's side: people who trust me, and starting an emergency for one of them.
 * They never see the emergency card itself: only the basics, and a short-lived QR code that a
 * doctor scans to open the card (decided 2026-10-12). */
export default function TrustedScreen() {
  const me = useMe();
  const { data, error, setData } = useLoad(() => api.trustedBy(me.id), [me.id]);
  const [asking, setAsking] = useState<string | null>(null); // id of the person
  const [leaving, setLeaving] = useState<string | null>(null); // id of the person
  const [busy, setBusy] = useState(false);
  const [started, setStarted] = useState<{ person: TrustedPerson; res: TrustedEmergency } | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const start = async (p: TrustedPerson) => {
    setBusy(true);
    setActionError(null);
    try {
      const res = await api.trustedEmergency(p.id);
      setStarted({ person: p, res });
      setAsking(null);
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const stepDown = async (p: TrustedPerson) => {
    setBusy(true);
    setActionError(null);
    try {
      setData(await api.stepDown(me.id, p.id));
      setLeaving(null);
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (started) {
    return <ForTheDoctor person={started.person} res={started.res} onDone={() => setStarted(null)} onAgain={() => start(started.person)} />;
  }
  return (
    <div className="space-y-4">
      <div>
        <h1 className="title">People who trust me</h1>
        <p className="mt-1 text-ink-600">
          In an emergency you get a QR code to show the doctor. You see only their name, blood group and allergies.
        </p>
      </div>
      <ErrorNote error={error ?? actionError} />
      {data?.length === 0 && <p className="text-sm text-ink-500">Nobody has added you as a trusted person.</p>}
      <ul className="space-y-3">
        {data?.map((p) => (
          <li key={p.id} className="card space-y-3 p-4">
            <div className="flex flex-wrap items-center gap-3">
              <span className="text-lg font-bold">{p.name}</span>
              {asking !== p.id && (
                <button className="btn ml-auto [background:var(--grad-emergency)]" onClick={() => setAsking(p.id)}>
                  <Siren size={16} aria-hidden /> Start an emergency
                </button>
              )}
            </div>
            {asking === p.id && (
              <div className="space-y-3 rounded-2xl bg-red-50 p-4 ring-1 ring-red-200">
                <p className="flex gap-2 text-sm text-red-900">
                  <Siren size={16} className="mt-0.5 shrink-0" aria-hidden />
                  <span>
                  Start an emergency for {p.name}? {p.name} and their other trusted people get an alert. You get a
                  QR code to show a doctor.</span>
                </p>
                <div className="flex flex-wrap gap-2">
                  <button className="btn [background:var(--grad-emergency)]" disabled={busy} onClick={() => start(p)}>
                    {busy ? "Starting…" : "Start emergency"}
                  </button>
                  <button className="btn-light" disabled={busy} onClick={() => setAsking(null)}>Cancel</button>
                </div>
              </div>
            )}
            {asking !== p.id && (leaving === p.id ? (
              <div className="flex flex-wrap items-center gap-2 rounded-2xl bg-ink-50/80 p-3 text-sm ring-1 ring-ink-100">
                <span className="flex-1">Stop being {p.name}'s trusted person? They get an alert.</span>
                <button className="btn" disabled={busy} onClick={() => stepDown(p)}>Stop being trusted</button>
                <button className="btn-light" disabled={busy} onClick={() => setLeaving(null)}>Cancel</button>
              </div>
            ) : (
              <button className="link-quiet" onClick={() => setLeaving(p.id)}>
                <UserMinus size={16} aria-hidden /> Stop being their trusted person
              </button>
            ))}
          </li>
        ))}
      </ul>
    </div>
  );
}

function minutesLeft(expiresAt: string): number {
  return Math.max(0, Math.ceil((new Date(expiresAt).getTime() - Date.now()) / 60000));
}

function ForTheDoctor({ person, res, onDone, onAgain }: {
  person: TrustedPerson;
  res: TrustedEmergency;
  onDone: () => void;
  onAgain: () => void;
}) {
  const url = `${window.location.origin}${window.location.pathname}#/doctor/${person.id}/${res.code}`;
  const [qr, setQr] = useState<string | null>(null);
  const [left, setLeft] = useState(minutesLeft(res.expires_at));

  useEffect(() => {
    QRCode.toDataURL(url, { margin: 1, width: 420 }).then(setQr, () => setQr(null));
  }, [url]);
  useEffect(() => {
    const t = setInterval(() => setLeft(minutesLeft(res.expires_at)), 15000);
    return () => clearInterval(t);
  }, [res.expires_at]);

  const b = res.basics;
  return (
    <div className="space-y-5">
      <button className="btn-light" onClick={onDone}>
        <ChevronLeft size={16} aria-hidden /> Back to People who trust me
      </button>

      <section className="overflow-hidden rounded-card bg-white shadow-[var(--lift)] ring-1 ring-red-200">
        <header className="space-y-2 bg-red-600 p-5 text-white">
          <p className="flex items-center gap-2 text-sm font-bold tracking-wide uppercase">
            <Siren size={18} aria-hidden /> Emergency started
          </p>
          <h1 className="text-3xl font-extrabold tracking-tight">{b.name}</h1>
          {b.blood_group && (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-white px-3 py-1 text-sm font-extrabold text-red-800">
              <Droplet size={14} aria-hidden /> Blood group {b.blood_group}
            </span>
          )}
        </header>
        <div className="space-y-2 p-5">
          <p className="flex items-center gap-1.5 text-xs font-bold tracking-wider text-ink-600 uppercase">
            <ShieldAlert size={14} aria-hidden /> Allergies
          </p>
          {b.allergies.length ? (
            <ul className="flex flex-wrap gap-2">
              {b.allergies.map((a) => (
                <li key={a.label} className="rounded-2xl bg-red-50 px-3 py-2 text-lg font-extrabold text-red-800 ring-1 ring-red-200">
                  <ShieldAlert size={18} className="mr-1.5 inline align-[-0.15em]" aria-hidden />{a.label}{a.reaction ? <span className="text-sm font-semibold text-red-700"> · {a.reaction}</span> : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="font-semibold text-ink-700">No known allergies recorded</p>
          )}
        </div>
      </section>

      <section className="card space-y-4 text-center">
        <h2 className="section-title">Show this to the doctor</h2>
        {left > 0 ? (
          <>
            {qr && (
              <img src={qr} alt={`Emergency QR code for ${b.name}, for a doctor`} className="mx-auto size-64 max-w-full rounded-2xl bg-white p-2 ring-1 ring-ink-200" />
            )}
            <p className="flex items-center justify-center gap-1.5 text-sm font-semibold text-ink-700">
              <Clock size={16} aria-hidden /> Works once. {left} minute{left === 1 ? "" : "s"} left.
            </p>
            <p className="text-sm text-ink-600">
              The doctor scans it, adds their name and registry number, and sees the full emergency card. You do not see it.
            </p>
          </>
        ) : (
          <div className="space-y-3">
            <p className="text-ink-700">This QR code has expired.</p>
            <button className="btn [background:var(--grad-emergency)]" onClick={onAgain}>
              <Siren size={16} aria-hidden /> Get a new QR code
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
