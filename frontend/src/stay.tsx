// Emergency visits and hospital stays the patient logs themselves: the form on Add a visit, and
// the card on the stay's own page. Only the patient logs or closes a stay.
import { Building2, Check, FileUp, Pencil, Sparkles } from "lucide-react";
import { useState } from "react";
import { api, type Encounter, type HospitalStay } from "./api";
import { ErrorNote } from "./components";
import { formatDate, go } from "./lib";

export const STAY_KIND: Record<HospitalStay["kind"], string> = {
  emergency_visit: "Emergency visit",
  admission: "Hospital admission",
};

/** Today in the patient's own time zone, as YYYY-MM-DD (for date inputs). */
export const localToday = () => new Date().toLocaleDateString("en-CA");

/** "14 to 16 Oct 2026" (or "14 Oct 2026 to 16 Nov 2026" across months), or "since 14 Oct 2026" while open. */
export function stayDates(s: { started_on: string; ended_on: string | null }): string {
  if (!s.ended_on) return `since ${formatDate(s.started_on)}`;
  const start = s.started_on.slice(0, 10);
  const end = s.ended_on.slice(0, 10);
  if (start === end) return formatDate(start);
  if (start.slice(0, 7) === end.slice(0, 7)) return `${Number(start.slice(8, 10))} to ${formatDate(end)}`;
  return `${formatDate(start)} to ${formatDate(end)}`;
}

/** Add a visit, "An emergency or hospital stay": log it now, during it or after. */
export function LogStay({ patientId }: { patientId: string }) {
  const today = localToday();
  const [kind, setKind] = useState<HospitalStay["kind"]>("admission");
  const [place, setPlace] = useState("");
  const [reason, setReason] = useState("");
  const [started, setStarted] = useState(today);
  const [stillThere, setStillThere] = useState(true);
  const [ended, setEnded] = useState(today);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const enc = await api.logStay(patientId, {
        kind, place: place.trim() || null, reason: reason.trim() || null, started_on: started,
        ended_on: stillThere ? null : ended,
      });
      go(`/encounters/${enc.id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="card space-y-4">
      <p className="text-sm text-ink-600">
        Add this stay now, even if you are still there. A doctor who opens your emergency card will see it. Once you
        are home, add the discharge summary and other documents.
      </p>
      <fieldset className="space-y-2">
        <legend className="label">What was it?</legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {(["emergency_visit", "admission"] as const).map((k) => (
            <label key={k} className={`btn-light cursor-pointer justify-start ${kind === k ? "ring-2 ring-lagoon-500" : ""}`}>
              <input type="radio" name="stay-kind" className="sr-only" checked={kind === k} onChange={() => setKind(k)} />
              {k === "emergency_visit" ? "Emergency visit (went home the same day)" : "Admitted to hospital"}
            </label>
          ))}
        </div>
      </fieldset>
      <label className="block" htmlFor="stay-place">
        <span className="label">Hospital or clinic (optional)</span>
        <input id="stay-place" className="input" value={place} maxLength={120} onChange={(e) => setPlace(e.target.value)} />
      </label>
      <label className="block" htmlFor="stay-reason">
        <span className="label">What happened (optional)</span>
        <input id="stay-reason" className="input" value={reason} maxLength={200} placeholder="For example: chest pain, a fall, high fever"
          onChange={(e) => setReason(e.target.value)} />
      </label>
      <label className="flex items-center gap-2 text-sm" htmlFor="stay-still">
        <input id="stay-still" type="checkbox" className="size-4" checked={stillThere} onChange={(e) => setStillThere(e.target.checked)} />
        I am still there
      </label>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block" htmlFor="stay-started">
          <span className="label">Started on</span>
          <input id="stay-started" type="date" className="input" value={started} max={today} onChange={(e) => setStarted(e.target.value)} />
        </label>
        {!stillThere && (
          <label className="block" htmlFor="stay-ended">
            <span className="label">I went home on</span>
            <input id="stay-ended" type="date" className="input" value={ended} min={started} max={today}
              onChange={(e) => setEnded(e.target.value)} />
          </label>
        )}
      </div>
      <ErrorNote error={error} />
      <button className="btn" disabled={busy || !started} onClick={submit}>{busy ? "Saving…" : "Save stay"}</button>
    </div>
  );
}

/** The top of a stay's page: what, where, when, with "I went home" and an edit form. */
export function StayCard({ enc, onChange }: { enc: Encounter; onChange: (enc: Encounter) => void }) {
  const stay = enc.stay!;
  const today = localToday();
  const [closing, setClosing] = useState(false);
  const [editing, setEditing] = useState(false);
  const [ended, setEnded] = useState(stay.suggested_end ?? today);
  const [place, setPlace] = useState(stay.place ?? "");
  const [reason, setReason] = useState(stay.reason ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async (body: Record<string, unknown>) => {
    setBusy(true);
    setError(null);
    try {
      onChange(await api.updateStay(enc.id, body));
      setClosing(false);
      setEditing(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card space-y-4">
      <div className="flex items-start gap-3">
        <span className="tile"><Building2 size={20} aria-hidden /></span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-ink-500">{STAY_KIND[stay.kind]}</p>
          <p className="text-xl font-bold tracking-tight">{stay.reason ?? STAY_KIND[stay.kind]}</p>
          <p className="text-ink-600">{[stay.place, stayDates(stay)].filter(Boolean).join(", ")}</p>
        </div>
        {!stay.ended_on && <span className="chip bg-lagoon-100 text-lagoon-800">Still there</span>}
      </div>
      <p className="text-sm text-ink-500">
        Added by you, so it is marked "from memory, no document".
        {!stay.ended_on && " Your emergency card lists you as in hospital until you add the day you went home."}
      </p>

      {!stay.ended_on && stay.suggested_end && !editing && !closing && (
        <div className="space-y-2 rounded-2xl border-l-4 border-l-tide-500 bg-white/70 p-4">
          <span className="chip bg-tide-50 text-tide-800"><Sparkles size={12} aria-hidden /> Suggested</span>
          <p className="font-semibold">
            Your documents say you went home on {formatDate(stay.suggested_end)}.
          </p>
          {stay.suggested_end_quote && (
            <p className="text-sm text-ink-600">From a document: <q className="italic">{stay.suggested_end_quote}</q></p>
          )}
          <button className="btn" disabled={busy} onClick={() => save({ ended_on: stay.suggested_end })}>
            <Check size={16} aria-hidden /> Use this date
          </button>
        </div>
      )}

      {editing ? (
        <div className="grid gap-3 rounded-2xl bg-ink-50/80 p-4 ring-1 ring-ink-100 sm:grid-cols-2">
          <label className="block" htmlFor="edit-stay-place">
            <span className="label">Hospital or clinic</span>
            <input id="edit-stay-place" className="input" value={place} maxLength={120} onChange={(e) => setPlace(e.target.value)} />
          </label>
          <label className="block" htmlFor="edit-stay-reason">
            <span className="label">What happened</span>
            <input id="edit-stay-reason" className="input" value={reason} maxLength={200} onChange={(e) => setReason(e.target.value)} />
          </label>
          <div className="flex gap-2 sm:col-span-2">
            <button className="btn" disabled={busy} onClick={() => save({ place: place.trim() || null, reason: reason.trim() || null })}>
              Save changes
            </button>
            <button className="btn-light" disabled={busy} onClick={() => setEditing(false)}>Cancel</button>
          </div>
        </div>
      ) : closing ? (
        <div className="flex flex-wrap items-end gap-2 rounded-2xl bg-ink-50/80 p-4 ring-1 ring-ink-100">
          <label className="block" htmlFor="stay-home">
            <span className="label">I went home on</span>
            <input id="stay-home" type="date" className="input w-auto" value={ended} min={stay.started_on} max={today}
              onChange={(e) => setEnded(e.target.value)} />
          </label>
          <button className="btn" disabled={busy || !ended} onClick={() => save({ ended_on: ended })}>
            <Check size={16} aria-hidden /> Save
          </button>
          <button className="btn-light" disabled={busy} onClick={() => setClosing(false)}>Cancel</button>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          {!stay.ended_on && <button className="btn" onClick={() => setClosing(true)}>I went home</button>}
          <button className="btn-light" onClick={() => go(`/add/${enc.id}`)}>
            <FileUp size={16} aria-hidden /> Add documents
          </button>
          <button className="btn-light" onClick={() => setEditing(true)}><Pencil size={16} aria-hidden /> Edit</button>
          {stay.ended_on && (
            <button className="btn-light" disabled={busy} onClick={() => save({ ended_on: null })}>I am still there</button>
          )}
        </div>
      )}
      <ErrorNote error={error} />
    </div>
  );
}
