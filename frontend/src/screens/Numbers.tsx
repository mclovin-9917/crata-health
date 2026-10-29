import { AlertTriangle, ChevronDown, Pencil, Plus, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, type NumberPoint, type NumberSeries, type NumbersGroup, type Preset } from "../api";
import { useMe } from "../App";
import { BandNotes, Chart, lineName, plainName, rangeWords, shown } from "../charts";
import { ActionsMenu, ErrorNote, Note } from "../components";
import { formatDate, go, useLoad } from "../lib";

/** My health numbers: every number in the record over time, from the reports and from readings
 * the patient adds. A number is compared only with a range printed on a report (its own, or the
 * latest one for the same measure); the screen never says what a number means. `openKey` (from
 * #/numbers/<measure key>, as linked from Ask about my health) opens that measure's row. */
export default function NumbersScreen({ openKey }: { openKey?: string }) {
  const me = useMe();
  const { data, error, reload } = useLoad(() => api.numbers(me.id), [me.id]);
  const [adding, setAdding] = useState(false);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="title min-w-0">My health numbers</h1>
        <button className="btn shrink-0 ml-auto" onClick={() => setAdding(true)} disabled={adding || !data} aria-label="Add a reading">
          <Plus size={16} aria-hidden /> <span className="sm:hidden">Add</span><span className="hidden sm:inline">Add a reading</span>
        </button>
      </div>
      <p className="text-ink-600">
        Numbers from your lab reports and prescriptions, plus readings you add.
      </p>
      {data && data.groups.length > 0 && (
        <p className="flex flex-wrap items-center gap-x-1.5 text-sm text-ink-700">
          <span className="inline-block size-2.5 rounded-full border border-amber-700 bg-amber-500" aria-hidden />
          Amber dot: outside the range printed on the report.
          <span className="inline-block h-3 w-5 rounded-sm bg-tide-500/10 ring-1 ring-tide-500/30" aria-hidden />
          Shaded band: the printed range.
        </p>
      )}
      <ErrorNote error={error} />
      {data && adding && (
        <AddReading
          presets={data.presets}
          onCancel={() => setAdding(false)}
          onDone={() => { setAdding(false); reload(); }}
        />
      )}
      {data && data.groups.length === 0 && !adding && (
        <div className="card text-sm text-ink-600">
          Nothing here yet. Add a reading, like blood pressure or weight, or add a lab report to a visit.
        </div>
      )}
      {data && data.groups.length > 0 && <NumbersTable groups={data.groups} onChanged={reload} initialOpen={openKey} />}
      {data && data.groups.length > 0 && (
        <p className="px-1 text-xs text-ink-500">
          Ranges differ between labs and between people. Units are never converted. Ask your doctor what a number
          means for you.
        </p>
      )}
    </div>
  );
}

/** "Above the printed range (4 to 5.6)" or "Above the range on your 14 Sep 2026 report (70 to 100)". */
function rangeNote(p: NumberPoint): string | null {
  if (!p.range || (p.position !== "low" && p.position !== "high")) return null;
  const side = p.position === "high" ? "Above" : "Below";
  const what = p.range.borrowed ? `the range on your ${formatDate(p.range.on)} report` : "the printed range";
  return `${side} ${what} (${rangeWords(p.range)})`;
}

const unitOf = (s: NumberSeries) => (s.unit_missing ? "(unit not printed)" : s.unit ?? "");

const HEAD = "px-2 md:px-3 py-2 text-xs font-bold tracking-wider text-ink-600 uppercase";
/* Below `md` the range, date and trend columns shrink to nothing instead of being display:none, so the
   table keeps the same number of columns and the open row can span all of them. Their text stays
   readable to a screen reader; the narrow layout shows the same facts inside the first two columns. */
const NARROW = "max-md:w-0 max-md:overflow-hidden max-md:p-0";

/** The numbers as a lab-report table: one row per measure, newest reading first. A row opens
 * to its chart and readings: clicking anywhere on the row toggles it (onClick on the row), and the
 * measure's name inside it is the real button, for the keyboard and screen readers. */
function NumbersTable({ groups, onChanged, initialOpen }: { groups: NumbersGroup[]; onChanged: () => void; initialOpen?: string }) {
  const [openKey, setOpenKey] = useState<string | null>(() => (initialOpen && groups.some((g) => g.key === initialOpen) ? initialOpen : null));
  const box = useRef<HTMLDivElement>(null);
  // A link from an answer lands on its measure: bring the opened row into view once.
  useEffect(() => {
    if (!initialOpen || openKey !== initialOpen) return;
    box.current?.querySelector("[aria-expanded='true']")?.scrollIntoView({ block: "center" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <div className="card p-2 sm:p-3" ref={box}>
      <table className="w-full table-fixed border-collapse text-left text-sm">
        <caption className="sr-only">
          One row for each measure. Select a row to open its chart and readings.
        </caption>
        <thead>
          <tr>
            <th scope="col" className={HEAD}>Measure</th>
            <th scope="col" className={`${HEAD} w-32 md:w-36 text-right`}>Latest</th>
            <th scope="col" className={`${HEAD} ${NARROW} md:w-32`}>Printed range</th>
            <th scope="col" className={`${HEAD} ${NARROW} md:w-28`}>Date</th>
            <th scope="col" className={`${HEAD} ${NARROW} md:w-[7.5rem]`}>Trend</th>
            <th scope="col" className="w-8 md:w-10"><span className="sr-only">Open or close the details</span></th>
          </tr>
        </thead>
        {groups.map((g) => (
          <GroupRows
            key={g.key}
            group={g}
            open={openKey === g.key}
            onToggle={() => setOpenKey(openKey === g.key ? null : g.key)}
            onChanged={onChanged}
          />
        ))}
      </table>
    </div>
  );
}

/** The latest value of a card: "134 / 84 mmHg" when its lines share a unit. */
function latestText(live: NumberSeries[]): { value: string; unit: string } {
  const units = live.map(unitOf);
  const values = live.map((s) => shown(s.latest!.value));
  if (units.every((u) => u === units[0])) return { value: values.join(" / "), unit: units[0] };
  return { value: values.map((v, i) => `${v} ${units[i]}`.trim()).join(" / "), unit: "" };
}

interface RangeLine { label: string | null; text: string; where: string | null }

/** The range column. One entry when the lines agree ("90 to 120 / 60 to 80"), one per named line
 * when they differ (another date, a line with nothing left, a range from another report). */
function rangeLines(group: NumbersGroup, live: NumberSeries[], uniform: boolean): RangeLine[] {
  const per = live.map((s) => {
    const r = s.latest!.range;
    return { s, text: r ? rangeWords(r) : null, where: r?.borrowed ? `on your ${formatDate(r.on)} report` : null };
  });
  if (per.every((p) => !p.text)) return [];
  const several = group.series.length > 1;
  if (!several || (uniform && per.every((p) => p.where === per[0].where))) {
    return [{ label: null, text: per.map((p) => p.text ?? "-").join(" / "), where: per.find((p) => p.where)?.where ?? null }];
  }
  return per.map((p) => ({ label: lineName(p.s), text: p.text ?? "-", where: p.where }));
}

function GroupRows({ group, open, onToggle, onChanged }: {
  group: NumbersGroup;
  open: boolean;
  onToggle: () => void;
  onChanged: () => void;
}) {
  const several = group.series.length > 1;
  const live = group.series.filter((s) => s.latest);
  const detailId = `numbers-${group.key}`;
  // The lines of a card are shown as one ("134 / 84") only when each has a latest reading from the same day.
  const uniform = !several || (live.length === group.series.length && live.every((s) => s.latest!.on === live[0].latest!.on));
  const latest = live.length ? latestText(live) : null;
  const ranges = rangeLines(group, live, uniform);
  const rangeSmall = ranges.map((r) => `${r.label ? `${r.label} ` : ""}${r.text}${r.where ? `, ${r.where}` : ""}`).join("; ");
  const date = group.latest_on ? formatDate(group.latest_on) : "";
  const count = `${group.count} reading${group.count === 1 ? "" : "s"}`;
  const outside = live.filter((s) => s.latest!.position === "low" || s.latest!.position === "high");
  return (
    <tbody className="border-t border-ink-100">
      <tr
        className={`cursor-pointer transition-colors hover:bg-white/60 has-[:focus-visible]:bg-tide-50 ${open ? "bg-white/50" : ""}`}
        onClick={onToggle}
      >
        <td className="px-2 md:px-3 py-3 align-middle">
          {/* No onClick on the button: its click reaches the row, which toggles once. */}
          <button
            type="button"
            className="text-left font-semibold text-ink-900"
            aria-expanded={open}
            aria-controls={open ? detailId : undefined}
          >
            {plainName(group.name)}
          </button>
          {date && <span className="mt-0.5 block text-xs text-ink-500 md:hidden">{date}, {count}</span>}
        </td>
        <td className="px-2 md:px-3 py-3 text-right align-middle">
          {latest ? (
            <>
              {uniform ? (
                <>
                  <span className="font-bold tabular-nums text-ink-900">
                    {latest.value}
                    {latest.unit && <span className="ml-1 text-xs font-semibold text-ink-600">{latest.unit}</span>}
                  </span>
                  {several && <span className="sr-only"> ({live.map(lineName).join(" / ")})</span>}
                </>
              ) : (
                live.map((s) => (
                  <span key={`${s.key}-${s.unit}-latest`} className="block">
                    <span className="text-xs font-semibold text-ink-600">{lineName(s)} </span>
                    <span className="font-bold tabular-nums text-ink-900">{shown(s.latest!.value)}</span>
                    <span className="ml-1 text-xs font-semibold text-ink-600">{unitOf(s)}</span>
                    <span className="block text-xs text-ink-500">({formatDate(s.latest!.on)})</span>
                  </span>
                ))
              )}
              {outside.length > 0 && (
                <span className="mt-1 flex flex-wrap justify-end gap-1">
                  {outside.map((s) => (
                    <span key={`${s.key}-${s.unit}-chip`} className="chip bg-amber-100 text-amber-900">
                      <AlertTriangle size={12} aria-hidden />
                      {several ? `${lineName(s)}: ` : ""}{s.latest!.position === "high" ? "Above range" : "Below range"}
                    </span>
                  ))}
                </span>
              )}
              {rangeSmall && <span className="mt-1 block text-xs text-ink-500 md:hidden">Range {rangeSmall}</span>}
            </>
          ) : (
            <span className="text-xs text-ink-500">All readings removed</span>
          )}
        </td>
        <td className={`px-3 py-3 align-middle tabular-nums text-ink-700 ${NARROW}`}>
          {ranges.length ? (
            ranges.map((r) => (
              <span key={r.label ?? "all"} className="block">
                {r.label && <span className="text-xs text-ink-500">{r.label} </span>}
                {r.text}
                {r.where && <span className="block text-xs text-ink-500">{r.where}</span>}
              </span>
            ))
          ) : (
            <>
              <span aria-hidden>&mdash;</span>
              <span className="sr-only">No range printed</span>
            </>
          )}
        </td>
        <td className={`px-3 py-3 align-middle text-ink-600 ${NARROW}`}>
          <span className="whitespace-nowrap">{date}</span>
          <span className="block text-xs text-ink-500">{count}</span>
        </td>
        <td className={`px-3 py-3 align-middle ${NARROW}`}><Chart series={group.series} /></td>
        <td className="w-8 md:w-10 py-3 pr-2 md:pr-3 text-right align-middle text-ink-500">
          <ChevronDown size={18} className={`inline transition ${open ? "rotate-180" : ""}`} aria-hidden />
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={6} className="px-2 md:px-3 pt-1 pb-5">
            <div id={detailId} className="space-y-4">
              <div className="space-y-2">
                <Chart series={group.series} big />
                <BandNotes series={group.series} />
              </div>
              {group.series.map((s) => (
                <Readings key={`${s.key}-${s.unit}-${s.unit_missing}`} series={s} title={several} onChanged={onChanged} />
              ))}
            </div>
          </td>
        </tr>
      )}
    </tbody>
  );
}

function Readings({ series, title, onChanged }: { series: NumberSeries; title: boolean; onChanged: () => void }) {
  return (
    <div className="space-y-1">
      {title && (
        <h3 className="text-xs font-bold tracking-wider text-ink-600 uppercase">
          {plainName(series.name)}{unitOf(series) ? `, ${unitOf(series)}` : ""}
        </h3>
      )}
      <ul className="divide-y divide-ink-100">
        {[...series.points].reverse().map((p) => (
          <Reading key={p.entry_id} point={p} name={series.name} unit={unitOf(series)} onChanged={onChanged} />
        ))}
      </ul>
    </div>
  );
}

function Reading({ point: p, name, unit, onChanged }: { point: NumberPoint; name: string; unit: string; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const act = async (body: Record<string, unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await api.entryAction(p.entry_id, body);
      setEditing(false);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const note = rangeNote(p);
  return (
    <li className="space-y-1 py-2.5">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="w-24 text-sm text-ink-600">{formatDate(p.on)}</span>
        <span className={`text-base font-bold tabular-nums ${p.removed ? "text-ink-500 line-through decoration-ink-300" : ""}`}>
          {shown(p.value)} <span className="text-sm font-semibold text-ink-600">{unit}</span>
        </span>
        <span className="text-xs text-ink-500">
          {p.source === "you" ? (
            "Added by you"
          ) : p.encounter_id ? (
            <button className="underline" onClick={() => go(`/encounters/${p.encounter_id}`)}>From the report</button>
          ) : (
            "From a report"
          )}
          {p.edited && ", edited by you"}
        </span>
        <span className="ml-auto flex items-center">
          {p.removed ? (
            <button className="link-quiet" disabled={busy} onClick={() => act({ action: "confirm" })} aria-label={`Add the ${name} reading of ${formatDate(p.on)} back`}>Add back</button>
          ) : (
            <ActionsMenu
              name={`${name} reading of ${formatDate(p.on)}`}
              items={[
                { label: "Edit", icon: <Pencil size={16} aria-hidden />, disabled: busy, onSelect: () => setEditing(true) },
                { label: "Remove", icon: <X size={16} aria-hidden />, disabled: busy, onSelect: () => void act({ action: "reject" }) },
              ]}
            />
          )}
        </span>
      </div>
      {!p.removed && p.range && rangeWords(p.range) && (
        <p className="text-xs text-ink-500">
          {p.range.borrowed ? `Range on your ${formatDate(p.range.on)} report` : "Printed range"}: {rangeWords(p.range)}
        </p>
      )}
      {!p.removed && note && (
        <Note tone="amber" icon={<AlertTriangle size={16} aria-hidden />}>{note}. Ask your doctor what it means for you.</Note>
      )}
      {editing && <ReadingForm point={p} unit={p.unit ?? ""} busy={busy} onSave={(r) => act({ action: "edit", result: r })} onCancel={() => setEditing(false)} />}
      <ErrorNote error={error} />
    </li>
  );
}

/** Correct a reading: the number, its unit and its date. The first reading stays in its history. */
function ReadingForm({ point, unit, busy, onSave, onCancel }: {
  point: NumberPoint;
  unit: string;
  busy: boolean;
  onSave: (result: Record<string, unknown>) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(String(point.value));
  const [u, setU] = useState(unit);
  const [on, setOn] = useState(point.on);
  const [problem, setProblem] = useState<string | null>(null);
  const id = `reading-${point.entry_id}`;
  const save = () => {
    const n = Number(value);
    if (!value.trim() || !Number.isFinite(n) || n < 0) return setProblem("Enter the number, for example 13.2.");
    setProblem(null);
    onSave({ value: n, unit: u.trim() || null, measured_on: on });
  };
  return (
    <div className="grid gap-3 rounded-2xl bg-ink-50/80 p-4 ring-1 ring-ink-100 sm:grid-cols-3">
      <label htmlFor={`${id}-value`}><span className="label">Number</span>
        <input id={`${id}-value`} className="input" inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} /></label>
      <label htmlFor={`${id}-unit`}><span className="label">Unit</span>
        <input id={`${id}-unit`} className="input" value={u} onChange={(e) => setU(e.target.value)} /></label>
      <label htmlFor={`${id}-date`}><span className="label">Date</span>
        <input id={`${id}-date`} type="date" className="input" value={on} max={new Date().toLocaleDateString("en-CA")} onChange={(e) => setOn(e.target.value)} /></label>
      {problem && <p className="text-sm font-semibold text-amber-800 sm:col-span-3">{problem}</p>}
      <div className="flex gap-2 sm:col-span-3">
        <button className="btn" disabled={busy} onClick={save}>Save changes</button>
        <button className="btn-light" disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

const OTHER = "Something else";

/** Add a reading the patient took: a common measure from the list (blood pressure is two
 * numbers) or any name. It lands on the same chart as the reports' numbers. */
function AddReading({ presets, onCancel, onDone }: { presets: Preset[]; onCancel: () => void; onDone: () => void }) {
  const me = useMe();
  const [choice, setChoice] = useState(presets[0]?.name ?? OTHER);
  const preset = presets.find((p) => p.name === choice);
  const [name, setName] = useState("");
  const [unit, setUnit] = useState(presets[0]?.unit ?? "");
  const [first, setFirst] = useState("");
  const [second, setSecond] = useState("");
  const [on, setOn] = useState(new Date().toLocaleDateString("en-CA"));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const pick = (value: string) => {
    setChoice(value);
    setUnit(presets.find((p) => p.name === value)?.unit ?? "");
  };
  const two = !!preset && preset.parts.length === 2;
  const number = (s: string) => (s.trim() && Number.isFinite(Number(s)) && Number(s) >= 0 ? Number(s) : null);

  const save = async () => {
    const a = number(first);
    const b = number(second);
    if (a === null || (two && b === null)) return setError(two ? "Enter both numbers, for example 138 and 88." : "Enter the number, for example 62.5.");
    if (!preset && !name.trim()) return setError("Enter what you measured.");
    setBusy(true);
    setError(null);
    try {
      const readings = two
        ? [{ name: preset!.parts[0], value: a, unit }, { name: preset!.parts[1], value: b!, unit }]
        : [{ name: preset ? preset.name : name.trim(), value: a, unit: unit.trim() || null }];
      await api.addReadings(me.id, readings, on);
      onDone();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="card grid gap-3 sm:grid-cols-2" role="group" aria-label="Add a reading">
      <label className="sm:col-span-2" htmlFor="reading-what"><span className="label">What did you measure?</span>
        <select id="reading-what" className="input" value={choice} onChange={(e) => pick(e.target.value)}>
          {presets.map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
          <option value={OTHER}>{OTHER}</option>
        </select></label>
      {!preset && (
        <label className="sm:col-span-2" htmlFor="reading-name"><span className="label">Name (as a report would call it)</span>
          <input id="reading-name" className="input" value={name} onChange={(e) => setName(e.target.value)} /></label>
      )}
      <label htmlFor="reading-first"><span className="label">{two ? "Top number (systolic)" : "Number"}</span>
        <input id="reading-first" className="input" inputMode="decimal" value={first} onChange={(e) => setFirst(e.target.value)} /></label>
      {two && (
        <label htmlFor="reading-second"><span className="label">Bottom number (diastolic)</span>
          <input id="reading-second" className="input" inputMode="decimal" value={second} onChange={(e) => setSecond(e.target.value)} /></label>
      )}
      <label htmlFor="reading-unit"><span className="label">Unit</span>
        <input id="reading-unit" className="input" value={unit} onChange={(e) => setUnit(e.target.value)} /></label>
      <label htmlFor="reading-date"><span className="label">Date</span>
        <input id="reading-date" type="date" className="input" value={on} max={new Date().toLocaleDateString("en-CA")} onChange={(e) => setOn(e.target.value)} /></label>
      {error && <p className="text-sm font-semibold text-amber-800 sm:col-span-2">{error}</p>}
      <div className="flex gap-2 sm:col-span-2">
        <button className="btn" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save reading"}</button>
        <button className="btn-light" disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
      <p className="text-xs text-ink-500 sm:col-span-2">Marked as added by you. Never shown on your emergency card.</p>
    </div>
  );
}
