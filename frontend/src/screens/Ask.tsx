import { ChevronDown, ExternalLink, Loader2, ShieldAlert } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api, type AskAnswer } from "../api";
import { useMe } from "../App";
import { EmergencyNow } from "../assist";
import { ErrorNote } from "../components";

const EXAMPLES = ["Is papaya good for me?", "What should I eat with high blood pressure?"];
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
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<Saved[]>(() => readSaved(me.id));

  const ask = async (q: string) => {
    const text = q.trim();
    if (!text || busy) return;
    setBusy(true);
    setError(null);
    try {
      const answer = await api.ask(me.id, text);
      const next = [{ question: text, answer }, ...saved].slice(0, KEEP);
      setSaved(next);
      writeSaved(me.id, next);
      setQuestion("");
    } catch (e) {
      setError((e as Error).message);
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
        <ErrorNote error={error} />
      </form>

      {saved.map((s, i) => (
        <section key={i} className="space-y-3" aria-label="Answer">
          <h2 className="section-title">{s.question}</h2>
          <Answer answer={s.answer} />
        </section>
      ))}
    </div>
  );
}

function Answer({ answer: a }: { answer: AskAnswer }) {
  if (a.urgent) return <EmergencyNow signs={a.warning_signs} helpline={a.helpline} />;
  if (!a.health_question) {
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

      {a.points.length === 0 ? (
        <div className="card text-sm text-ink-700">{a.note}</div>
      ) : (
        <ul className="card space-y-4">
          {a.points.map((p, i) => <PointRow key={i} point={p} />)}
        </ul>
      )}

      {a.considered.length > 0 && (
        <p className="text-sm text-ink-700">Searched with your condition: {a.considered.join(", ")}</p>
      )}
      {a.points.length > 0 && <p className="rounded-2xl bg-ink-100 px-4 py-3 text-sm text-ink-700">{a.note}</p>}
    </div>
  );
}

function PointRow({ point: p }: { point: AskAnswer["points"][number] }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="space-y-1.5 text-sm text-ink-800">
      <p>{p.text}</p>
      <p className="flex flex-wrap items-center gap-x-3 text-xs text-ink-600">
        <span className="font-semibold">{p.site}</span>
        <button
          type="button"
          className="inline-flex min-h-8 items-center gap-1 text-tide-700 underline"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
        >
          See the source <ChevronDown size={12} className={open ? "rotate-180" : ""} aria-hidden />
        </button>
      </p>
      {open && (
        <div className="space-y-1 rounded-xl bg-ink-50 px-3 py-2 text-xs text-ink-700 ring-1 ring-ink-100">
          <p>“{p.quote}”</p>
          <a className="inline-flex items-center gap-1 break-all underline" href={p.url} target="_blank" rel="noreferrer">
            {p.site}: {p.title} <ExternalLink size={12} aria-hidden />
          </a>
        </div>
      )}
    </li>
  );
}
