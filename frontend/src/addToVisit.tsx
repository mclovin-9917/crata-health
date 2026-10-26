import { Plus } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { api, type EntryKind } from "./api";
import { ErrorNote } from "./components";

/** What the patient can add to a visit that missed something: the kinds the server accepts
 * (`ADDABLE_KINDS` in backend/app/api/routes.py), in plain words. */
const KINDS: [EntryKind, string][] = [
  ["medication", "Medicine"],
  ["test", "Test"],
  ["note", "Advice or note"],
  ["symptom", "Symptom"],
  ["diagnosis", "Diagnosis"],
  ["allergy", "Allergy"],
  ["follow_up", "Follow-up"],
];

const HOW_OFTEN: [string, string][] = [
  ["", "Not sure or as needed"],
  ["1", "Once a day"],
  ["2", "Twice a day"],
  ["3", "Three times a day"],
  ["4", "Four times a day"],
];

/** A quiet button on a visit, opening a small form: "Add something to this visit". What is added is
 * the patient's own statement, so it is in the record at once and marked as added by them. */
export function AddToVisit({ encounterId, onAdded }: { encounterId: string; onAdded: () => void }) {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<EntryKind>("medication");
  const [label, setLabel] = useState("");
  const [dose, setDose] = useState("");
  const [often, setOften] = useState("");
  const [days, setDays] = useState("");
  const [ongoing, setOngoing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nameError, setNameError] = useState<string | null>(null);
  const [daysError, setDaysError] = useState<string | null>(null);
  const id = useId();
  const openButton = useRef<HTMLButtonElement>(null);
  const kindSelect = useRef<HTMLSelectElement>(null);
  const wasOpen = useRef(false);

  // Focus moves into the form when it opens, and back to the button when it closes.
  useEffect(() => {
    if (open) kindSelect.current?.focus();
    else if (wasOpen.current) openButton.current?.focus();
    wasOpen.current = open;
  }, [open]);

  const close = () => {
    setOpen(false);
    setLabel("");
    setDose("");
    setOften("");
    setDays("");
    setOngoing(false);
    setError(null);
    setNameError(null);
    setDaysError(null);
  };

  const save = async () => {
    setError(null);
    const name = label.trim();
    const nameProblem = name ? null : "Enter a name first.";
    const daysProblem =
      kind === "medication" && !ongoing && days && (!/^\d+$/.test(days.trim()) || Number(days) < 1 || Number(days) > 3650)
        ? "Enter a whole number of days, from 1 to 3650."
        : null;
    setNameError(nameProblem);
    setDaysError(daysProblem);
    if (nameProblem || daysProblem) return;

    const body: Record<string, unknown> = { kind, label: name };
    if (kind === "medication") {
      body.medication = {
        dose: dose.trim() || null,
        doses_per_day: often ? Number(often) : null,
        duration_days: !ongoing && days ? Number(days) : null,
        ongoing,
      };
    }
    setBusy(true);
    try {
      await api.addToVisit(encounterId, body);
      close();
      onAdded();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <div>
        <button ref={openButton} type="button" className="link-quiet" onClick={() => setOpen(true)}>
          <Plus size={16} aria-hidden /> Add something to this visit
        </button>
      </div>
    );
  }

  return (
    <section className="card space-y-3" aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`} className="section-title">Add something to this visit</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        <label htmlFor={`${id}-kind`}>
          <span className="label">What is it?</span>
          <select
            id={`${id}-kind`}
            ref={kindSelect}
            className="input min-h-11"
            value={kind}
            onChange={(e) => setKind(e.target.value as EntryKind)}
          >
            {KINDS.map(([k, name]) => (
              <option key={k} value={k}>{name}</option>
            ))}
          </select>
        </label>
        <div>
          <label htmlFor={`${id}-name`}>
            <span className="label">Name</span>
          </label>
          <input
            id={`${id}-name`}
            className="input min-h-11"
            maxLength={120}
            value={label}
            aria-invalid={nameError ? true : undefined}
            aria-describedby={nameError ? `${id}-name-error` : undefined}
            onChange={(e) => setLabel(e.target.value)}
          />
          {nameError && (
            <p id={`${id}-name-error`} className="mt-1 text-sm text-red-800">{nameError}</p>
          )}
        </div>
        {kind === "medication" && (
          <>
            <label htmlFor={`${id}-dose`}>
              <span className="label">Dose (optional)</span>
              <input
                id={`${id}-dose`}
                className="input min-h-11"
                maxLength={60}
                placeholder="500 mg"
                value={dose}
                onChange={(e) => setDose(e.target.value)}
              />
            </label>
            <label htmlFor={`${id}-often`}>
              <span className="label">How often (optional)</span>
              <select
                id={`${id}-often`}
                className="input min-h-11"
                value={often}
                onChange={(e) => setOften(e.target.value)}
              >
                {HOW_OFTEN.map(([v, name]) => (
                  <option key={v} value={v}>{name}</option>
                ))}
              </select>
            </label>
            <div>
              <label htmlFor={`${id}-days`}>
                <span className="label">For how many days (optional)</span>
              </label>
              <input
                id={`${id}-days`}
                className="input min-h-11"
                type="number"
                inputMode="numeric"
                min={1}
                max={3650}
                step={1}
                disabled={ongoing}
                value={days}
                aria-invalid={daysError ? true : undefined}
                aria-describedby={daysError ? `${id}-days-error` : undefined}
                onChange={(e) => setDays(e.target.value)}
              />
              {daysError && (
                <p id={`${id}-days-error`} className="mt-1 text-sm text-red-800">{daysError}</p>
              )}
            </div>
            <label className="flex min-h-11 items-center gap-2 self-end text-sm" htmlFor={`${id}-ongoing`}>
              <input
                id={`${id}-ongoing`}
                type="checkbox"
                className="size-5"
                checked={ongoing}
                onChange={(e) => {
                  setOngoing(e.target.checked);
                  setDaysError(null);
                }}
              />
              Long-term medicine
            </label>
          </>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        <button type="button" className="btn min-h-11" disabled={busy} onClick={save}>
          {busy ? "Adding…" : "Add to visit"}
        </button>
        <button type="button" className="btn-light min-h-11" disabled={busy} onClick={close}>Cancel</button>
      </div>
      <p className="text-sm text-ink-600">
        This shows as added by you.
        {(kind === "allergy" || kind === "medication") && " Allergies and current medicines you add go on your emergency card."}
      </p>
      <ErrorNote error={error} />
    </section>
  );
}
