import { ArrowDown, ArrowUp, Printer, ShieldAlert, Sparkles, Stethoscope, Trash2 } from "lucide-react";
import { useState, type ReactNode } from "react";
import { api, type OutOfRangeTest } from "../api";
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
  const tests = r.tests_out_of_range ?? [];
  return (
    <div className="space-y-5">
      <h1 className="title">Summary for your doctor</h1>

      {p.urgent && <EmergencyNow signs={p.warning_signs} helpline={p.helpline} />}

      <section className="card space-y-2 border-l-4 border-l-tide-500 print:hidden" aria-label="Who to see">
        <div className="flex flex-wrap items-center gap-2">
          <Stethoscope size={18} className="text-tide-700" aria-hidden />
          <h2 className="text-xs font-bold tracking-wide text-ink-600 uppercase">Who to see</h2>
          <span className="chip bg-tide-50 text-tide-800"><Sparkles size={12} aria-hidden /> Suggested</span>
        </div>
        <p className="text-2xl font-bold tracking-tight text-ink-900">{p.specialty_label}</p>
        {p.reason && <p className="text-sm text-ink-700">{p.reason}</p>}
        <p className="text-xs text-ink-600">A suggestion, not a diagnosis. A general physician can always help you decide.</p>
      </section>

      <article className="card-solid print-area text-sm text-ink-800" aria-label="For the doctor">
        <div className="mb-3">
          <h2 className="text-lg font-bold tracking-tight">For the doctor</h2>
          <p className="text-sm text-ink-600">{formatDate(p.created_at)}</p>
        </div>

        {p.urgent && p.warning_signs.length > 0 && (
          <p className="mb-3 font-semibold">Warning signs I described: {p.warning_signs.join(", ")}</p>
        )}

        <dl className="divide-y divide-ink-100">
          <Row label="Symptoms">
            {p.symptoms.length ? (
              <ul className="list-disc space-y-0.5 pl-5">{p.symptoms.map((s, i) => <li key={i}>{s.text}</li>)}</ul>
            ) : <None />}
          </Row>
          <Row label="Since">{p.since ? <p>{p.since}</p> : <None />}</Row>

          <Row label="Allergies">
            {r.allergies.length ? (
              <ul className="flex flex-wrap gap-1.5" aria-label="Allergies">
                {r.allergies.map((a) => (
                  <li key={a} className="chip bg-red-50 text-red-800 ring-1 ring-red-200">
                    <ShieldAlert size={12} aria-hidden /> Allergy: {a}
                  </li>
                ))}
              </ul>
            ) : <None />}
          </Row>
          <Row label="Medicines"><Chips items={r.medicines} /></Row>
          <Row label="Conditions"><Chips items={r.conditions} /></Row>
          <Row label="Recent events">
            {r.events.length > 0 ? (
              <ul className="list-disc space-y-0.5 pl-5">{r.events.map((x, i) => <li key={i}>{x}</li>)}</ul>
            ) : <None />}
          </Row>
          <Row label="Tests out of range">
            {tests.length > 0 ? <TestsTable tests={tests} />
              : r.out_of_range.length > 0 ? (
                <ul className="list-disc space-y-0.5 pl-5">{r.out_of_range.map((x, i) => <li key={i}>{x}</li>)}</ul>
              ) : <None />}
          </Row>

          <Row label="Questions">
            {p.questions.length ? (
              <ol className="list-decimal space-y-0.5 pl-5">{p.questions.map((q, i) => <li key={i}>{q}</li>)}</ol>
            ) : <None />}
          </Row>
        </dl>

        {/* Collapsed on screen; printed in full as a second copy of the text. */}
        <details className="mt-3 border-t border-ink-100 pt-3 print:hidden">
          <summary className="min-h-8 cursor-pointer text-xs font-bold tracking-wide text-ink-600 uppercase">In my words</summary>
          <p className="mt-1 whitespace-pre-wrap">{p.text}</p>
        </details>
        <div className="mt-3 hidden border-t border-ink-100 pt-3 print:block">
          <p className="text-xs font-bold tracking-wide text-ink-600 uppercase">In my words</p>
          <p className="mt-1 whitespace-pre-wrap">{p.text}</p>
        </div>

        <p className="mt-3 border-t border-ink-100 pt-3 text-xs text-ink-500">
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

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[30%_minmax(0,1fr)] gap-3 py-3">
      <dt className="text-xs font-bold tracking-wide text-ink-600 uppercase">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  );
}

const None = () => <p className="text-ink-500">None recorded</p>;

function Chips({ items }: { items: string[] }) {
  if (!items.length) return <None />;
  return (
    <ul className="flex flex-wrap gap-1.5">
      {items.map((x, i) => <li key={i} className="chip bg-ink-100 text-ink-800">{x}</li>)}
    </ul>
  );
}

function TestsTable({ tests }: { tests: OutOfRangeTest[] }) {
  return (
    <div>
      <div className="hidden grid-cols-[1.3fr_1fr_0.9fr_1fr_0.8fr] gap-2 border-b border-ink-100 pb-1 text-xs font-semibold text-ink-600 sm:grid" aria-hidden>
        <span>Test</span><span>Value</span><span>Result</span><span>Printed range</span><span>Date</span>
      </div>
      <ul className="divide-y divide-ink-100">
        {tests.map((t, i) => {
          const high = t.direction === "high";
          return (
            <li key={i} className="py-2 first:pt-0">
              {/* Phone: two lines, name and result, then range and date */}
              <div className="sm:hidden">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="font-semibold">{t.name}</span>
                  <span className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap">
                    {t.value}{t.unit ? ` ${t.unit}` : ""}
                    <span className="inline-flex items-center gap-0.5 font-semibold text-amber-900">
                      {high ? <ArrowUp size={14} aria-hidden /> : <ArrowDown size={14} aria-hidden />}
                      {high ? "High" : "Low"}
                    </span>
                  </span>
                </div>
                <div className="text-sm text-ink-600">
                  {[t.range_text ? `Range ${t.range_text}` : "", t.date ? formatDate(t.date) : ""].filter(Boolean).join(" · ")}
                </div>
              </div>
              <div className="hidden gap-x-2 sm:grid sm:grid-cols-[1.3fr_1fr_0.9fr_1fr_0.8fr] sm:items-center">
                <span className="font-semibold">{t.name}</span>
                <span>{t.value}{t.unit ? ` ${t.unit}` : ""}</span>
                <span className="inline-flex items-center gap-1 font-semibold text-amber-900">
                  {high ? <ArrowUp size={14} aria-hidden /> : <ArrowDown size={14} aria-hidden />}
                  {high ? "High" : "Low"}
                </span>
                <span className="text-ink-600">{t.range_text ? `Range ${t.range_text}` : ""}</span>
                <span className="text-ink-600">{t.date ? formatDate(t.date) : ""}</span>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
