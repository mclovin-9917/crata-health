import { AlertTriangle, ChevronRight, Sparkles } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { api, EVENT_KINDS, type Entry, type EntryKind, type Fact, type Visit } from "../api";
import { useMe } from "../App";
import { DisclosureButton, EntryDetails, ErrorNote, PatientBasics, SaidBy, Timeline, addedToVisit, groupByPrefix, valueText } from "../components";
import { MedicineName } from "../medicine";
import { AddReport, confirmedValuesOf, groupValues, outsideRange, reportDate } from "../results";
import { STAY_KIND } from "../stay";
import { editedByPatient, formatDate, go, kindLabel, translated, useLoad } from "../lib";

// Summary sections, in reading order: the essentials first, then the
// timeline, then the tests and reports, then what came from recent visits. Events live on the
// timeline instead. Test values sit in each test's report view (#/reports/<test id>).
const ESSENTIALS: [string, EntryKind[]][] = [
  ["Allergies", ["allergy"]],
  ["Diagnoses", ["diagnosis"]],
  ["Medicines", ["medication"]],
];
const FROM_VISITS: [string, EntryKind[]][] = [
  ["Symptoms you reported", ["symptom"]],
  ["Follow-ups", ["follow_up"]],
  ["Advice and notes", ["note"]],
];

export default function RecordScreen() {
  const me = useMe();
  const { data, error, reload } = useLoad(() => api.record(me.id), [me.id]);
  const [tab, setTab] = useState<"summary" | "visits">("summary");

  if (error) return <ErrorNote error={error} />;
  if (!data) return <p className="text-ink-500">Loading…</p>;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="title">My record</h1>
        <div className="flex rounded-full bg-white/70 p-1 text-sm ring-1 ring-ink-200 backdrop-blur">
          {(["summary", "visits"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              aria-pressed={tab === t}
              className={`min-h-11 rounded-full px-3.5 py-1 font-semibold transition ${
                tab === t ? "text-white [background:var(--grad-action)]" : "text-ink-600 hover:text-ink-900"
              }`}
            >
              {t === "summary" ? "Summary" : `Visits (${data.visits.filter((v) => v.encounter_id).length})`}
            </button>
          ))}
        </div>
      </div>
      <PatientBasics patient={me} />
      {tab === "summary" ? (
        <Summary facts={data.facts} visits={data.visits} timeline={data.timeline} reload={reload} />
      ) : (
        <Visits visits={data.visits} />
      )}
    </div>
  );
}

/** One fact's row: the fact, its action (if any), then an icon-only chevron at the right end; the
 * details open below the row. */
function FactRow({ main, action, name, children }: { main: ReactNode; action: ReactNode; name: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <li>
      <div className="flex flex-wrap items-start gap-x-2">
        <div className="min-w-0 grow py-1">{main}</div>
        {action}
        <DisclosureButton label="Show details" name={name} variant="icon" open={open} controls={id} onToggle={() => setOpen(!open)} />
      </div>
      <div id={id} hidden={!open}>
        {open && children}
      </div>
    </li>
  );
}

// ---- consolidated: each fact once ----------------------------------------------------------
function Summary({ facts, visits, timeline, reload }: {
  facts: Fact[];
  visits: Visit[];
  timeline: Parameters<typeof Timeline>[0]["entries"];
  reload: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const dateOf = new Map(visits.map((v) => [v.encounter_id, v.visit_date]));
  const stop = async (own: Entry) => {
    setError(null);
    try {
      await api.entryAction(own.id, { action: "stop" });
      reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  // Where a fact was seen: the visits (a link to each), and, if the patient added it themselves,
  // "added by you" once (with how many times when it was several, such as readings).
  const seenIn = (f: Fact) => {
    const visits = f.sightings.filter((s) => s.encounter_id);
    const hand = f.sightings.length - visits.length;
    return (
      <>
        {visits.length > 1 ? `Seen in ${visits.length} visits: ` : visits.length === 1 ? "From " : ""}
        {visits.map((s, i) => (
          <span key={s.entry_id}>
            {i > 0 && ", "}
            <button className="underline" onClick={() => go(`/encounters/${s.encounter_id}`)}>
              {formatDate(dateOf.get(s.encounter_id) ?? s.on)}
            </button>
          </span>
        ))}
        {hand > 0 && (
          <>
            {visits.length > 0 ? ", added by you" : "Added by you"}
            {hand > 1 ? ` (${hand} times)` : ""}
          </>
        )}
      </>
    );
  };

  // One fact, once. `text` replaces the label when a shared title is shown above a group.
  const fact = (f: Fact, text?: string) => {
    const own = f.kind === "medication" ? f.latest : null;
    return (
    <FactRow
      key={f.key}
      name={text ?? f.latest.label}
      main={
        <>
          <div className="flex flex-wrap items-baseline gap-2">
            <MedicineName entry={text ? { ...f.latest, label: text } : f.latest} />
            {editedByPatient(f.latest) && <span className="text-xs text-amber-700">Edited by you</span>}
            {translated(f.latest) && <span className="text-xs text-ink-500">Translated</span>}
          </div>
          <EntryDetails entry={f.latest} />
        </>
      }
      action={null}
    >
      <div className="flex flex-wrap items-center gap-2 pb-1">
        <SaidBy speaker={f.latest.said_by} added={addedToVisit(f.latest)} />
        <p className="text-xs text-ink-500">{seenIn(f)}</p>
      </div>
      {/* Inside the details, not beside the chevron: it changes the emergency card. */}
      {own && !own.medication?.stopped_on && (
        <div className="pb-1">
          <button className="link-quiet" onClick={() => stop(own)}>I stopped taking this</button>
        </div>
      )}
    </FactRow>
    );
  };

  // The facts of some kinds, once each; null when there are none.
  const list = (kinds: EntryKind[]) => {
    const items = facts.filter((f) => kinds.includes(f.kind));
    if (!items.length) return null;
    // Notes that share a title ("Given in hospital: ...") are shown once under it.
    const groups = kinds.includes("note")
      ? groupByPrefix(items, (f) => f.latest.label)
      : items.map((f) => ({ title: null, items: [{ item: f, text: f.latest.label }] }));
    return (
      <ul className="space-y-3">
        {groups.map((g) =>
          g.title === null ? (
            fact(g.items[0].item)
          ) : (
            <li key={g.title}>
              <p className="text-xs font-bold tracking-wider text-ink-600 uppercase">
                {g.title} <span className="text-ink-400 tabular-nums">{g.items.length}</span>
              </p>
              <ul className="mt-2 space-y-3 border-l-2 border-ink-100 pl-4">
                {g.items.map(({ item, text }) => fact(item, text))}
              </ul>
            </li>
          ),
        )}
      </ul>
    );
  };
  const section = ([title, kinds]: [string, EntryKind[]]) => {
    const body = list(kinds);
    return body && (
      <div key={title} className="card">
        <h2 className="section-title mb-3">{title}</h2>
        {body}
      </div>
    );
  };
  const visitSections = FROM_VISITS.map(([title, kinds]) => ({ title, body: list(kinds) })).filter((s) => s.body);
  // Each confirmed test once, with one report per visit it was seen in (newest first); a report's
  // values (numbers and words) are on its report view.
  const allEntries = visits.flatMap((v) => v.entries);
  const byId = new Map(allEntries.map((e) => [e.id, e]));
  const testIds = new Set(allEntries.filter((e) => e.kind === "test").map((e) => e.id));
  const tests = facts.filter((f) => f.kind === "test").map((f) => {
    const seen = f.sightings.map((s) => byId.get(s.entry_id)).filter((e): e is Entry => !!e && e.kind === "test");
    const reports = (seen.length ? seen : [f.latest]).map((test) => {
      const values = confirmedValuesOf(allEntries, test);
      return {
        test,
        count: values.length,
        outside: values.some((v) => outsideRange(v.result!) !== null),
        on: reportDate(values) ?? dateOf.get(test.encounter_id) ?? test.occurred_on,
      };
    });
    return { fact: f, reports };
  });
  // Results that belong to no confirmed test (words such as "Nil", numbers written on a
  // prescription): the latest of each. Readings the patient added live on My health numbers.
  const otherResults = facts.filter(
    (f) => f.kind === "result" && f.latest.result && f.latest.encounter_id && !f.latest.links.some((id) => testIds.has(id)),
  );
  const hasNumbers = facts.some((f) => f.kind === "result" && f.latest.result?.value != null);
  type ReportRow = (typeof tests)[number]["reports"][number];
  // One report: its date, how many values, an amber chip if one is outside the range, then a way
  // in (its report) or "Add report" when it has no values.
  const reportRow = ({ test, count, outside }: ReportRow, title: string, sub: string | null) => {
    const text = (
      <span className="min-w-0 grow">
        <span className="block font-semibold">{title}</span>
        <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-ink-600">
          {(sub || count > 0) && (
            <span>{[sub, count > 0 ? `${count} result${count === 1 ? "" : "s"}` : null].filter(Boolean).join(", ")}</span>
          )}
          {outside && (
            <span className="chip bg-amber-100 text-amber-900">
              <AlertTriangle size={12} aria-hidden /> Outside printed range
            </span>
          )}
        </span>
      </span>
    );
    return count > 0 ? (
      <button
        className="flex min-h-11 w-full items-center gap-3 py-2 text-left transition hover:bg-white/60"
        onClick={() => go(`/reports/${test.id}`)}
      >
        {text}
        <ChevronRight size={18} className="shrink-0 text-ink-500" aria-hidden />
      </button>
    ) : (
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
        {text}
        {test.encounter_id && <AddReport visitId={test.encounter_id} test={test} />}
      </div>
    );
  };

  return (
    <div className="space-y-4">
      <ErrorNote error={error} />
      {ESSENTIALS.map(section)}
      <Timeline entries={timeline}>
        <AddEvent onAdded={reload} />
      </Timeline>
      {(tests.length > 0 || otherResults.length > 0 || hasNumbers) && (
        <div className="card space-y-3">
          <h2 className="section-title">Tests and reports</h2>
          {tests.length > 0 && (
            <ul className="divide-y divide-ink-100">
              {tests.map(({ fact: f, reports }) => (
                <li key={f.key}>
                  {reports.length === 1 ? (
                    reportRow(reports[0], f.latest.label, formatDate(reports[0].on))
                  ) : (
                    <div className="py-2">
                      <p className="font-semibold">{f.latest.label}</p>
                      <ul className="mt-1 ml-1 divide-y divide-ink-100 border-l-2 border-ink-100 pl-3">
                        {reports.map((r) => (
                          <li key={r.test.id}>{reportRow(r, formatDate(r.on), null)}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
          {otherResults.length > 0 && (
            <div>
              <h3 className="text-sm font-semibold text-ink-700">Other results</h3>
              <ul className="mt-1 space-y-1 text-sm">
                {otherResults.map((f) => {
                  const r = f.latest.result!;
                  return (
                    <li key={f.key}>
                      <span className="font-semibold">{f.latest.label}</span>: {valueText(r)}
                      {r.measured_on && <span className="text-ink-500">, {formatDate(r.measured_on)}</span>}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
          {hasNumbers && (
            <button
              className="flex min-h-11 w-full items-center gap-3 text-left text-sm font-semibold text-tide-700 hover:underline"
              onClick={() => go("/numbers")}
            >
              <span className="grow">Open My health numbers</span>
              <ChevronRight size={18} className="shrink-0 text-ink-500" aria-hidden />
            </button>
          )}
        </div>
      )}
      {visitSections.length > 0 && (
        <div className="card space-y-4">
          <h2 className="section-title">From recent visits</h2>
          {visitSections.map((s) => (
            <div key={s.title}>
              <h3 className="mb-2 text-sm font-semibold text-ink-700">{s.title}</h3>
              {s.body}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---- visit by visit ------------------------------------------------------------------------
// Grouped by month, newest first (the server sorts them). Each visit is one short row that opens
// the visit page, where the details, "change date", the name and "delete this visit" live.
function monthOf(iso: string | null): string {
  if (!iso) return "Date unknown";
  const [y, m] = iso.slice(0, 7).split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString(undefined, { month: "long", year: "numeric" });
}

/** What to call a visit that has no name: what it holds, in plain words. */
function fallbackName(v: Visit): string {
  if (v.stay) return STAY_KIND[v.stay.kind];
  const has = (...kinds: string[]) => v.entries.some((e) => kinds.includes(e.kind));
  const parts: string[] = [];
  if (has("medication")) parts.push("Prescription");
  if (has("result")) parts.push("Lab report"); // a prescription that only orders tests is not one
  if (!parts.length) {
    const types = v.sources.map((s) => s.source_type);
    if (types.includes("audio")) parts.push("Recording");
    else if (types.includes("image")) parts.push("Documents");
    else if (types.includes("text")) parts.push("Typed notes");
  }
  return parts.length ? parts.map((p, i) => (i ? p.toLowerCase() : p)).join(" and ") : "Visit";
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

function Visits({ visits }: { visits: Visit[] }) {
  if (!visits.length) {
    return (
      <div className="space-y-5">
        <div className="space-y-3">
          <p className="text-sm text-ink-500">No visits yet. Add your first one.</p>
          <button className="btn" onClick={() => go("/add")}>Add a visit</button>
        </div>
      </div>
    );
  }
  const months: { month: string; items: Visit[] }[] = [];
  for (const v of visits.filter((x) => x.encounter_id)) {
    const month = monthOf(v.visit_date);
    const last = months[months.length - 1];
    if (last && last.month === month) last.items.push(v);
    else months.push({ month, items: [v] });
  }
  // Readings the patient added (blood pressure, weight...) live on My health numbers, so they do
  // not crowd this list.
  const byHand = visits.find((v) => !v.encounter_id);
  const own = byHand && { ...byHand, entries: byHand.entries.filter((e) => e.kind !== "result") };
  return (
    <div className="space-y-5">
      {months.map((g) => (
        <section key={g.month} aria-label={g.month}>
          <h2 className="mb-2 text-xs font-bold tracking-wider text-ink-600 uppercase">{g.month}</h2>
          <ul className="space-y-2">
            {g.items.map((v) => <VisitRow key={v.encounter_id} visit={v} />)}
          </ul>
        </section>
      ))}
      {own && own.entries.length > 0 && (
        <div className="card">
          <h2 className="section-title">Added by you</h2>
          <EntryList visit={own} />
        </div>
      )}
    </div>
  );
}

function VisitRow({ visit }: { visit: Visit }) {
  const facts = visit.entries.filter((e) => e.kind !== "result").length;
  const values = visit.entries.filter((e) => e.kind === "result").length;
  const counts = [facts && plural(facts, "fact"), values && plural(values, "result")].filter(Boolean).join(", ");
  // A stay shows its place.
  const name = visit.title ?? fallbackName(visit);
  const where = visit.stay?.place ?? visit.facility;
  return (
    <li>
      <button
        className={`card flex min-h-11 w-full items-center gap-3 p-4 text-left hover:bg-white/90 ${visit.stay ? "border-l-4 border-l-lagoon-600" : ""}`}
        onClick={() => go(`/encounters/${visit.encounter_id}`)}
      >
        <span className="min-w-0 grow">
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className={`font-semibold ${visit.title ? "" : "text-ink-700"}`}>{name}</span>
            {visit.title && visit.title_source === "ai" && (
              <span className="chip bg-tide-50 text-tide-800"><Sparkles size={12} aria-hidden /> Suggested</span>
            )}
            {visit.stay && !visit.stay.ended_on && (
              <span className="chip bg-white text-lagoon-800 ring-1 ring-lagoon-200">Still there</span>
            )}
          </span>
          <span className="block text-xs text-ink-500">
            {[formatDate(visit.visit_date), where].filter(Boolean).join(", ")}
          </span>
        </span>
        <span className="flex shrink-0 flex-wrap items-center justify-end gap-2 text-xs text-ink-600">
          {visit.status === "failed" && (
            <span className="chip bg-red-50 text-red-800"><AlertTriangle size={12} aria-hidden /> Could not be read</span>
          )}
          {visit.status === "processing" && <span>Reading…</span>}
          {visit.pending > 0 && <span className="chip bg-tide-50 text-tide-800">{visit.pending} to review</span>}
          {counts ? <span>{counts}</span> : visit.pending === 0 && visit.status === "ready" ? <span>Nothing confirmed</span> : null}
        </span>
        <ChevronRight size={18} className="shrink-0 text-ink-500" aria-hidden />
      </button>
    </li>
  );
}

function EntryList({ visit }: { visit: Visit }) {
  if (!visit.entries.length) return <p className="mt-2 text-sm text-ink-400">Nothing confirmed from this visit.</p>;
  // Values sit under their test; ones that belong to no test (numbers written on a prescription)
  // come after the rest.
  const values = groupValues(visit.entries).filter((g) => g.values.some((v) => v.status === "confirmed"));
  const under = (testId: string) =>
    values.find((g) => g.test?.id === testId)?.values.filter((v) => v.status === "confirmed") ?? [];
  const loose = values.find((g) => g.test === null)?.values.filter((v) => v.status === "confirmed") ?? [];
  type E = (typeof visit.entries)[number];
  const row = (e: E, nested: boolean, text?: string) => (
    <li key={e.id} className={nested ? "ml-4 border-l-2 border-ink-100 pl-3" : ""}>
      <div className="flex flex-wrap items-baseline gap-2">
        <MedicineName entry={text ? { ...e, label: text } : e} />
        {!nested && <span className="text-xs uppercase text-ink-500">{kindLabel(e.kind)}</span>}
        {!nested && <SaidBy speaker={e.said_by} added={addedToVisit(e)} />}
        {editedByPatient(e) && <span className="text-xs text-amber-700">Edited by you</span>}
      </div>
      <EntryDetails entry={e} />
      {e.kind === "test" && visit.encounter_id && under(e.id).length === 0 && (
        <div className="mt-1"><AddReport visitId={visit.encounter_id} test={e} /></div>
      )}
    </li>
  );
  // Notes that share a title ("Given in hospital: ...") are shown once under it, where the first
  // of them is.
  const noteGroups = groupByPrefix(visit.entries.filter((e) => e.kind === "note"), (e) => e.label)
    .filter((g) => g.title !== null);
  const groupAt = new Map(noteGroups.map((g) => [g.items[0].item.id, g]));
  const grouped = new Set(noteGroups.flatMap((g) => g.items.map((i) => i.item.id)));
  return (
    <ul className="mt-2 space-y-2">
      {visit.entries.filter((e) => e.kind !== "result").flatMap((e) => {
        if (grouped.has(e.id)) {
          const g = groupAt.get(e.id);
          return g ? [(
            <li key={`group-${g.title}`}>
              <p className="text-xs font-semibold tracking-wide text-ink-500 uppercase">
                {g.title} <span className="text-ink-400 tabular-nums">{g.items.length}</span>
              </p>
              <ul className="mt-1 ml-4 space-y-2 border-l-2 border-ink-100 pl-3">
                {g.items.map(({ item, text }) => row(item, true, text))}
              </ul>
            </li>
          )] : [];
        }
        return [row(e, false), ...(e.kind === "test" ? under(e.id).map((v) => row(v, true)) : [])];
      })}
      {loose.length > 0 && <li className="pt-1 text-xs font-semibold tracking-wide text-ink-500 uppercase">Other results</li>}
      {loose.map((v) => row(v, true))}
    </ul>
  );
}

// ---- add a past event by hand ----------------------------------------------------------------
function AddEvent({ onAdded }: { onAdded: () => void }) {
  const me = useMe();
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<EntryKind>("surgery");
  const [label, setLabel] = useState("");
  const [when, setWhen] = useState("");
  const [error, setError] = useState<string | null>(null);

  const add = async () => {
    setError(null);
    try {
      await api.addEvent(me.id, { kind, label, occurred_on: when || null });
      setLabel("");
      setWhen("");
      setOpen(false);
      onAdded();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (!open) return <button className="btn-light" onClick={() => setOpen(true)}>Add a past event</button>;
  return (
    <div className="grid gap-3 rounded-2xl bg-ink-50/80 p-4 ring-1 ring-ink-100 sm:grid-cols-3">
      <label className="block" htmlFor="event-kind"><span className="label">Type</span>
        <select id="event-kind" className="input" value={kind} onChange={(e) => setKind(e.target.value as EntryKind)}>
          {EVENT_KINDS.map((k) => <option key={k} value={k}>{kindLabel(k)}</option>)}
        </select></label>
      <label className="block" htmlFor="event-what"><span className="label">What was it?</span>
        <input id="event-what" className="input" value={label} onChange={(e) => setLabel(e.target.value)} /></label>
      <label className="block" htmlFor="event-when"><span className="label">When</span>
        <input id="event-when" className="input" value={when} aria-describedby="event-when-hint" onChange={(e) => setWhen(e.target.value)} /></label>
      <p id="event-when-hint" className="text-xs text-ink-500 sm:col-span-3">
        A year, a month or a full date, for example 2019 or 2019-03-12. It is marked "from memory, no document".
      </p>
      <div className="flex gap-2 sm:col-span-3">
        <button className="btn" disabled={!label.trim()} onClick={add}>Add event</button>
        <button className="btn-light" onClick={() => setOpen(false)}>Cancel</button>
      </div>
      <div className="sm:col-span-3"><ErrorNote error={error} /></div>
    </div>
  );
}
