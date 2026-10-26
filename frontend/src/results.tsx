import { Plus } from "lucide-react";
import { useState } from "react";
import type { Entry, TestResult } from "./api";
import { go } from "./lib";
import { wasReplaced } from "./media";

/** A test's values, in the order they were read. */
export function valuesOf(entries: Entry[], test: Entry): Entry[] {
  return entries.filter((e) => e.kind === "result" && e.links.includes(test.id));
}

/** A test's confirmed values, as the report view and the Summary list show them. */
export function confirmedValuesOf(entries: Entry[], test: Entry): Entry[] {
  return valuesOf(entries, test).filter((e) => e.status === "confirmed" && !wasReplaced(e) && e.result);
}

/** The date of a report: the one its values carry (the newest when they differ), or null. */
export function reportDate(values: Entry[]): string | null {
  const days = values.map((v) => v.result?.measured_on).filter((d): d is string => !!d).sort();
  return days.length ? days[days.length - 1] : null;
}

/** Which side of the range printed on the report a number is on; null inside it, or when the
 * value is in words or the report printed no range. */
export function outsideRange(r: TestResult): "above" | "below" | null {
  if (r.value === null) return null;
  if (r.ref_high !== null && r.value > r.ref_high) return "above";
  if (r.ref_low !== null && r.value < r.ref_low) return "below";
  return null;
}

/** Values grouped under the test they belong to; values that belong to none (numbers written on
 * a prescription) come last. Values the app replaced or that were removed with a re-read are
 * left out; values the patient removed are kept, so they can be added back. */
export function groupValues(entries: Entry[]): { test: Entry | null; values: Entry[] }[] {
  const values = entries.filter((e) => e.kind === "result" && !wasReplaced(e));
  const tests = entries.filter((e) => e.kind === "test" && !wasReplaced(e));
  const groups = tests
    .map((test) => ({ test, values: values.filter((v) => v.links.includes(test.id)) }))
    .filter((g) => g.values.length);
  const placed = new Set(groups.flatMap((g) => g.values.map((v) => v.id)));
  const loose = values.filter((v) => !placed.has(v.id));
  return loose.length ? [...groups, { test: null, values: loose }] : groups;
}

/** "Add report" on a test that has no values yet: opens the add-documents form for that test. */
export function AddReport({ visitId, test, className = "" }: { visitId: string; test: Entry; className?: string }) {
  return (
    <button className={`btn-light py-1.5 ${className}`} onClick={() => go(`/add/${visitId}/${test.id}`)}>
      <Plus size={16} aria-hidden /> Add report
    </button>
  );
}

const num = (s: string): number | null | "bad" => {
  const t = s.trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : "bad";
};

/** Correct a value: name, number or words, unit, the report's range, the date. The printed quote
 * stays attached as proof, and the first reading stays in the history. */
export function ValueForm({ entry, busy, onSave, onCancel }: {
  entry: Entry;
  busy: boolean;
  onSave: (body: Record<string, unknown>) => void;
  onCancel: () => void;
}) {
  const r: TestResult = entry.result ?? { value: null, value_text: null, unit: null, ref_low: null, ref_high: null, ref_text: null, measured_on: null };
  const [label, setLabel] = useState(entry.label);
  const [value, setValue] = useState(r.value?.toString() ?? "");
  const [words, setWords] = useState(r.value_text ?? "");
  const [unit, setUnit] = useState(r.unit ?? "");
  const [low, setLow] = useState(r.ref_low?.toString() ?? "");
  const [high, setHigh] = useState(r.ref_high?.toString() ?? "");
  const [on, setOn] = useState(r.measured_on ?? "");
  const [problem, setProblem] = useState<string | null>(null);
  const id = `value-${entry.id}`;

  const save = () => {
    const [v, lo, hi] = [num(value), num(low), num(high)];
    if (v === "bad" || lo === "bad" || hi === "bad") return setProblem("Enter numbers only, for example 13.2.");
    if (v === null && !words.trim()) return setProblem("Enter the number, or the result in words.");
    setProblem(null);
    onSave({
      label: label.trim() || entry.label,
      result: {
        value: v, value_text: words.trim() || null, unit: unit.trim() || null,
        ref_low: lo, ref_high: hi, measured_on: on || null,
      },
    });
  };

  return (
    <div className="grid gap-3 rounded-2xl bg-ink-50/80 p-4 ring-1 ring-ink-100 sm:grid-cols-2">
      <label className="sm:col-span-2" htmlFor={`${id}-name`}>
        <span className="label">Name</span>
        <input id={`${id}-name`} className="input" value={label} onChange={(e) => setLabel(e.target.value)} />
      </label>
      <label htmlFor={`${id}-value`}>
        <span className="label">Number</span>
        <input id={`${id}-value`} className="input" inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} />
      </label>
      <label htmlFor={`${id}-unit`}>
        <span className="label">Unit</span>
        <input id={`${id}-unit`} className="input" value={unit} onChange={(e) => setUnit(e.target.value)} />
      </label>
      <label className="sm:col-span-2" htmlFor={`${id}-words`}>
        <span className="label">Result in words (instead of a number)</span>
        <input id={`${id}-words`} className="input" value={words} onChange={(e) => setWords(e.target.value)} />
      </label>
      <label htmlFor={`${id}-low`}>
        <span className="label">Printed range from</span>
        <input id={`${id}-low`} className="input" inputMode="decimal" value={low} onChange={(e) => setLow(e.target.value)} />
      </label>
      <label htmlFor={`${id}-high`}>
        <span className="label">Printed range up to</span>
        <input id={`${id}-high`} className="input" inputMode="decimal" value={high} onChange={(e) => setHigh(e.target.value)} />
      </label>
      <label className="sm:col-span-2" htmlFor={`${id}-date`}>
        <span className="label">Date of the report</span>
        <input id={`${id}-date`} type="date" className="input" value={on} max={new Date().toLocaleDateString("en-CA")} onChange={(e) => setOn(e.target.value)} />
      </label>
      {problem && <p className="text-sm font-semibold text-amber-800 sm:col-span-2">{problem}</p>}
      <div className="flex gap-2 sm:col-span-2">
        <button className="btn" disabled={busy} onClick={save}>Save changes</button>
        <button className="btn-light" disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
      <p className="text-xs text-ink-500 sm:col-span-2">
        The printed words stay saved as the source, and the first reading is kept in the history.
      </p>
    </div>
  );
}
