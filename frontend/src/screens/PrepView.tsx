import { Printer, ShieldAlert, Sparkles, Trash2 } from "lucide-react";
import { useState, type ReactNode } from "react";
import { api } from "../api";
import { EmergencyNow } from "../assist";
import { ErrorNote } from "../components";
import { formatDate, go, useLoad } from "../lib";

/** One saved summary for the doctor: who to see, the summary itself (printable), Print, Delete. */
export default function PrepViewScreen({ prepId }: { prepId: string }) {
  const { data: p, error } = useLoad(() => api.visitPrep(prepId), [prepId]);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const remove = async () => {
    if (!window.confirm("Delete this summary? This cannot be undone.")) return;
    setBusy(true);
    setProblem(null);
    try {
      await api.deleteVisitPrep(prepId);
      go("/prepare");
    } catch (e) {
      setProblem((e as Error).message);
      setBusy(false);
    }
  };

  if (error) {
    return (
      <div className="space-y-3">
        <ErrorNote error={error} />
        <button className="btn-light" onClick={() => go("/prepare")}>Back to your summaries</button>
      </div>
    );
  }
  if (!p) return <p className="text-ink-500" role="status">Loading…</p>;

  const r = p.record;
  return (
    <div className="space-y-5">
      <h1 className="title">Summary for your doctor</h1>

      {p.urgent && <EmergencyNow signs={p.warning_signs} helpline={p.helpline} />}

      <section className="card space-y-2 border-l-4 border-l-tide-500 print:hidden" aria-label="Who to see">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="section-title">Who to see</h2>
          <span className="chip bg-tide-50 text-tide-800"><Sparkles size={12} aria-hidden /> Suggested</span>
        </div>
        <p className="text-lg font-semibold">{p.specialty_label}</p>
        {p.reason && <p className="text-sm text-ink-700">{p.reason}</p>}
        <p className="rounded-2xl bg-ink-100 px-4 py-3 text-sm text-ink-700">
          A suggestion, not a diagnosis. A general physician can always help you decide.
        </p>
      </section>

      <article className="card-solid print-area space-y-4" aria-label="Summary for the doctor">
        <div>
          <p className="text-lg font-bold tracking-tight">Summary for the doctor</p>
          <p className="text-sm text-ink-600">{formatDate(p.created_at)}</p>
        </div>

        {p.urgent && p.warning_signs.length > 0 && (
          <p className="font-semibold">Warning signs I described: {p.warning_signs.join(", ")}</p>
        )}

        <Block title="What I feel">
          {p.symptoms.length > 0 && (
            <ul className="list-disc space-y-0.5 pl-5">
              {p.symptoms.map((s, i) => <li key={i}>{s.text}</li>)}
            </ul>
          )}
          <p className="mt-1 whitespace-pre-wrap">
            <span className="font-semibold">In my words: </span>{p.text}
          </p>
        </Block>

        {p.since && (
          <Block title="Since when"><p>{p.since}</p></Block>
        )}

        <Block title="Allergies">
          {r.allergies.length ? (
            <ul className="space-y-1">
              {r.allergies.map((a) => (
                <li key={a} className="flex items-center gap-1.5 font-semibold text-red-700">
                  <ShieldAlert size={16} className="shrink-0" aria-hidden /> Allergy: {a}
                </li>
              ))}
            </ul>
          ) : <p className="text-ink-600">None in my record.</p>}
        </Block>

        <List title="Current medicines" items={r.medicines} none="None in my record." />
        <List title="Conditions" items={r.conditions} none="None in my record." />
        <List title="Recent events" items={r.events} none="None in the last 12 months." />
        <List title="Test values outside range" items={r.out_of_range} none="None in my record." />
        {p.questions.length > 0 && <List title="Questions to ask" items={p.questions} none="" />}

        <p className="border-t border-ink-100 pt-3 text-xs text-ink-500">
          Made with Crata Health from what I typed and my confirmed record. Not a diagnosis.
        </p>
      </article>

      <div className="flex flex-wrap items-center gap-3 print:hidden">
        <button className="btn" onClick={() => window.print()}>
          <Printer size={16} aria-hidden /> Print
        </button>
        <button className="link-quiet" disabled={busy} onClick={remove}>
          <Trash2 size={14} aria-hidden /> Delete this summary
        </button>
      </div>
      <ErrorNote error={problem} />
    </div>
  );
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="text-sm text-ink-800">
      <h3 className="mb-1 text-xs font-bold tracking-wide text-ink-600 uppercase">{title}</h3>
      {children}
    </section>
  );
}

function List({ title, items, none }: { title: string; items: string[]; none: string }) {
  return (
    <Block title={title}>
      {items.length ? (
        <ul className="list-disc space-y-0.5 pl-5">{items.map((x, i) => <li key={i}>{x}</li>)}</ul>
      ) : <p className="text-ink-600">{none}</p>}
    </Block>
  );
}
