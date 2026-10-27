import { AlertTriangle, ExternalLink, FileText, Lightbulb, Loader2, ShieldAlert, Sparkles, Stethoscope } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { api, type AskAnswer, type AskCategory, type AskPoint, type AskRecordItem, type AskSource } from "../api";
import { useMe } from "../App";
import { EmergencyNow } from "../assist";
import { ErrorNote } from "../components";
import { appendText, VoiceButton } from "../voice";

const EXAMPLES = ["What tablets am I taking for BP?", "Is papaya good for me?", "What should I eat with high blood pressure?"];
const KEEP = 5;
const storeKey = (personId: string) => `crata.askAnswers.${personId}`;

interface Saved { question: string; answer: AskAnswer }

function readSaved(personId: string): Saved[] {
  try {
    const raw = sessionStorage.getItem(storeKey(personId));
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list.slice(0, KEEP) : [];
  } catch {
    return [];
  }
}

function writeSaved(personId: string, list: Saved[]) {
  try {
    sessionStorage.setItem(storeKey(personId), JSON.stringify(list.slice(0, KEEP)));
  } catch {
    // storage blocked: answers just are not kept
  }
}

/** Ask about food, diet or daily life. Answers come from trusted sites and are not stored on the server. */
export default function AskScreen() {
  const me = useMe();
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ text: string; from: "main" | "follow" } | null>(null);
  const [saved, setSaved] = useState<Saved[]>(() => readSaved(me.id));

  const [followUp, setFollowUp] = useState("");

  const ask = async (q: string, previous?: Saved) => {
    const text = q.trim();
    if (!text || busy) return;
    setBusy(true);
    setError(null);
    try {
      const prev = previous && previous.answer.points.length > 0
        ? { question: previous.question, points: previous.answer.points.map((p) => p.text) }
        : null;
      const answer = await api.ask(me.id, text, prev);
      const next = [{ question: text, answer }, ...saved].slice(0, KEEP);
      setSaved(next);
      writeSaved(me.id, next);
      if (previous) setFollowUp("");
      else setQuestion("");
    } catch (e) {
      setError({ text: (e as Error).message, from: previous ? "follow" : "main" });
    } finally {
      setBusy(false);
    }
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    void ask(question);
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="title">Ask about my health</h1>
        <p className="mt-1 text-ink-600">Food, diet and daily life, from trusted health sites.</p>
      </div>

      <form className="card space-y-4" onSubmit={submit}>
        <div>
          <label className="label" htmlFor="ask-q">Ask about food, diet or daily life</label>
          <input
            id="ask-q"
            className="input"
            maxLength={500}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            disabled={busy}
          />
          <div className="mt-2">
            <VoiceButton patientId={me.id} disabled={busy} onText={(t) => setQuestion((cur) => appendText(cur, t))} />
          </div>
        </div>
        <div className="flex flex-wrap gap-2" aria-label="Examples">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              type="button"
              className="btn-light text-left whitespace-normal"
              disabled={busy}
              onClick={() => { setQuestion(ex); void ask(ex); }}
            >
              {ex}
            </button>
          ))}
        </div>
        <button className="btn w-full sm:w-auto" type="submit" disabled={busy || !question.trim()}>
          {busy ? <><Loader2 size={16} className="animate-spin" aria-hidden /> Looking it up…</> : "Ask"}
        </button>
        {busy && <p className="text-sm text-ink-600" role="status">Checking trusted sites. This can take up to 30 seconds.</p>}
        <ErrorNote error={error?.from === "main" ? error.text : null} />
      </form>

      {saved.map((s, i) => (
        <section key={i} className="space-y-3" aria-label="Answer">
          <h2 className="section-title">{s.question}</h2>
          <Answer answer={s.answer} />
          {i === 0 && s.answer.health_question && !s.answer.urgent && (
            <form
              className="card space-y-3"
              onSubmit={(e) => { e.preventDefault(); void ask(followUp, s); }}
            >
              <label className="label" htmlFor="ask-follow">Ask a follow-up</label>
              <input
                id="ask-follow"
                className="input"
                maxLength={500}
                value={followUp}
                onChange={(e) => setFollowUp(e.target.value)}
                disabled={busy}
              />
              <VoiceButton patientId={me.id} disabled={busy} onText={(t) => setFollowUp((cur) => appendText(cur, t))} />
              <button className="btn w-full sm:w-auto" type="submit" disabled={busy || !followUp.trim()}>
                {busy ? <><Loader2 size={16} className="animate-spin" aria-hidden /> Looking it up…</> : "Ask"}
              </button>
              <ErrorNote error={error?.from === "follow" ? error.text : null} />
            </form>
          )}
        </section>
      ))}
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
  const hasRecord = recordItems.length > 0 || !!a.record_summary;
  const note = a.kind === "record" && !a.note
    ? "From your confirmed record. Ask your doctor before changing any medicine."
    : a.note;
  if (a.urgent) return <EmergencyNow signs={a.warning_signs} helpline={a.helpline} />;
  if (a.kind === "not_health" || (!a.kind && !a.health_question)) {
    return (
      <div className="card text-sm text-ink-700">
        This is not a health question, so there is nothing to look up. Try asking about food, diet or daily life.
      </div>
    );
  }
  return (
    <div className="space-y-3">
      {a.allergy_notes.length > 0 && (
        <ul className="space-y-1 rounded-2xl bg-red-50 px-4 py-3 ring-1 ring-red-200">
          {a.allergy_notes.map((n) => (
            <li key={n} className="flex items-start gap-2 font-semibold text-red-800">
              <ShieldAlert size={18} className="mt-0.5 shrink-0" aria-hidden /> {n}
            </li>
          ))}
        </ul>
      )}

      {hasRecord && (
        <section className="card space-y-3" aria-label="From your record">
          <h3 className="flex items-center gap-1.5 text-base font-bold tracking-tight text-ink-900">
            <FileText size={16} className="text-ink-600" aria-hidden /> From your record
          </h3>
          {a.record_summary && <AiSummary text={a.record_summary} />}
          {recordItems.length > 0 && (
            <ul className="divide-y divide-ink-100">
              {recordItems.map((it) => <RecordRow key={it.id} item={it} />)}
            </ul>
          )}
        </section>
      )}

      {a.points.length === 0 ? (
        !hasRecord && <div className="card text-sm text-ink-700">{note}</div>
      ) : (
        <div className="card space-y-5">
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

function AiSummary({ text }: { text: string }) {
  return (
    <div className="space-y-1 border-l-2 border-l-ink-200 pl-3">
      <span className="chip bg-tide-50 text-tide-800"><Sparkles size={12} aria-hidden /> AI summary</span>
      <p className="text-sm text-ink-800">{text}</p>
    </div>
  );
}

const KIND_LABELS: Record<string, string> = {
  medicine: "Medicine", allergy: "Allergy", condition: "Condition", diagnosis: "Condition", test: "Test", event: "Event",
};

function RecordRow({ item }: { item: AskRecordItem }) {
  const allergy = item.kind === "allergy";
  const label = KIND_LABELS[item.kind] ?? item.kind.charAt(0).toUpperCase() + item.kind.slice(1);
  return (
    <li className="flex flex-wrap items-start gap-x-2 gap-y-1 py-2 first:pt-0 last:pb-0">
      <span className={`chip shrink-0 ${allergy ? "bg-red-50 text-red-800 ring-1 ring-red-200" : "bg-ink-100 text-ink-800"}`}>
        {allergy && <ShieldAlert size={12} aria-hidden />} {label}
      </span>
      <span className="min-w-0 flex-1 basis-40 text-sm">
        <span className="font-bold text-ink-900">{item.label}</span>
        {item.detail && <span className="block text-ink-600">{item.detail}</span>}
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
