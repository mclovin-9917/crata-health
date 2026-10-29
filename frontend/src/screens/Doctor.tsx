import { CircleCheck, House, Siren, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type EmergencySummary, type MeView } from "../api";
import { CrataMark } from "../logo";
import { EmergencyCard, ErrorNote } from "../components";
import { go, rememberReturn } from "../lib";

/** Opened from the card's QR code, by a doctor. No patient shell, no patient data before the
 * emergency is declared. A signed-in doctor opens the card under their account; a doctor
 * without one types a name and registry ID, and the log says "no account". */
export default function DoctorScreen({ patientId, code }: { patientId: string; code?: string }) {
  const [account, setAccount] = useState<MeView | null | undefined>(undefined);
  const [name, setName] = useState("");
  const [registry, setRegistry] = useState("");
  const [place, setPlace] = useState("");
  const [declared, setDeclared] = useState(false);
  const [busy, setBusy] = useState(false);
  const [summary, setSummary] = useState<EmergencySummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.auth.me().then(setAccount, () => setAccount(null));
  }, []);
  const doctor = account?.role === "doctor" ? account : null;
  // What is still missing before the card can be opened (said under the disabled button).
  const fields = [...(!doctor && !name.trim() ? ["name"] : []), ...(!doctor && !registry.trim() ? ["registry number"] : [])];
  const missing = [
    ...(fields.length ? [`Enter your ${fields.join(" and ")}`] : []),
    ...(!declared ? [fields.length ? "tick the declaration" : "Tick the declaration"] : []),
  ];

  const signOut = () => api.auth.logout().finally(() => setAccount(null));
  // A signed-in doctor goes back to their home; a doctor without an account closes the card,
  // so the patient's details are not left on screen.
  const done = () => {
    if (doctor) {
      go("/");
      return;
    }
    setSummary(null);
    setDeclared(false);
  };
  const signIn = () => {
    rememberReturn(window.location.hash); // back to this QR page after signing in
    go("/");
  };

  const open = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await api.emergencyAccess(patientId, {
        ...(doctor ? {} : { actor_label: name.trim(), registry_id: registry.trim() }),
        place: place.trim() || undefined,
        code,
      });
      setSummary(res.summary);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-2xl space-y-5 px-4 py-6">
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-2 font-bold tracking-tight text-ink-900">
          <CrataMark size={28} />
          Crata Health
        </span>
        <span className="chip bg-ink-100 text-ink-700">Prototype: registry numbers are not verified</span>
        {doctor && (
          <button className="btn-light ml-auto py-1.5" onClick={() => go("/")}>
            <House size={16} aria-hidden /> Home
          </button>
        )}
      </div>

      {account === undefined ? (
        <p className="text-ink-500">Loading…</p>
      ) : account && !doctor ? (
        <div className="card-solid space-y-3">
          <h1 className="section-title">This page is for doctors</h1>
          <p className="text-sm text-ink-700">
            You are signed in as {account.name}, a patient. Doctors open this page with a doctor account, or without one
            after you sign out.
          </p>
          <div className="flex flex-wrap gap-2">
            <button className="btn-light" onClick={signOut}>Sign out</button>
            <button className="btn-light" onClick={() => go("/")}>Back to Home</button>
          </div>
        </div>
      ) : summary ? (
        <>
          <p className="flex items-start gap-2 rounded-2xl bg-green-50 px-4 py-3 text-sm text-green-900 ring-1 ring-green-200">
            <CircleCheck size={18} className="mt-0.5 shrink-0" aria-hidden />
            Access logged. The patient and their trusted people get an alert.
          </p>
          <EmergencyCard summary={summary} />
          <div className="card-solid space-y-3">
            <p className="text-sm text-ink-600">
              Scan the patient's QR code again to reopen the card. {code ? "A code from a trusted person works only once." : ""}
            </p>
            <button className="btn-light" onClick={done}>
              {doctor ? <><House size={16} aria-hidden /> Back to Home</> : <><X size={16} aria-hidden /> Close the card</>}
            </button>
          </div>
        </>
      ) : (
        <div className="card-solid space-y-4">
          <div className="flex items-center gap-3">
            <span className="tile [background:var(--grad-emergency)]"><Siren size={20} aria-hidden /></span>
            <div>
              <h1 className="section-title">Emergency access</h1>
              <p className="text-sm text-ink-600">
                {code
                  ? "This patient's trusted person gave you this code. It works once."
                  : "Open this patient's emergency card. Every opening is logged."}
              </p>
            </div>
          </div>
          {doctor ? (
            <p className="rounded-2xl bg-ink-50 px-4 py-3 text-sm text-ink-700 ring-1 ring-ink-100">
              Signed in as <strong>{doctor.name}</strong>, registry number {doctor.registry_id} (not verified in this prototype).
            </p>
          ) : (
            <>
              <p className="text-sm text-ink-600">No account needed. The patient's access log will say "no account".</p>
              <label className="block" htmlFor="doc-name">
                <span className="label">Your name</span>
                <input id="doc-name" className="input min-h-11" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
              </label>
              <label className="block" htmlFor="doc-registry">
                <span className="label">Medical registry number</span>
                <input id="doc-registry" className="input min-h-11" value={registry} onChange={(e) => setRegistry(e.target.value)} />
              </label>
            </>
          )}
          <label className="block" htmlFor="doc-place">
            <span className="label">Hospital or place (optional)</span>
            <input id="doc-place" className="input min-h-11" value={place} onChange={(e) => setPlace(e.target.value)} />
          </label>
          <label className="flex min-h-11 items-start gap-3 rounded-2xl bg-red-50 px-4 py-3 text-sm text-red-900 ring-1 ring-red-200" htmlFor="doc-declare">
            <input id="doc-declare" type="checkbox" className="size-6 shrink-0 accent-red-700" checked={declared} onChange={(e) => setDeclared(e.target.checked)} />
            I declare this is a medical emergency. This access is logged and the patient gets an alert.
          </label>
          <ErrorNote error={error} />
          <button
            className="btn w-full py-3 text-base [background:var(--grad-emergency)]"
            disabled={busy || missing.length > 0}
            aria-describedby={missing.length > 0 ? "doc-missing" : undefined}
            onClick={open}
          >
            <Siren size={18} aria-hidden /> {busy ? "Opening…" : "Open emergency card"}
          </button>
          {missing.length > 0 && (
            <p id="doc-missing" className="text-center text-sm text-ink-600">
              {missing.join(", and ")}.
            </p>
          )}
          {!doctor && (
            <div className="text-center">
              <button className="link-quiet" onClick={signIn}>Have a doctor account? Sign in</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
