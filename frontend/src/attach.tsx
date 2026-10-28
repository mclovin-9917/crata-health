import { Sparkles } from "lucide-react";
import { useState } from "react";
import { api, type AttachTarget, type Encounter } from "./api";
import { ErrorNote } from "./components";
import { formatDate, go } from "./lib";
import { visitMedia } from "./media";

/** "CBC", "CBC and HbA1c", "CBC, HbA1c and TSH". */
function plainList(items: string[]): string {
  return items.length < 2 ? (items[0] ?? "") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

/** Smart attach: a new visit made only of reports may be the report for a test an earlier visit ordered
 * and that is still waiting for one. The AI only suggests it (the "Suggested" marker and the tide rule);
 * the patient adds it there or keeps the visit as its own. */
export function AttachCard({ enc, target, onKept }: { enc: Encounter; target: AttachTarget; onKept: () => void }) {
  const [busy, setBusy] = useState<"add" | "keep" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const add = async () => {
    setBusy("add");
    setError(null);
    try {
      const done = await api.attachVisit(enc.id, target);
      // The browser's copy of the files follows their records to the earlier visit (if it still has them).
      await visitMedia.move(enc.id, done.encounter_id).catch(() => undefined);
      go(`/encounters/${done.encounter_id}`);
    } catch (e) {
      const message = (e as Error).message;
      setError(/no longer applies/i.test(message)
        ? "That suggestion no longer fits, so nothing was moved. This visit stays as it is."
        : message);
      setBusy(null);
      if (/no longer applies/i.test(message)) onKept();
    }
  };

  const keep = async () => {
    setBusy("keep");
    setError(null);
    try {
      await api.keepVisit(enc.id);
      onKept();
    } catch (e) {
      setError((e as Error).message);
      setBusy(null);
    }
  };

  const tests = plainList(target.test_labels);
  const where = target.visit_title
    ? `your visit on ${formatDate(target.visit_date)} (${target.visit_title})`
    : `your visit on ${formatDate(target.visit_date)}`;

  return (
    <section className="card space-y-3 border-l-4 border-l-tide-500" aria-label="Add this report to an earlier visit">
      <span className="chip bg-tide-50 text-tide-800"><Sparkles size={12} aria-hidden /> Suggested</span>
      <p className="font-semibold text-ink-900">
        This looks like the report for {tests} from {where}. Add it there?
      </p>
      <p className="text-sm text-ink-600">
        Its numbers, and anything else from this visit, move there. This visit is then removed.
      </p>
      <div className="flex flex-wrap gap-2">
        <button className="btn" disabled={busy !== null} onClick={add}>Add it there</button>
        <button className="btn-light" disabled={busy !== null} onClick={keep}>Keep as its own visit</button>
      </div>
      <ErrorNote error={error} />
    </section>
  );
}
