import {
  Activity, AlertTriangle, Building2, CalendarClock, ChevronDown, Droplet, FileText, FlaskConical, Gauge, Hospital, Lock, MoreHorizontal, Pill, Pin, ShieldAlert, Siren,
  Stethoscope, Syringe, Thermometer, User, type LucideIcon,
} from "lucide-react";
import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import type {
  EmergencySummary, Encounter, Entry, EntryCheck, EntryKind, FlagReason, MedicationState, Patient, SourceItem, Speaker, TestResult,
} from "./api";
import { age, dosePattern, doseWords, formatDate, kindLabel, locatorStart, sexWord } from "./lib";
import { visitMedia } from "./media";
import { MedicineName } from "./medicine";

/** The fixed disclosure labels: "How this works" for a screen's
 * explanation, "More info" for explanatory text, "Show details" for a fact's extras,
 * and "See where this came from" for proof only. */
export type DisclosureLabel = "How this works" | "More info" | "Show details" | "See where this came from";

/** "text": the labelled row. "icon": only a chevron (44 px square) for the end of a fact's row; the
 * label stays as its accessible name. */
export type DisclosureVariant = "text" | "icon";

/** One show/hide row for the whole app: a button at least 44 px tall with a chevron that turns when
 * open. The content is not rendered while closed. `count` adds a quiet "(3)" after the label. */
export function Disclosure({ label, name, count, defaultOpen = false, variant = "text", className = "", children }: {
  label: DisclosureLabel;
  name?: string; // what it is about; screen readers hear "Show details for <name>"
  count?: number;
  defaultOpen?: boolean;
  variant?: DisclosureVariant;
  className?: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <div className={className}>
      <DisclosureButton label={label} name={name} count={count} variant={variant} open={open} controls={id} onToggle={() => setOpen(!open)} />
      <div id={id} hidden={!open}>
        {open && children}
      </div>
    </div>
  );
}

/** The trigger of a Disclosure, on its own, for a row that holds two of them side by side (the
 * caller shows the content under the row). Looks exactly like Disclosure's. */
export function DisclosureButton({ label, name, count, variant = "text", open, controls, onToggle }: {
  label: DisclosureLabel;
  name?: string;
  count?: number;
  variant?: DisclosureVariant;
  open: boolean;
  controls?: string;
  onToggle: () => void;
}) {
  const proof = label === "See where this came from";
  const accessible = name ? `${label} for ${name}` : undefined;
  if (variant === "icon") {
    return (
      <button
        type="button"
        className="inline-flex size-11 shrink-0 items-center justify-center rounded-lg text-ink-500 hover:bg-ink-100 hover:text-ink-700"
        aria-label={accessible ?? label}
        aria-expanded={open}
        aria-controls={controls}
        onClick={onToggle}
      >
        <ChevronDown size={18} className={`transition ${open ? "rotate-180" : ""}`} aria-hidden />
      </button>
    );
  }
  return (
    <button
      type="button"
      className={`inline-flex min-h-11 items-center gap-1.5 rounded-lg text-left text-sm font-semibold hover:underline ${
        proof ? "text-tide-700" : "text-ink-700"
      }`}
      aria-label={accessible}
      aria-expanded={open}
      aria-controls={controls}
      onClick={onToggle}
    >
      {label}
      {count !== undefined && <span className="font-normal text-ink-500">({count})</span>}
      <ChevronDown size={16} className={`shrink-0 transition ${open ? "rotate-180" : ""}`} aria-hidden />
    </button>
  );
}

/** Tide is reserved for AI suggestions, so the doctor's chip is warm sand, not blue. */
const SAID_BY: Record<Speaker, { text: string; tone: string; Icon: LucideIcon }> = {
  doctor: { text: "Doctor said", tone: "bg-ink-100 text-ink-800", Icon: Stethoscope },
  patient: { text: "You said", tone: "bg-lagoon-50 text-lagoon-800", Icon: User },
  document: { text: "From a document", tone: "bg-white ring-1 ring-ink-200 text-ink-700", Icon: FileText },
};

/** `added`: the patient added it to a visit themselves (says "You added" instead of "You said"). */
export function SaidBy({ speaker, added }: { speaker: Speaker | null; added?: boolean }) {
  if (!speaker) return null;
  const { text, tone, Icon } = SAID_BY[speaker];
  return <span className={`chip ${tone}`}><Icon size={12} aria-hidden /> {added && speaker === "patient" ? "Added by you" : text}</span>;
}

/** Something the patient added to a visit by hand (the server marks its source "manual"). */
export const addedToVisit = (e: Entry) =>
  e.encounter_id !== null && e.provenance.some((p) => p.source_type === "manual");

const KIND_ICONS: Record<EntryKind, LucideIcon> = {
  medication: Pill,
  diagnosis: Stethoscope,
  symptom: Thermometer,
  test: FlaskConical,
  result: Gauge,
  allergy: ShieldAlert,
  follow_up: CalendarClock,
  note: FileText,
  emergency_visit: Hospital,
  admission: Hospital,
  procedure: Activity,
  surgery: Activity,
  implant: Syringe,
};

/** "paracetamol", or "amoxicillin + clavulanic acid", when the label doesn't already say it. */
export function genericLine(e: Entry): string | null {
  const g = e.medication?.generic_names ?? [];
  if (!g.length) return null;
  const label = e.label.toLowerCase();
  return g.every((n) => label.includes(n)) ? null : g.join(" + ");
}

/** The gradient icon tile in front of an entry. Allergies get the red tile: red means danger. */
export function KindIcon({ kind, small }: { kind: EntryKind; small?: boolean }) {
  const Icon = KIND_ICONS[kind] ?? FileText;
  const red = kind === "allergy" ? "[background:var(--grad-emergency)]" : "";
  return (
    <span className={`tile ${small ? "size-8 rounded-lg" : ""} ${red}`}>
      <Icon size={small ? 16 : 20} aria-hidden />
    </span>
  );
}

/** The details we have ("35 years, female, blood group B+"); what is
 * unknown is left out. `tone="hero"` is for the white-on-gradient hero. */
export function PatientBasics({ patient, tone = "page" }: { patient: Patient; tone?: "hero" | "page" }) {
  const bits = [
    patient.birth_year ? `${new Date().getFullYear() - patient.birth_year} years` : null,
    sexWord(patient.sex) || null,
    patient.blood_group ? `blood group ${patient.blood_group}` : null,
  ].filter(Boolean);
  if (!bits.length) return null;
  return (
    <div className="mt-3">
      <p className={`text-sm ${tone === "hero" ? "font-medium text-white" : "text-ink-700"}`}>{bits.join(", ").replace(/^./, (c) => c.toUpperCase())}</p>
    </div>
  );
}

export function ErrorNote({ error }: { error: string | null }) {
  return error ? <p role="alert" className="my-2 rounded-xl bg-red-50 px-3 py-2 text-sm text-red-800 ring-1 ring-red-200">{error}</p> : null;
}

/** Lines that share a title ("Given in hospital: Inj Ceftriaxone", "Given in hospital: Inj
 * Pantoprazole") are shown once under that title, so the title is not read over and over. Only a
 * title shared by two or more lines is pulled out; a line on its own keeps its whole label. */
export type Grouped<T> = { title: string | null; items: { item: T; text: string }[] };

export function groupByPrefix<T>(items: T[], labelOf: (t: T) => string): Grouped<T>[] {
  const split = (label: string) => {
    const i = label.indexOf(": ");
    const rest = label.slice(i + 2).trim();
    return i >= 3 && i <= 40 && rest ? { title: label.slice(0, i).trim(), rest } : null;
  };
  const count = new Map<string, number>();
  for (const t of items) {
    const p = split(labelOf(t));
    if (p) count.set(p.title.toLowerCase(), (count.get(p.title.toLowerCase()) ?? 0) + 1);
  }
  const out: Grouped<T>[] = [];
  const at = new Map<string, Grouped<T>>();
  for (const t of items) {
    const p = split(labelOf(t));
    if (p && (count.get(p.title.toLowerCase()) ?? 0) > 1) {
      let g = at.get(p.title.toLowerCase());
      if (!g) {
        g = { title: p.title, items: [] };
        at.set(p.title.toLowerCase(), g);
        out.push(g);
      }
      g.items.push({ item: t, text: p.rest });
    } else {
      out.push({ title: null, items: [{ item: t, text: labelOf(t) }] });
    }
  }
  return out;
}

/** A line of context on Review. Amber means "check this" (red stays for emergencies and allergies). */
export function Note({ children, icon, tone, role }: { children: ReactNode; icon: ReactNode; tone?: "amber"; role?: "status" }) {
  const colors = tone === "amber" ? "bg-amber-100 text-amber-900" : "bg-white/60 text-ink-700 ring-1 ring-ink-100";
  return <p role={role} className={`flex gap-2 rounded-2xl px-4 py-3 text-sm ${colors}`}><span className="mt-0.5 shrink-0">{icon}</span><span>{children}</span></p>;
}

// Cautious wording: what to check, never whether to take a medicine.
export const CHECK_TEXT: Record<EntryCheck, string> = {
  medicine_unnamed:
    "No medicine name. Only its purpose was said or written. Check your prescription or ask your doctor for the name, then edit this.",
  name_not_in_quote: "Check this one. The name is not clearly in the words it came from.",
  not_english: "Some details are not in English yet. Edit them if they look wrong.",
};

const FLAG_TEXT: Record<FlagReason, string | null> = {
  check: null, // the entry's own check notes say it
  clash: null, // one short line that points at the check above (see `clashLine`)
  unsure: "Check this one. The wording was unclear.",
};

/** The one short line on a row that is named by a check at the top of Review: the check says it
 * in full once, the row only points at it. `urgent` is the red allergy clash. */
export type ClashLine = { text: string; urgent: boolean };

export function clashLine(enc: Encounter, entryId: string): ClashLine | null {
  const found = enc.discrepancies.filter((d) => d.entry_ids.includes(entryId));
  if (!found.length) return null; // no check above names this row: FlagNotes shows its own notes
  if (found.some((d) => d.severity === "urgent")) return { text: "Part of the allergy clash above", urgent: true };
  if (found.some((d) => d.check === "allergy_clash")) return { text: "Part of the allergy check above", urgent: false };
  return { text: "Part of a check above", urgent: false };
}

/** The notes under a row that needs a look: its check notes, then why it is flagged. A row named by
 * a check at the top gets one short line instead of repeating it (red for an allergy clash, amber
 * for the other checks). On a fact read as it is they stay until the patient edits it. */
export function FlagNotes({ entry, flag, clash }: { entry: Entry; flag?: FlagReason; clash?: ClashLine | null }) {
  const notes = [...entry.checks.map((c) => CHECK_TEXT[c]), flag ? FLAG_TEXT[flag] : null].filter(Boolean) as string[];
  const line = flag === "clash" ? clash ?? null : null;
  // A clash flag with no check above to point at still has to say something.
  if (flag === "clash" && !line && !notes.length) notes.push("Check this one.");
  return (
    <>
      {notes.map((text) => (
        <Note key={text} tone="amber" icon={<AlertTriangle size={16} aria-hidden />}>{text}</Note>
      ))}
      {line &&
        (line.urgent ? (
          <p className="flex items-center gap-2 text-sm font-semibold text-red-800">
            <ShieldAlert size={16} className="shrink-0" aria-hidden /> {line.text}
          </p>
        ) : (
          <p className="flex items-center gap-2 text-sm font-semibold text-amber-900">
            <AlertTriangle size={16} className="shrink-0" aria-hidden /> {line.text}
          </p>
        ))}
    </>
  );
}

/** Correct a fact: what it is, and a medicine's dose, days and whether it is long-term. The
 * original words stay attached as proof, and saving does not confirm it. */
export function EditForm({ entry, busy, onSave, onCancel }: {
  entry: Entry;
  busy: boolean;
  onSave: (body: Record<string, unknown>) => void;
  onCancel: () => void;
}) {
  const inRecord = entry.status === "confirmed"; // read as it is: nothing left to confirm
  const m = entry.medication;
  const [label, setLabel] = useState(entry.label);
  const [dose, setDose] = useState(m?.dose ?? "");
  const [days, setDays] = useState(m?.duration_days?.toString() ?? "");
  const [ongoing, setOngoing] = useState(m?.ongoing ?? false);
  const id = `edit-${entry.id}`;

  const save = () => {
    const body: Record<string, unknown> = { label };
    if (entry.kind === "medication") {
      body.medication = { dose: dose || null, duration_days: days ? Number(days) : null, ongoing };
    }
    onSave(body);
  };

  return (
    <div className="grid gap-3 rounded-2xl bg-ink-50/80 p-4 ring-1 ring-ink-100 sm:grid-cols-2">
      <label className="sm:col-span-2" htmlFor={`${id}-label`}>
        <span className="label">Name</span>
        <input id={`${id}-label`} className="input" value={label} onChange={(e) => setLabel(e.target.value)} />
      </label>
      {entry.kind === "medication" && (
        <>
          <label htmlFor={`${id}-dose`}>
            <span className="label">Dose</span>
            <input id={`${id}-dose`} className="input" value={dose} onChange={(e) => setDose(e.target.value)} />
          </label>
          <label htmlFor={`${id}-days`}>
            <span className="label">For how many days</span>
            <input id={`${id}-days`} className="input" type="number" min={1} value={days} onChange={(e) => setDays(e.target.value)} />
          </label>
          <label className="flex items-center gap-2 text-sm sm:col-span-2" htmlFor={`${id}-ongoing`}>
            <input id={`${id}-ongoing`} type="checkbox" checked={ongoing} onChange={(e) => setOngoing(e.target.checked)} />
            Long-term medicine
          </label>
        </>
      )}
      <div className="flex gap-2 sm:col-span-2">
        <button className="btn" disabled={busy} onClick={save}>Save changes</button>
        <button className="btn-light" disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
      <p className="text-xs text-ink-500 sm:col-span-2">
        The words it came from stay saved.
        {inRecord ? "" : " Saving does not confirm it, so confirm it afterwards."}
      </p>
    </div>
  );
}

const HIDDEN_FIELDS = new Set([
  "dose", "doses_per_day", "duration_value", "duration_unit", "duration", "ongoing", "as_needed",
]);

/** Plain words for field names shown raw ("frequency: 1-0-1" reads "how often: 1-0-1 (morning and night)"). */
const FIELD_WORDS: Record<string, string> = { frequency: "how often" };

/** "13.2 g/dL", "Nil", "<0.5 mg/L": the value as printed on the report. */
export function valueText(r: TestResult): string {
  const v = r.value !== null ? String(r.value) : r.value_text ?? "";
  return [v, r.value !== null ? r.unit : null].filter(Boolean).join(" ");
}

/** The report's own normal range: "13 to 17", "up to 200", or the words it printed. */
export function rangeText(r: TestResult): string | null {
  if (r.ref_low !== null && r.ref_high !== null) return `${r.ref_low} to ${r.ref_high}`;
  if (r.ref_high !== null) return `up to ${r.ref_high}`;
  if (r.ref_low !== null) return `from ${r.ref_low}`;
  return r.ref_text;
}

const capFirst = (s: string) => s.replace(/^./, (c) => c.toUpperCase());

/** One field of an entry in words: "how often: 1-0-1 (morning and night)", a date as a date,
 * anything else as "key: value". `bare`: an `instructions` value alone ("After food"), for the
 * medicine lines of `dose="column"`; elsewhere it keeps its label ("instructions: after food"). */
function fieldBit(k: string, v: string, bare = false): string {
  if (bare && k === "instructions") return capFirst(v);
  const shown = k === "frequency" ? dosePattern(v) : isDay(v) ? formatDate(v) : v;
  return `${FIELD_WORDS[k] ?? k}: ${shown}`;
}

/** The key facts: "500 mg · 1 per day · 3 days · rash" on one line. `dose="column"` is for a
 * medicine whose frequency sits in a `DoseColumn` beside it: the frequency is left out and the
 * rest goes on two short lines (the dose and instructions, then the course). */
export function EntryDetails({ entry, dose = "inline" }: { entry: Entry; dose?: "column" | "inline" }) {
  const m = entry.medication;
  const r = entry.result;
  // A dose the name already says ("625 mg" in "Augmentin 625 mg", or the name itself) is not repeated.
  const doseText = m?.dose && !entry.label.toLowerCase().includes(m.dose.trim().toLowerCase()) ? m.dose : null;
  if (dose === "column" && entry.kind === "medication") {
    const what: string[] = [];
    if (doseText) what.push(dosePattern(doseText));
    // The column shows a pattern or its words, or the raw text; then the count would be lost.
    const freq = entry.fields.frequency?.trim();
    if (freq && !dashParts(freq) && !doseWords(freq) && m?.doses_per_day) what.push(`${m.doses_per_day} per day`);
    for (const [k, v] of Object.entries(entry.fields)) {
      if (HIDDEN_FIELDS.has(k) || k === "frequency") continue; // the column shows the frequency
      what.push(fieldBit(k, v, true));
    }
    if (entry.occurred_on) what.push(isDay(entry.occurred_on) ? formatDate(entry.occurred_on) : entry.occurred_on);
    if (entry.unverified) what.push("from memory, no document");
    const course: string[] = [];
    const line = m && courseLine(m);
    if (line) course.push(line);
    if (m?.stopped_on) course.push(`stopped ${formatDate(m.stopped_on)}`);
    const lines = [what.join(" · "), course.join(" · ")].filter(Boolean);
    return lines.length ? <div className="text-sm text-ink-600">{lines.map((l) => <p key={l}>{l}</p>)}</div> : null;
  }
  const bits: string[] = [];
  if (r) {
    bits.push(valueText(r));
    const range = rangeText(r);
    if (range) bits.push(`printed range ${range}`);
    if (r.measured_on) bits.push(formatDate(r.measured_on));
  }
  if (doseText) bits.push(dosePattern(doseText));
  if (m?.doses_per_day) bits.push(`${m.doses_per_day} per day`);
  const course = m && courseLine(m);
  if (course) bits.push(course);
  if (m?.stopped_on) bits.push(`stopped ${formatDate(m.stopped_on)}`);
  for (const [k, v] of Object.entries(entry.fields)) {
    if (HIDDEN_FIELDS.has(k)) continue; // older entries repeat structured values
    bits.push(fieldBit(k, v));
  }
  if (entry.occurred_on) bits.push(isDay(entry.occurred_on) ? formatDate(entry.occurred_on) : entry.occurred_on);
  if (entry.unverified) bits.push("from memory, no document");
  return bits.length ? <p className="text-sm text-ink-600">{bits.join(" · ")}</p> : null;
}

const isDay = (v: string) => /^\d{4}-\d{2}-\d{2}$/.test(v);

/** "1-0-1", "1 - 0 - ½" or "1–0–1" (en or em dashes too): the parts of a 3 or 4 part pattern, else null. */
function dashParts(text: string): string[] | null {
  const parts = text.trim().split(/\s*[-–—]\s*/);
  if (parts.length < 3 || parts.length > 4) return null;
  return parts.every((p) => /^(\d+(\.\d+)?|\d*[½¼¾]|\d+\/\d+)$/.test(p)) ? parts : null;
}

const PART_NAMES: Record<number, string[]> = {
  3: ["Morn", "Noon", "Night"],
  4: ["Morn", "Noon", "Eve", "Night"],
};
const PART_WORDS: Record<number, string[]> = {
  3: ["morning", "noon", "night"],
  4: ["morning", "noon", "evening", "night"],
};

/** "Morning 1, noon 0, night 1 (morning and night)": the pattern in words for a screen reader. */
function patternLabel(parts: string[]): string {
  const said = PART_WORDS[parts.length].map((w, i) => `${i === 0 ? capFirst(w) : w} ${parts[i]}`).join(", ");
  const words = doseWords(parts.join("-"));
  return words ? `${said} (${words})` : said;
}

/** A medicine's frequency in its own fixed-width cell (w-28), so down a list of medicines every frequency
 * sits in one aligned column: a small morning / noon / night table for a pattern like 1-0-1, a
 * chip with words for "SOS" or "BD", else what was written. Empty (but still there) when unknown. */
export function DoseColumn({ entry }: { entry: Entry }) {
  if (entry.kind !== "medication") return null;
  const raw = entry.fields.frequency?.trim() ?? "";
  const parts = raw ? dashParts(raw) : null;
  const words = raw ? doseWords(raw) : null;
  const perDay = entry.medication?.doses_per_day;
  let body: ReactNode = null;
  if (parts) {
    body = (
      <div
        role="img"
        aria-label={patternLabel(parts)}
        className={`grid w-full gap-x-1.5 text-center ${parts.length === 4 ? "grid-cols-4" : "grid-cols-3"}`}
      >
        {PART_NAMES[parts.length].map((n) => (
          <span key={n} className="text-[9px] font-semibold tracking-wide text-ink-500 uppercase" aria-hidden>{n}</span>
        ))}
        {parts.map((p, i) => (
          <span key={i} className={`text-base font-bold tabular-nums ${/^0+(\.0+)?$/.test(p) ? "text-ink-300" : "text-ink-900"}`} aria-hidden>{p}</span>
        ))}
      </div>
    );
  } else if (words) {
    body = <span className="chip bg-tide-50 text-tide-800 text-center whitespace-normal">{capFirst(words)}</span>;
  } else if (raw) {
    body = <span className="chip bg-ink-100 text-ink-700 text-center whitespace-normal">{raw}</span>;
  } else if (perDay) {
    body = <span className="chip bg-ink-100 text-ink-700 text-center whitespace-normal">{perDay} a day</span>;
  }
  return <div className="flex w-28 shrink-0 justify-center pt-1">{body}</div>;
}

/** The "more actions" button of a row (a 44 px "..."), with a small menu under it: Edit, Remove and
 * the like in one place instead of a button under every fact. Escape or a click outside closes it,
 * the arrow keys move between the items, and choosing one closes it. */
export function ActionsMenu({ name, items }: {
  name: string; // what the row is about; screen readers hear "More actions for <name>"
  items: { label: string; icon?: ReactNode; onSelect: () => void; disabled?: boolean }[];
}) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const buttons = () => Array.from(menu.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)') ?? []);

  useEffect(() => {
    if (!open) return;
    buttons()[0]?.focus();
    const away = (ev: PointerEvent) => {
      if (!wrap.current?.contains(ev.target as Node)) setOpen(false);
    };
    // pointerdown, not mousedown: iOS Safari sends no mouse events for a tap on a non-clickable element.
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [open]);

  if (!items.length) return null;

  const onKeyDown = (ev: KeyboardEvent) => {
    if (ev.key === "Escape" && open) {
      ev.stopPropagation();
      setOpen(false);
      trigger.current?.focus();
    } else if (open && (ev.key === "ArrowDown" || ev.key === "ArrowUp")) {
      ev.preventDefault();
      const all = buttons();
      if (!all.length) return;
      const at = all.indexOf(document.activeElement as HTMLButtonElement);
      const next = ev.key === "ArrowDown" ? (at + 1) % all.length : (at - 1 + all.length) % all.length;
      all[next].focus();
    } else if (open && ev.key === "Tab") {
      setOpen(false);
    }
  };

  return (
    <div ref={wrap} className="relative shrink-0" onKeyDown={onKeyDown}>
      <button
        ref={trigger}
        type="button"
        className="inline-flex size-11 shrink-0 items-center justify-center rounded-lg text-ink-500 hover:bg-ink-100 hover:text-ink-700"
        aria-label={`More actions for ${name}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen(!open)}
      >
        <MoreHorizontal size={18} aria-hidden />
      </button>
      {open && (
        <div
          ref={menu}
          id={menuId}
          role="menu"
          aria-label={`More actions for ${name}`}
          className="absolute top-full right-0 z-30 mt-1 min-w-40 rounded-2xl bg-white p-1 shadow-lg ring-1 ring-ink-200"
        >
          {items.map((it) => (
            <button
              key={it.label}
              type="button"
              role="menuitem"
              disabled={it.disabled}
              className="flex min-h-11 w-full items-center gap-2 rounded-xl px-3 text-left text-sm font-semibold text-ink-800 hover:bg-ink-50 disabled:opacity-40"
              onClick={() => {
                setOpen(false);
                trigger.current?.focus();
                it.onSelect();
              }}
            >
              {it.icon}
              {it.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// The trusted sites' short names, as in backend/app/medicine_info.py.
const SITE_NAMES: Record<string, string> = {
  "medlineplus.gov": "MedlinePlus", "nhs.uk": "NHS", "drugs.com": "Drugs.com",
  "mayoclinic.org": "Mayo Clinic", "1mg.com": "1mg",
};

export function siteName(url: string): string {
  const host = new URL(url).hostname.replace(/^www\./, "");
  return SITE_NAMES[host] ?? host;
}

/** Where a medicine's active ingredients came from, after "Contains ...": nothing for the
 * curated table, "(from 1mg)" with a link for a trusted site, "(matched by AI)" otherwise. */
export function MatchedFrom({ medication }: { medication: Entry["medication"] }) {
  if (medication?.generic_from === "search" && medication.generic_source) {
    return (
      <span className="font-normal text-ink-500">
        {" "}(from{" "}
        <a className="underline" href={medication.generic_source} target="_blank" rel="noreferrer">
          {siteName(medication.generic_source)}
        </a>)
      </span>
    );
  }
  if (medication?.generic_from === "model") return <span className="font-normal text-ink-500"> (matched by AI)</span>;
  return null;
}

/** When a medicine is taken: "3 days, 13 Oct 2026 to 15 Oct 2026", "from 13 Oct 2026", or
 * "long-term" (its stored start is only the day it entered the record, so it is not shown).
 * The end day counts the start day as day 1, as on the server. */
function courseLine(m: NonNullable<Entry["medication"]>): string {
  const days = m.duration_days ? `${m.duration_days} days` : "";
  if (m.ongoing) return "long-term";
  if (!m.started_on) return days;
  if (!m.duration_days) return `from ${formatDate(m.started_on)}`;
  const end = new Date(`${m.started_on}T00:00:00`);
  end.setDate(end.getDate() + m.duration_days - 1);
  return `${days}, ${formatDate(m.started_on)} to ${formatDate(end.toLocaleDateString("en-CA"))}`;
}

// Facts grouped by kind, in reading order: Home's latest visit and the Review screen.
// In the order a consult goes (decided 2026-10-19): what you said, allergies, what the doctor
// found, tests, medicines, then advice (follow-up included). Events only come from stays.
export const KIND_GROUPS: [string, EntryKind[]][] = [
  ["Symptoms you reported", ["symptom"]],
  ["Allergies", ["allergy"]],
  ["Diagnoses", ["diagnosis"]],
  ["Tests", ["test"]],
  ["Medicines", ["medication"]],
  ["Advice and notes", ["note", "follow_up"]],
  ["Events", ["emergency_visit", "admission", "procedure", "surgery", "implant"]],
];

function briefMedicine(e: Entry): ReactNode {
  const m = e.medication;
  const when = m?.stopped_on ? "stopped" : m?.duration_days ? `${m.duration_days} days` : m?.ongoing ? "long-term" : "";
  return <><MedicineName entry={e} className="font-semibold" />{when ? ` (${when})` : ""}</>;
}

// A visit in a few lines, grouped by what kind of fact it is. Built by code from confirmed
// entries only (decided 2026-10-12): it never says anything the patient has not confirmed.
export function VisitSummary({ entries }: { entries: Entry[] }) {
  const confirmed = entries.filter((e) => e.status === "confirmed");
  const groups = KIND_GROUPS.map(([title, kinds]) => {
    const items = confirmed.filter((e) => kinds.includes(e.kind));
    return { title, kind: kinds[0], items };
  }).filter((g) => g.items.length);
  if (!groups.length) return null;
  return (
    <ul className="space-y-3">
      {groups.map((g) => (
        <li key={g.title} className="flex gap-3">
          <KindIcon kind={g.kind} small />
          <div className="min-w-0">
            <p className="text-xs font-semibold tracking-wide text-ink-500 uppercase">{g.title}</p>
            <p className={`font-semibold ${g.kind === "allergy" ? "text-red-800" : ""}`}>
              {g.kind === "medication"
                ? g.items.map((e, i) => <span key={e.id}>{i > 0 && ", "}{briefMedicine(e)}</span>)
                : g.items.map((e) => e.label).join(", ")}
            </p>
          </div>
        </li>
      ))}
    </ul>
  );
}

const MED_STATE: Record<MedicationState, string> = {
  current: "",
  end_unknown: "Duration not known",
  ended: "Course finished",
  stopped: "Stopped",
};

function CardSection({ title, icon, children }: { title: string; icon: ReactNode; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="flex items-center gap-1.5 text-xs font-bold tracking-wider text-ink-600 uppercase">
        {icon} {title}
      </h3>
      {children}
    </section>
  );
}

const NONE = <p className="text-ink-500">None recorded</p>;

/** What a doctor needs in the first minute. Built from confirmed entries only (server side).
 * Solid white, large type and red only for danger: it has to read at a glance, in any light. */
export function EmergencyCard({ summary, showTimeline = true }: { summary: EmergencySummary; showTimeline?: boolean }) {
  const p = summary.patient;
  const facts = [age(p.birth_year), sexWord(p.sex)].filter(Boolean).join(", ").replace(/^./, (c) => c.toUpperCase());
  return (
    <div className="space-y-4">
      <article className="overflow-hidden rounded-card bg-white shadow-[var(--lift)] ring-1 ring-red-200">
        <header className="space-y-3 bg-red-600 p-5 text-white">
          <p className="flex items-center gap-2 text-sm font-bold tracking-wide uppercase">
            <Siren size={18} aria-hidden /> Emergency card
          </p>
          <h2 className="text-4xl font-extrabold tracking-tight">{p.name}</h2>
          <div className="flex flex-wrap gap-2">
            {facts && <span className="rounded-full bg-red-900 px-3 py-1 text-sm font-bold text-white">{facts}</span>}
            {p.blood_group && (
              <span className="flex items-center gap-1.5 rounded-full bg-white px-3 py-1 text-sm font-extrabold text-red-800">
                <Droplet size={14} aria-hidden /> Blood group {p.blood_group}
              </span>
            )}
          </div>
        </header>

        <div className="space-y-5 p-5">
          {summary.open_stays.length > 0 && (
            <CardSection title="In hospital now" icon={<Building2 size={14} aria-hidden />}>
              <ul className="space-y-2">
                {summary.open_stays.map((s, i) => (
                  <li key={i} className="rounded-2xl bg-tide-50 px-4 py-3 ring-1 ring-tide-100">
                    <p className="text-lg font-bold">
                      {s.kind === "admission" ? "Admitted" : "Emergency visit"}
                      {s.place ? ` at ${s.place}` : ""} since {formatDate(s.since)}
                    </p>
                    {s.reason && <p className="font-semibold text-ink-700">{s.reason}</p>}
                    <p className="text-sm text-ink-500">Added by the patient</p>
                  </li>
                ))}
              </ul>
            </CardSection>
          )}
          <CardSection title="Allergies" icon={<ShieldAlert size={14} aria-hidden />}>
            {summary.allergies.length ? (
              <ul className="space-y-2">
                {summary.allergies.map((e) => (
                  <li key={e.id} className="rounded-2xl bg-red-50 px-4 py-3 ring-1 ring-red-200">
                    <p className="flex items-center gap-2 text-2xl font-extrabold tracking-tight text-red-800">
                      <ShieldAlert size={22} className="shrink-0" aria-hidden /> {e.label}
                    </p>
                    {e.fields.reaction && <p className="font-semibold text-red-700">Reaction: {e.fields.reaction}</p>}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="font-semibold text-ink-700">No known allergies recorded</p>
            )}
          </CardSection>

          <CardSection title="Current medicines" icon={<Pill size={14} aria-hidden />}>
            {summary.medications.length ? (
              <ul className="divide-y divide-ink-100 rounded-2xl ring-1 ring-ink-100">
                {summary.medications.map(({ entry, state }) => {
                  const m = entry.medication;
                  const how = entry.fields.frequency ? dosePattern(entry.fields.frequency) : m?.doses_per_day ? `${m.doses_per_day} per day` : "";
                  const generic = genericLine(entry);
                  return (
                    <li key={entry.id} className="flex flex-wrap items-baseline gap-x-2 gap-y-1 px-4 py-3">
                      <span className="text-lg font-bold">{entry.label}</span>
                      {how && <span className="text-ink-600">{how}</span>}
                      {entry.checks.includes("medicine_unnamed") && (
                        <span className="chip bg-amber-100 text-amber-900"><AlertTriangle size={12} aria-hidden /> No medicine name</span>
                      )}
                      {MED_STATE[state] && (
                        state === "end_unknown" ? (
                          <span className="chip ml-auto bg-amber-100 text-amber-900"><AlertTriangle size={12} aria-hidden /> {MED_STATE[state]}</span>
                        ) : (
                          <span className="chip ml-auto bg-ink-100 text-ink-700">{MED_STATE[state]}</span>
                        )
                      )}
                      {generic && <span className="w-full text-sm text-ink-500">Contains {generic}</span>}
                    </li>
                  );
                })}
              </ul>
            ) : NONE}
          </CardSection>

          <div className="grid gap-5 sm:grid-cols-2">
            <CardSection title="Long-term conditions" icon={<Stethoscope size={14} aria-hidden />}>
              {summary.conditions.length ? (
                <ul className="flex flex-wrap gap-2">
                  {summary.conditions.map((e) => (
                    <li key={e.id} className="rounded-full bg-ink-100 px-3 py-1 font-semibold text-ink-900">{e.label}</li>
                  ))}
                </ul>
              ) : NONE}
            </CardSection>
            <CardSection title="Implants" icon={<Syringe size={14} aria-hidden />}>
              {summary.implants.length ? (
                <ul className="flex flex-wrap gap-2">
                  {summary.implants.map((e) => (
                    <li key={e.id} className="rounded-full bg-ink-100 px-3 py-1 font-semibold text-ink-900">{e.label}</li>
                  ))}
                </ul>
              ) : <p className="text-ink-500">None recorded</p>}
            </CardSection>
          </div>

          <p className="flex items-center gap-1.5 border-t border-ink-100 pt-4 text-sm text-ink-500">
            <Lock size={14} aria-hidden /> Shows only what the patient confirmed. Not a substitute for a doctor's examination.
          </p>
        </div>
      </article>
      {showTimeline && <Timeline entries={summary.timeline} />}
    </div>
  );
}

/** `children`: shown at the end of the card (My records puts "Add a past event" there). */
export function Timeline({ entries, children }: { entries: Entry[]; children?: ReactNode }) {
  return (
    <div className="card space-y-3">
      <h3 className="section-title">Timeline</h3>
      {entries.length === 0 && (
        <p className="text-sm text-ink-400">No events yet.{children ? " Add a past event below." : ""}</p>
      )}
      <ol className="space-y-2">
        {entries.map((e) => (
          <li key={e.id} className="flex gap-3 text-sm">
            <span className="w-24 shrink-0 text-ink-500">
              {e.occurred_on ? (isDay(e.occurred_on) ? formatDate(e.occurred_on) : e.occurred_on) : "Date unknown"}
            </span>
            <span>
              {(e.kind === "surgery" || e.kind === "implant") && (
                <Pin size={14} className="mr-1 inline text-lagoon-700" aria-label="pinned" />
              )}
              <span className="font-medium">{e.label}</span>
              <span className="text-ink-500">, {kindLabel(e.kind).toLowerCase()}{e.unverified ? ", from memory, no document" : ""}</span>
            </span>
          </li>
        ))}
      </ol>
      {children}
    </div>
  );
}

const SOURCE_NAMES: Record<string, string> = {
  audio: "the recording",
  image: "a document",
  text: "your typed notes",
  manual: "you",
};

/** The proof behind an entry, behind one label everywhere ("See where this came from"): for each
 * source, its name, the quote first and large, a short excerpt around it, and the photo or the
 * recording at the right moment when this browser still has them. */
export function WhereFrom({ entry, sources, className = "" }: { entry: Entry; sources: SourceItem[]; className?: string }) {
  return (
    <Disclosure label="See where this came from" name={entry.label} className={className}>
      <Proof entry={entry} sources={sources} />
    </Disclosure>
  );
}

/** The proof itself, for a row that places its own trigger (see `WhereFrom`). */
export function Proof({ entry, sources }: { entry: Entry; sources: SourceItem[] }) {
  return (
    <div className="mb-2 space-y-5 rounded-2xl bg-ink-50/80 p-4 ring-1 ring-ink-100">
      {entry.provenance.map((p, i) => (
        <ProofItem key={i} p={p} source={sources.find((s) => s.id === p.source_id)} />
      ))}
    </div>
  );
}

function ProofItem({ p, source }: { p: Entry["provenance"][number]; source: SourceItem | undefined }) {
  const [whole, setWhole] = useState(false);
  const local = visitMedia.of(source);
  const start = locatorStart(p.locator);
  const text = source?.text ?? "";
  const hit = p.quote ? findQuote(text, p.quote) : null;
  const shown = whole ? null : hit && excerptAround(text, hit.index, hit.length);
  // Nothing more to show when the excerpt already is the whole text.
  const canExpand = text.length > 0 && (whole || !shown || shown.from > 0 || shown.to < text.length);
  return (
    <div className="space-y-3">
      <p className="text-sm text-ink-600">
        From {source?.filename ?? SOURCE_NAMES[p.source_type]}{p.locator ? ` at ${p.locator}` : ""}
      </p>
      {p.quote && (
        <blockquote className="border-l-2 border-ink-300 pl-3 text-base font-semibold text-ink-900">“{p.quote}”</blockquote>
      )}
      {text && (shown || whole) && (
        <p className="text-sm leading-relaxed whitespace-pre-line text-ink-700">
          {whole ? (
            <>
              {tight(hit ? text.slice(0, hit.index) : text)}
              {hit && <mark className="rounded bg-ink-200 px-0.5 text-ink-900">{tight(text.slice(hit.index, hit.index + hit.length))}</mark>}
              {hit && tight(text.slice(hit.index + hit.length))}
            </>
          ) : (
            shown && (
              <>
                {shown.from > 0 && "… "}
                {tight(text.slice(shown.from, hit!.index))}
                <mark className="rounded bg-ink-200 px-0.5 text-ink-900">{tight(text.slice(hit!.index, hit!.index + hit!.length))}</mark>
                {tight(text.slice(hit!.index + hit!.length, shown.to))}
                {shown.to < text.length && " …"}
              </>
            )
          )}
        </p>
      )}
      {canExpand && (
        <button
          type="button"
          className="inline-flex min-h-11 items-center text-sm font-semibold text-ink-700 underline underline-offset-2 hover:text-ink-900"
          aria-expanded={whole}
          onClick={() => setWhole(!whole)}
        >
          {whole ? "Show less" : "Show the whole text"}
        </button>
      )}
      {local?.kind === "image" && local.file.type === "application/pdf" && (
        <a className="btn-light" href={local.url} target="_blank" rel="noreferrer">
          Open the PDF{source?.pages && source.pages > 1 ? ` (${source.pages} pages)` : ""}
        </a>
      )}
      {local?.kind === "image" && local.file.type !== "application/pdf" && (
        <img src={local.url} alt="uploaded document" className="max-h-80 rounded-xl ring-1 ring-ink-200" />
      )}
      {local?.kind === "audio" && (
        <audio
          controls
          src={local.url}
          onLoadedMetadata={(ev) => {
            if (start !== null) ev.currentTarget.currentTime = start;
          }}
        />
      )}
    </div>
  );
}

/** Display only: runs of blank lines (and the spaces around them) become one line break. */
function tight(s: string): string {
  return s.replace(/[ \t]*\n[ \t\n]*/g, "\n");
}

/** Where the quote sits in the source text. Loose match (case and spacing) for display only; the
 * server's quote check is the real one. */
function findQuote(text: string, quote: string): { index: number; length: number } | null {
  if (!text) return null;
  const pattern = quote.trim().replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+");
  const m = new RegExp(pattern, "i").exec(text);
  return m ? { index: m.index, length: m[0].length } : null;
}

/** A short stretch of text around the quote, cut at word edges. */
function excerptAround(text: string, index: number, length: number): { from: number; to: number } {
  const radius = 140;
  let from = Math.max(0, index - radius);
  let to = Math.min(text.length, index + length + radius);
  if (from > 0) {
    const space = text.indexOf(" ", from);
    if (space !== -1 && space < index) from = space + 1;
  }
  if (to < text.length) {
    const space = text.lastIndexOf(" ", to);
    if (space > index + length) to = space;
  }
  return { from, to };
}
