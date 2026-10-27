import { AlertTriangle, ChevronRight, ClipboardList, Loader2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api } from "../api";
import { useMe } from "../App";
import { ErrorNote } from "../components";
import { formatDate, go, useLoad } from "../lib";
import { appendText, VoiceButton } from "../voice";

/** Before a visit: say what you feel, get a summary to hand to the doctor. */
export default function PrepareScreen() {
  const me = useMe();
  const list = useLoad(() => api.visitPreps(me.id), [me.id]);
  const [text, setText] = useState("");
  const [since, setSince] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const prep = await api.createVisitPrep(me.id, text.trim(), since.trim() || null);
      go(`/prepare/${prep.id}`);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="title">Before a visit</h1>
        <p className="mt-1 text-ink-600">Tell us what you feel. Get a summary for your doctor.</p>
      </div>

      <form className="card space-y-4" onSubmit={submit}>
        <div>
          <label className="label" htmlFor="prep-text">What are you feeling?</label>
          <textarea
            id="prep-text"
            className="input min-h-32"
            maxLength={2000}
            value={text}
            onChange={(e) => setText(e.target.value)}
            disabled={busy}
            required
          />
          <div className="mt-2">
            <VoiceButton patientId={me.id} disabled={busy} onText={(t) => setText((cur) => appendText(cur, t))} />
          </div>
        </div>
        <div>
          <label className="label" htmlFor="prep-since">Since when? (optional)</label>
          <input
            id="prep-since"
            className="input"
            maxLength={80}
            placeholder="For example: 3 days"
            value={since}
            onChange={(e) => setSince(e.target.value)}
            disabled={busy}
          />
        </div>
        <button className="btn w-full sm:w-auto" type="submit" disabled={busy || !text.trim()}>
          {busy ? <><Loader2 size={16} className="animate-spin" aria-hidden /> Making your summary…</> : "Make my summary"}
        </button>
        {busy && (
          <p className="text-sm text-ink-600" role="status">This can take up to 30 seconds. Please stay on this page.</p>
        )}
        <ErrorNote error={error} />
      </form>

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
                      <span className="block truncate text-sm text-ink-600">{p.symptoms[0]?.text ?? p.text}</span>
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
