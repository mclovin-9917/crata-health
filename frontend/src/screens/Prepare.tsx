import { AlertTriangle, Check, ChevronRight, ClipboardList, Loader2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api, type PrepAnswer, type PrepQuestions } from "../api";
import { useMe } from "../App";
import { EmergencyNow } from "../assist";
import { ErrorNote } from "../components";
import { capFirst, formatDate, go, useLoad } from "../lib";
import { appendText, VoiceButton } from "../voice";

/** Before a visit: say what you feel, get a summary to hand to the doctor. */
export default function PrepareScreen() {
  const me = useMe();
  const list = useLoad(() => api.visitPreps(me.id), [me.id]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [qs, setQs] = useState<PrepQuestions | null>(null);
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const [typed, setTyped] = useState<Record<string, string>>({});

  const make = async (withAnswers: boolean) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const answers: PrepAnswer[] = [];
      if (withAnswers && qs) {
        for (const q of qs.questions) {
          const a = (typed[q.id] ?? "").trim() || chosen[q.id] || "";
          if (a) answers.push({ question: q.text, answer: a });
        }
      }
      const prep = await api.createVisitPrep(me.id, text.trim(), answers);
      go(`/prepare/${prep.id}`);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  const next = async (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.prepQuestions(me.id, text.trim());
      setChosen({});
      setTyped({});
      if (!res.urgent && res.questions.length === 0) {
        const prep = await api.createVisitPrep(me.id, text.trim(), []);
        go(`/prepare/${prep.id}`);
        return;
      }
      setQs(res);
      setBusy(false);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  const backToEdit = () => { setQs(null); setError(null); };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="title">Before a visit</h1>
        <p className="mt-1 text-ink-600">Tell us what you feel. Get a summary for your doctor.</p>
      </div>

      {!qs && (
        <form className="card space-y-4" onSubmit={next}>
          <div>
            <label className="label" htmlFor="prep-text">What are you feeling, and since when?</label>
            <textarea
              id="prep-text"
              className="input min-h-32"
              maxLength={2000}
              placeholder="For example: knee pain and swelling for a week, worse on the stairs"
              value={text}
              onChange={(e) => setText(e.target.value)}
              disabled={busy}
              required
            />
            <div className="mt-2">
              <VoiceButton patientId={me.id} disabled={busy} onText={(t) => setText((cur) => appendText(cur, t))} />
            </div>
          </div>
          <button className="btn w-full sm:w-auto" type="submit" disabled={busy || !text.trim()}>
            {busy ? <><Loader2 size={16} className="animate-spin" aria-hidden /> Checking…</> : "Next"}
          </button>
          {busy && (
            <p className="text-sm text-ink-600" role="status">This can take up to 30 seconds. Please stay on this page.</p>
          )}
          <ErrorNote error={error} />
        </form>
      )}

      {qs && (
        <div className="space-y-4">
          <p className="rounded-2xl bg-ink-100 px-4 py-3 text-sm text-ink-700">
            <span className="font-semibold">You wrote:</span> {text}
            {!busy && <> <button type="button" className="link-quiet inline" onClick={backToEdit}>Edit</button></>}
          </p>

          {qs.urgent && <EmergencyNow signs={qs.warning_signs} helpline={qs.helpline} />}

          {qs.questions.length > 0 && (
            <section className="card space-y-5" aria-label="A few quick questions">
              <div>
                <h2 className="section-title">A few quick questions</h2>
                <p className="text-sm text-ink-600">These help choose the right doctor. Answer what you can.</p>
              </div>
              {qs.questions.map((q) => (
                <div key={q.id} className="space-y-2">
                  <p id={`pq-${q.id}`} className="font-semibold text-ink-900">{q.text}</p>
                  <div className="flex flex-wrap gap-2" role="group" aria-labelledby={`pq-${q.id}`}>
                    {q.choices.map((c) => {
                      const on = chosen[q.id] === c && !(typed[q.id] ?? "").trim();
                      return (
                        <button
                          key={c}
                          type="button"
                          aria-pressed={on}
                          disabled={busy}
                          className={`${on ? "btn" : "btn-light"} min-h-11 whitespace-normal text-left`}
                          onClick={() => {
                            setChosen((cur) => ({ ...cur, [q.id]: on ? "" : c }));
                            setTyped((cur) => ({ ...cur, [q.id]: "" }));
                          }}
                        >
                          {on && <Check size={14} aria-hidden />} {c}
                        </button>
                      );
                    })}
                  </div>
                  <label className="sr-only" htmlFor={`pt-${q.id}`}>Or type your answer</label>
                  <input
                    id={`pt-${q.id}`}
                    className="input"
                    maxLength={200}
                    placeholder="Or type your answer"
                    value={typed[q.id] ?? ""}
                    disabled={busy}
                    onChange={(e) => {
                      setTyped((cur) => ({ ...cur, [q.id]: e.target.value }));
                      if (e.target.value) setChosen((cur) => ({ ...cur, [q.id]: "" }));
                    }}
                  />
                </div>
              ))}
            </section>
          )}

          <div className="flex flex-wrap items-center gap-3">
            {qs.questions.length > 0 && !qs.urgent && (
              <button className="btn w-full sm:w-auto" type="button" disabled={busy} onClick={() => void make(true)}>
                {busy ? <><Loader2 size={16} className="animate-spin" aria-hidden /> Making your summary…</> : "Make my summary"}
              </button>
            )}
            {qs.questions.length === 0 && (
              <button className="btn-light w-full sm:w-auto" type="button" disabled={busy} onClick={() => void make(false)}>
                {busy ? <><Loader2 size={16} className="animate-spin" aria-hidden /> Making your summary…</> : "Make my summary anyway"}
              </button>
            )}
            {qs.questions.length > 0 && (
              <button className="link-quiet" type="button" disabled={busy} onClick={() => void make(false)}>
                Skip questions
              </button>
            )}
          </div>
          {busy && (
            <p className="text-sm text-ink-600" role="status">This can take up to 30 seconds. Please stay on this page.</p>
          )}
          <ErrorNote error={error} />
        </div>
      )}

      <section className="space-y-3">
        <h2 className="section-title">Your summaries</h2>
        <ErrorNote error={list.error} />
        {list.data && list.data.length === 0 && (
          <div className="card text-sm text-ink-500">No summaries yet.</div>
        )}
        {list.data && list.data.length > 0 && (
          <ul className="space-y-2">
            {[...list.data]
              .sort((a, b) => b.created_at.localeCompare(a.created_at))
              .map((p) => (
                <li key={p.id}>
                  <button
                    className="card flex w-full items-center gap-3 p-4 text-left transition hover:bg-white/85"
                    onClick={() => go(`/prepare/${p.id}`)}
                  >
                    <span className="tile size-9"><ClipboardList size={18} aria-hidden /></span>
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-x-2">
                        <span className="font-semibold">{formatDate(p.created_at)}</span>
                        <span className="text-sm text-ink-700">{p.specialty_label}</span>
                        {p.urgent && (
                          <span className="inline-flex items-center gap-1 text-xs font-semibold text-red-700">
                            <AlertTriangle size={12} aria-hidden /> Urgent
                          </span>
                        )}
                      </span>
                      <span className="block truncate text-sm text-ink-600">{capFirst(p.symptoms[0]?.text ?? p.text)}</span>
                    </span>
                    <ChevronRight size={20} className="shrink-0 text-ink-500" aria-hidden />
                  </button>
                </li>
              ))}
          </ul>
        )}
      </section>
    </div>
  );
}
