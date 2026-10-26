import { Info, Smartphone, UserRound } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { api, type CodeSent, type DemoAccount, type MeView } from "../api";
import { ErrorNote } from "../components";

// Sign-in (decided 2026-10-12): phone number + one-time code for the real app. Hackathon judges
// pick a demo account from the list instead. There is no SMS provider yet, so the code is shown
// here, labelled.

const BLOOD_GROUPS = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"];

export default function SignInScreen({ onSignedIn }: { onSignedIn: (me: MeView) => void }) {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="title">Sign in</h1>
        <p className="mt-1 text-ink-600">Keep your health record in one place. Every fact shows where it came from.</p>
      </div>
      <DemoPicker onSignedIn={onSignedIn} />
      <PhoneSignIn onSignedIn={onSignedIn} />
    </div>
  );
}

/** Hackathon judges: one of the demo accounts, no phone needed. Hidden when switched off. */
function DemoPicker({ onSignedIn }: { onSignedIn: (me: MeView) => void }) {
  const [accounts, setAccounts] = useState<DemoAccount[]>([]);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.auth.demoAccounts().then((list) => {
      setAccounts(list);
      setKey(list[0]?.key ?? "");
    }, () => setAccounts([]));
  }, []);
  if (!accounts.length) return null;

  const chosen = accounts.find((a) => a.key === key);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSignedIn(await api.auth.demo(key));
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  return (
    <form className="card space-y-3" onSubmit={submit}>
      <div className="flex items-start gap-3">
        <span className="tile"><UserRound size={20} aria-hidden /></span>
        <div>
          <h2 className="section-title">Try the demo</h2>
          <p className="text-sm text-ink-600">Pick a demo account. All its data is made up.</p>
        </div>
      </div>
      <label className="block">
        <span className="label">Demo account</span>
        <select className="input" value={key} onChange={(e) => setKey(e.target.value)}>
          {accounts.map((a) => (
            <option key={a.key} value={a.key}>{a.name}</option>
          ))}
        </select>
      </label>
      {chosen && <p className="text-sm text-ink-600">{chosen.about}.</p>}
      <ErrorNote error={error} />
      <button className="btn" disabled={busy || !key}>{busy ? "Signing in…" : `Continue as ${chosen?.name ?? "demo"}`}</button>
    </form>
  );
}

type Step = { kind: "phone" } | { kind: "code"; sent: CodeSent } | { kind: "profile"; sent: CodeSent };

/** Phone number, then the one-time code, then (a new number only) name and role. */
function PhoneSignIn({ onSignedIn }: { onSignedIn: (me: MeView) => void }) {
  const [step, setStep] = useState<Step>({ kind: "phone" });
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const sendCode = (e?: FormEvent) => {
    e?.preventDefault();
    run(async () => {
      const sent = await api.auth.code(phone);
      setCode("");
      setStep({ kind: "code", sent });
    });
  };

  const verify = (e: FormEvent, sent: CodeSent) => {
    e.preventDefault();
    run(async () => {
      const r = await api.auth.verify(sent.challenge_id, code);
      if (r.status === "signed_in" && r.me) onSignedIn(r.me);
      else setStep({ kind: "profile", sent });
    });
  };

  return (
    <div className="card space-y-3">
      <div className="flex items-start gap-3">
        <span className="tile"><Smartphone size={20} aria-hidden /></span>
        <div>
          <h2 className="section-title">Sign in with your mobile number</h2>
          <p className="text-sm text-ink-600">We will text you a code. New here? This sets up your account too.</p>
        </div>
      </div>

      {step.kind === "phone" && (
        <form className="space-y-3" onSubmit={sendCode}>
          <label className="block">
            <span className="label">Mobile number</span>
            <input className="input" type="tel" inputMode="tel" autoComplete="tel" placeholder="98000 00000"
              value={phone} onChange={(e) => setPhone(e.target.value)} />
          </label>
          <ErrorNote error={error} />
          <button className="btn" disabled={busy || phone.replace(/\D/g, "").length < 10}>
            {busy ? "Sending…" : "Send code"}
          </button>
        </form>
      )}

      {step.kind === "code" && (
        <form className="space-y-3" onSubmit={(e) => verify(e, step.sent)}>
          <p className="text-sm text-ink-700">We sent a 6-digit code to {step.sent.phone}. It works for 10 minutes.</p>
          {step.sent.simulated_code && (
            <p className="flex gap-2 rounded-2xl bg-ink-100 px-4 py-3 text-sm text-ink-700">
              <Info size={16} className="mt-0.5 shrink-0" aria-hidden />
              <span>
                Prototype: no SMS is sent. Your code is{" "}
                <strong className="tracking-widest tabular-nums">{step.sent.simulated_code}</strong>.
              </span>
            </p>
          )}
          <label className="block">
            <span className="label">Code</span>
            <input className="input text-lg tracking-[0.4em] tabular-nums" inputMode="numeric" autoComplete="one-time-code"
              maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
          </label>
          <ErrorNote error={error} />
          <div className="flex flex-wrap gap-2">
            <button className="btn" disabled={busy || code.length !== 6}>{busy ? "Checking…" : "Sign in"}</button>
            <button type="button" className="btn-light" disabled={busy} onClick={() => sendCode()}>Send a new code</button>
            <button type="button" className="btn-light" disabled={busy} onClick={() => setStep({ kind: "phone" })}>
              Use another number
            </button>
          </div>
        </form>
      )}

      {step.kind === "profile" && <Profile sent={step.sent} onSignedIn={onSignedIn} />}
    </div>
  );
}

/** A new number: who is this account for? */
function Profile({ sent, onSignedIn }: { sent: CodeSent; onSignedIn: (me: MeView) => void }) {
  const [role, setRole] = useState<MeView["role"]>("patient");
  const [name, setName] = useState("");
  const [registry, setRegistry] = useState("");
  const [birthYear, setBirthYear] = useState("");
  const [bloodGroup, setBloodGroup] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ready = name.trim() && (role === "patient" || registry.trim());
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSignedIn(await api.auth.signup({
        challenge_id: sent.challenge_id,
        role,
        name: name.trim(),
        registry_id: role === "doctor" ? registry.trim() : null,
        birth_year: role === "patient" && birthYear ? Number(birthYear) : null,
        blood_group: role === "patient" && bloodGroup ? bloodGroup : null,
      }));
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  return (
    <form className="space-y-3" onSubmit={submit}>
      <p className="text-sm text-ink-700">{sent.phone} is confirmed. You are new here, so tell us a little about you.</p>
      <fieldset className="space-y-2">
        <legend className="label">I am signing up as</legend>
        <div className="flex flex-wrap gap-2">
          {(["patient", "doctor"] as const).map((r) => (
            <label key={r} className={`btn-light cursor-pointer ${role === r ? "ring-2 ring-lagoon-500" : ""}`}>
              <input type="radio" name="role" className="sr-only" checked={role === r} onChange={() => setRole(r)} />
              {r === "patient" ? "A patient" : "A doctor"}
            </label>
          ))}
        </div>
      </fieldset>
      <label className="block">
        <span className="label">Full name</span>
        <input className="input" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      {role === "doctor" ? (
        <label className="block">
          <span className="label">Medical registry number</span>
          <input className="input" value={registry} onChange={(e) => setRegistry(e.target.value)} />
          <span className="mt-1 block text-xs text-ink-500">Prototype: we do not check this. Patients see "not verified" next to it.</span>
        </label>
      ) : (
        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="label">Year of birth (optional)</span>
            <input className="input" inputMode="numeric" maxLength={4} value={birthYear}
              onChange={(e) => setBirthYear(e.target.value.replace(/\D/g, ""))} />
          </label>
          <label className="block">
            <span className="label">Blood group (optional)</span>
            <select className="input" value={bloodGroup} onChange={(e) => setBloodGroup(e.target.value)}>
              <option value="">Not sure</option>
              {BLOOD_GROUPS.map((g) => <option key={g} value={g}>{g}</option>)}
            </select>
          </label>
        </div>
      )}
      <ErrorNote error={error} />
      <button className="btn" disabled={busy || !ready}>{busy ? "Creating…" : "Create account"}</button>
    </form>
  );
}
