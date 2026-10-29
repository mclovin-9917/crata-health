import {
  AlertTriangle, ArrowUp, ExternalLink, FileText, Globe, LineChart, Lightbulb, Loader2, Plus, ShieldAlert, Sparkles, Stethoscope,
} from "lucide-react";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { api, type AskAnswer, type AskCategory, type AskChart, type AskPoint, type AskRecordItem, type AskSource } from "../api";
import { useMe } from "../App";
import { EmergencyNow } from "../assist";
import { BandNotes, Chart, lineName, lineTone, plainName, shown } from "../charts";
import { ErrorNote } from "../components";
import { formatDate } from "../lib";
import { appendText, VoiceButton } from "../voice";

const EXAMPLES = [
  "How have my sugar levels been?",
  "What tablets am I taking for BP?",
  "Is papaya good for me?",
  "What should I eat with high blood pressure?",
];
const KEEP = 10;
const storeKey = (personId: string) => `crata.askThread.${personId}`;

/** One question and its answer. The thread lives in this browser tab only (never on the server). */
interface Turn { question: string; answer: AskAnswer }

/** Oldest first. Read back after a trip to a visit or a report, so the way back finds the thread. */
function readThread(personId: string): Turn[] {
  try {
    const raw = sessionStorage.getItem(storeKey(personId));
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list)
      ? list.filter((t) => t && typeof t.question === "string" && t.answer && Array.isArray(t.answer.points)).slice(-KEEP)
      : [];
  } catch {
    return [];
  }
}

function writeThread(personId: string, list: Turn[]) {
  try {
    if (list.length === 0) sessionStorage.removeItem(storeKey(personId));
    else sessionStorage.setItem(storeKey(personId), JSON.stringify(list.slice(-KEEP)));
  } catch {
    // storage blocked: the thread just is not kept
  }
}

const reducedMotion = () => typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const touchScreen = () => typeof window.matchMedia === "function" && window.matchMedia("(pointer: coarse)").matches;

/** Ask about my health, as a conversation: the patient's questions and the answers stay on screen in
 * order, one question box under them. Answers come from the patient's confirmed record and from
 * trusted health sites. */
export default function AskScreen() {
  const me = useMe();
  const [thread, setThread] = useState<Turn[]>(() => readThread(me.id));
  const [pending, setPending] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [error, setError] = useState<string | null>(null);
  const newest = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLInputElement>(null);
  const first = useRef(true);
  const busy = pending !== null;

  // The page follows the conversation: the newest question comes to the top of the screen.
  useEffect(() => {
    const isFirst = first.current;
    first.current = false;
    if (isFirst && thread.length === 0) return;
    newest.current?.scrollIntoView({ block: "start", behavior: isFirst || reducedMotion() ? "auto" : "smooth" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [thread.length, pending]);

  const ask = async (q: string) => {
    const text = q.trim();
    if (!text || busy) return;
    // The server gets only the last question that was really answered (not a "not a health question"
    // or an emergency card) and its web points, so "what about mango?" or "check again" has something
    // to lean on. A record answer sends the question alone. Nothing is stored there, and no record
    // item is ever sent.
    const last = [...thread].reverse().find(
      (t) => !t.answer.urgent && t.answer.kind !== "not_health" && (t.answer.kind || t.answer.health_question),
    );
    const previous = last
      ? { question: last.question, points: last.answer.points.map((p) => p.text) }
      : null;
    setPending(text);
    setQuestion("");
    setError(null);
    try {
      const answer = await api.ask(me.id, text, previous);
      const next = [...thread, { question: text, answer }].slice(-KEEP);
      setThread(next);
      writeThread(me.id, next);
    } catch (e) {
      setError((e as Error).message);
      setQuestion(text); // nothing is lost: edit it or send it again
    } finally {
      setPending(null);
      if (!touchScreen()) box.current?.focus();
    }
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    void ask(question);
  };
  const clear = () => {
    setThread([]);
    setError(null);
    writeThread(me.id, []);
  };

  const empty = thread.length === 0 && !busy;
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start gap-x-3 gap-y-1">
        <div className="min-w-0 flex-1 basis-60">
          <h1 className="title">Ask about my health</h1>
          <p className="mt-1 text-ink-600">Answers come from your record and from trusted health sites.</p>
        </div>
        {thread.length > 0 && !busy && (
          <button type="button" className="link-quiet" onClick={clear}>Clear conversation</button>
        )}
      </div>

      <div className="space-y-5" role="log" aria-label="Conversation" aria-live="polite">
        {empty && (
          <div className="card space-y-3">
            <p className="text-sm text-ink-700">
              Ask about your medicines, allergies, conditions or test results, or about food, diet and daily life.
            </p>
            <div className="flex flex-wrap gap-2" aria-label="Examples">
              {EXAMPLES.map((ex) => (
                <button key={ex} type="button" className="btn-light min-h-11 text-left whitespace-normal" onClick={() => void ask(ex)}>
                  {ex}
                </button>
              ))}
            </div>
          </div>
        )}

        {thread.map((t, i) => (
          <div key={i} ref={i === thread.length - 1 && !busy ? newest : undefined} className="scroll-mt-20 space-y-3">
            <Bubble text={t.question} />
            <Answer answer={t.answer} />
          </div>
        ))}

        {pending !== null && (
          <div ref={newest} className="scroll-mt-20 space-y-3">
            <Bubble text={pending} />
            <div className="card flex items-center gap-2 text-sm text-ink-700" role="status">
              <Loader2 size={16} className="shrink-0 animate-spin" aria-hidden />
              Checking your record and trusted sites. This can take up to 30 seconds.
            </div>
          </div>
        )}
        <ErrorNote error={error} />
      </div>

      <form
        className="card sticky bottom-3 z-10 p-2 sm:p-3"
        onSubmit={submit}
        aria-label="Ask a question"
      >
        <div className="flex flex-wrap items-center gap-2">
          <label className="sr-only" htmlFor="ask-q">Your question</label>
          <input
            id="ask-q"
            ref={box}
            className="input min-h-11 min-w-0 flex-1"
            maxLength={500}
            placeholder={thread.length ? "Ask a follow-up" : "Ask about your record, food or daily life"}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            readOnly={busy}
          />
          <VoiceButton compact patientId={me.id} disabled={busy} onText={(t) => setQuestion((cur) => appendText(cur, t))} />
          <button className="btn min-h-11 shrink-0" type="submit" disabled={busy || !question.trim()}>
            {busy ? <Loader2 size={16} className="animate-spin" aria-hidden /> : <ArrowUp size={16} aria-hidden />} Ask
          </button>
        </div>
      </form>
    </div>
  );
}

/** The patient's question, on the right. */
function Bubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <p className="max-w-[85%] rounded-2xl rounded-br-md bg-lagoon-700 px-4 py-2.5 text-sm break-words text-white">
        <span className="sr-only">You asked: </span>
        {text}
      </p>
    </div>
  );
}

/** Answers saved before sources were numbered have no `sources`: one entry per link the points carry. */
function sourcesOf(a: AskAnswer): AskSource[] {
  if (a.sources && a.sources.length > 0) return a.sources;
  const out: AskSource[] = [];
  for (const p of a.points) {
    if (p.url && !out.some((s) => s.url === p.url)) {
      out.push({ n: out.length + 1, site: p.site, title: p.title, url: p.url });
    }
  }
  return out;
}

function Answer({ answer: a }: { answer: AskAnswer }) {
  const sources = a.kind === "record" ? [] : sourcesOf(a);
  const recordItems = a.record_items ?? [];
  const charts = a.charts ?? [];
  const hasRecord = recordItems.length > 0 || !!a.record_summary;
  const note = a.kind === "record" && !a.note
    ? "From your confirmed record. Ask your doctor before changing any medicine."
    : a.note;
  if (a.urgent) return <EmergencyNow signs={a.warning_signs} helpline={a.helpline} />;
  if (a.kind === "not_health" || (!a.kind && !a.health_question)) {
    return (
      <div className="card text-sm text-ink-700">
        This is not a health question, so there is nothing to look up. Try asking about your record, food, diet or daily life.
      </div>
    );
  }

  const fromRecord = a.kind === "record" || hasRecord;
  const fromWeb = a.points.length > 0 || a.kind === "general" || (a.kind === "both" && !hasRecord);
  const nothingInRecord = a.kind === "record" && !hasRecord;
  return (
    <div className="card space-y-4">
      <p className="flex items-center gap-1.5 text-xs font-bold tracking-wide text-ink-600 uppercase">
        {fromRecord && !fromWeb ? <FileText size={14} aria-hidden /> : <Globe size={14} aria-hidden />}
        {fromRecord && fromWeb ? "From your record and trusted health sites" : fromRecord ? "From your record" : "From trusted health sites"}
      </p>

      {a.allergy_notes.length > 0 && (
        <ul className="space-y-1 rounded-2xl bg-red-50 px-4 py-3 ring-1 ring-red-200">
          {a.allergy_notes.map((n) => (
            <li key={n} className="flex items-start gap-2 font-semibold text-red-800">
              <ShieldAlert size={18} className="mt-0.5 shrink-0" aria-hidden /> {n}
            </li>
          ))}
        </ul>
      )}

      {nothingInRecord && <NotInRecord note={a.note} />}

      {hasRecord && (
        <section className="space-y-3" aria-label="From your record">
          {a.record_summary && <AiSummary text={a.record_summary} />}
          {charts.map((c) => <ChartBlock key={c.key} chart={c} />)}
          <RecordList items={recordItems} charted={new Set(charts.map((c) => c.key))} />
        </section>
      )}

      {a.points.length === 0 ? (
        !hasRecord && !nothingInRecord && <p className="text-sm text-ink-700">{note}</p>
      ) : (
        <div className="space-y-5">
          {a.summary && <AiSummary text={a.summary} />}
          {SECTIONS.map((sec) => {
            const items = a.points.filter((p) => (p.category ?? "good_to_know") === sec.key);
            if (items.length === 0) return null;
            return (
              <section key={sec.key} aria-label={sec.title}>
                <h3 className="mb-2 flex items-center gap-1.5 text-base font-bold tracking-tight text-ink-900">
                  {sec.icon}
                  <span className={sec.key === "be_careful" ? "text-amber-900" : ""}>{sec.title}</span>
                </h3>
                <ul className="list-disc space-y-3 pl-5 marker:text-ink-400">
                  {items.map((p, i) => <PointRow key={i} point={p} n={p.source ?? sources.find((s) => s.url === p.url)?.n} />)}
                </ul>
              </section>
            );
          })}
        </div>
      )}

      {sources.length > 0 && (
        <section className="space-y-1.5" aria-label="Sources">
          <h3 className="text-xs font-bold tracking-wide text-ink-600 uppercase">Sources</h3>
          <ol className="space-y-1 text-sm text-ink-700">
            {sources.map((src) => (
              <li key={src.n} className="flex gap-2">
                <span className="w-5 shrink-0 text-right text-ink-500">{src.n}.</span>
                <span className="min-w-0">
                  <span className="font-semibold">{src.site}</span>,{" "}
                  <a className="inline-flex items-center gap-1 break-words text-tide-700 underline" href={src.url} target="_blank" rel="noopener noreferrer">
                    {src.title} <ExternalLink size={12} aria-hidden />
                  </a>
                </span>
              </li>
            ))}
          </ol>
        </section>
      )}

      {a.considered.length > 0 && (
        <p className="text-sm text-ink-700">Searched with your condition: {a.considered.join(", ")}</p>
      )}
      {(a.points.length > 0 || hasRecord) && note && (
        <p className="rounded-2xl bg-ink-100 px-4 py-3 text-sm text-ink-700">{note}</p>
      )}
    </div>
  );
}

/** Nothing in the record answers a question about it: say so, and how to put it there. */
function NotInRecord({ note }: { note: string }) {
  const [head, ...rest] = (note || "").split(/(?<=\.)\s+/);
  return (
    <div className="space-y-3">
      <p className="font-semibold text-ink-900">{head || "This hasn't been added to your record yet."}</p>
      <p className="text-sm text-ink-700">
        {rest.join(" ") || "Add the visit or report it comes from, and it will show up here."}
      </p>
      <a href="#/add" className="btn min-h-11">
        <Plus size={16} aria-hidden /> Add a visit
      </a>
    </div>
  );
}

function AiSummary({ text }: { text: string }) {
  return (
    <div className="space-y-1 border-l-2 border-l-ink-200 pl-3">
      <span className="chip bg-tide-50 text-tide-800"><Sparkles size={12} aria-hidden /> AI summary</span>
      <p className="text-sm text-ink-800">{text}</p>
    </div>
  );
}

/** One measure the question was about, drawn like My health numbers, with its latest value and the
 * ways on to the reports. The series comes from the patient's own numbers, never from the model. */
function ChartBlock({ chart }: { chart: AskChart }) {
  const several = chart.series.length > 1;
  return (
    <div className="space-y-2 rounded-2xl bg-white/60 p-3 ring-1 ring-ink-100">
      <h3 className="flex items-center gap-1.5 text-base font-bold tracking-tight text-ink-900">
        <LineChart size={16} className="text-ink-600" aria-hidden /> {plainName(chart.name)}
      </h3>
      <div className="space-y-1 text-sm">
        {chart.series.map((s, i) => {
          const p = s.latest;
          if (!p) return null;
          const outside = p.position === "high" || p.position === "low";
          return (
            <p key={`${s.key}-${s.unit}`} className="flex flex-wrap items-center gap-x-2 gap-y-1">
              {several && (
                <span className={`inline-flex items-center gap-1 text-xs font-semibold ${lineTone(i)}`}>
                  <span className="size-2.5 rounded-full bg-current" aria-hidden />
                  <span className="text-ink-700">{lineName(s)}</span>
                </span>
              )}
              <span className="font-bold tabular-nums text-ink-900">
                {shown(p.value)}{s.unit ? <span className="ml-1 text-xs font-semibold text-ink-600">{s.unit}</span> : null}
              </span>
              <span className="text-xs text-ink-600">on {formatDate(p.on)}</span>
              {outside && (
                <span className="chip bg-amber-100 text-amber-900">
                  <AlertTriangle size={12} aria-hidden /> {p.position === "high" ? "Above range" : "Below range"}
                </span>
              )}
            </p>
          );
        })}
      </div>
      <Chart series={chart.series} big />
      <BandNotes series={chart.series} />
      <div className="grid grid-cols-2 gap-2 pt-1">
        {chart.report_link && (
          <a href={`#${chart.report_link}`} className="btn-light min-h-11 min-w-0 px-2 whitespace-normal text-center" aria-label={`Latest report for ${plainName(chart.name)}`}>
            <FileText size={16} aria-hidden /> Latest report
          </a>
        )}
        <a
          href={`#${chart.numbers_link}`}
          className={`btn-light min-h-11 min-w-0 px-2 whitespace-normal text-center ${chart.report_link ? "" : "col-span-2 justify-self-start"}`}
          aria-label={`All readings for ${plainName(chart.name)}, with their reports`}
        >
          <LineChart size={16} aria-hidden /> All readings
        </a>
      </div>
    </div>
  );
}

const KIND_ORDER: { kind: string; title: string }[] = [
  { kind: "allergy", title: "Allergies" },
  { kind: "medicine", title: "Medicines" },
  { kind: "condition", title: "Conditions" },
  { kind: "test", title: "Tests" },
  { kind: "event", title: "Events" },
];
const KIND_ALIAS: Record<string, string> = { diagnosis: "condition" };

/** The record items of an answer, grouped by kind with a small heading each when there is more than one
 * kind. A test whose measure is drawn above is not listed twice. */
function RecordList({ items, charted }: { items: AskRecordItem[]; charted: Set<string> }) {
  const shownItems = items.filter((it) => !(it.kind === "test" && it.group_key && charted.has(it.group_key)));
  if (shownItems.length === 0) return null;
  const kindOf = (it: AskRecordItem) => KIND_ALIAS[it.kind] ?? it.kind;
  const known = new Set(KIND_ORDER.map((k) => k.kind));
  const groups = [
    ...KIND_ORDER.map((k) => ({ ...k, rows: shownItems.filter((it) => kindOf(it) === k.kind) })),
    { kind: "other", title: "Other", rows: shownItems.filter((it) => !known.has(kindOf(it))) },
  ].filter((g) => g.rows.length > 0);
  return (
    <div className="space-y-3">
      {groups.map((g) => (
        <section key={g.kind} aria-label={g.title}>
          {groups.length > 1 && (
            <h3 className="mb-1 text-xs font-bold tracking-wide text-ink-600 uppercase">{g.title}</h3>
          )}
          <ul className="divide-y divide-ink-100">
            {g.rows.map((it) => <RecordRow key={it.id} item={it} />)}
          </ul>
        </section>
      ))}
    </div>
  );
}

function RecordRow({ item }: { item: AskRecordItem }) {
  const allergy = item.kind === "allergy";
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5 first:pt-0 last:pb-0">
      <span className="min-w-0 flex-1 basis-44 text-sm">
        <span className="font-bold break-words text-ink-900">{item.label}</span>
        {allergy && (
          <span className="chip ml-2 bg-red-50 text-red-800 ring-1 ring-red-200"><ShieldAlert size={12} aria-hidden /> Allergy</span>
        )}
        {item.detail && <span className="block break-words text-ink-600">{item.detail}</span>}
      </span>
      <span className="flex flex-wrap gap-2">
        {item.link && (
          <a href={`#${item.link}`} className="btn-light min-h-11 px-3" aria-label={`${item.link_label || "Open"}: ${item.label}`}>
            {item.link_label || "Open"}
          </a>
        )}
        {item.numbers_link && (
          <a href={`#${item.numbers_link}`} className="btn-light min-h-11 px-3" aria-label={`See ${item.label} on My health numbers`}>
            See numbers
          </a>
        )}
      </span>
    </li>
  );
}

const SECTIONS: { key: AskCategory; title: string; icon: ReactNode }[] = [
  { key: "good_to_know", title: "Good to know", icon: <Lightbulb size={16} className="text-ink-600" aria-hidden /> },
  { key: "be_careful", title: "Be careful", icon: <AlertTriangle size={16} className="text-amber-700" aria-hidden /> },
  { key: "your_conditions", title: "With your conditions", icon: <Stethoscope size={16} className="text-ink-600" aria-hidden /> },
];

function PointRow({ point: p, n }: { point: AskPoint; n?: number }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="text-sm text-ink-800">
      {p.text}{" "}
      <button
        type="button"
        className="relative -top-1 -my-1.5 inline-flex min-h-8 min-w-8 items-center justify-center rounded px-2 text-xs font-semibold text-tide-700 underline"
        aria-expanded={open}
        aria-label={`Show the source quote${n ? ` ${n}` : ""}`}
        onClick={() => setOpen((o) => !o)}
      >
        [{n ?? "i"}]
      </button>
      {open && (
        <span className="mt-1.5 block rounded-xl bg-ink-50 px-3 py-2 text-xs text-ink-700 ring-1 ring-ink-100">
          “{p.quote}” <span className="text-ink-500">({p.site})</span>
        </span>
      )}
    </li>
  );
}
