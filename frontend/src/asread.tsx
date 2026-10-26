import { Pencil } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { api, type Encounter, type Entry, type FlagReason } from "./api";
import {
  DisclosureButton, EditForm, EntryDetails, ErrorNote, FlagNotes, KindIcon, Proof, clashLine, groupByPrefix,
} from "./components";
import { editedByPatient } from "./lib";
import { wasReplaced } from "./media";
import { AddReport, ValueForm, valuesOf } from "./results";

/** Review's "Added from this visit" card: what the patient does not have to confirm (what they
 * reported, tests with their values, advice, follow-ups). It is already in the record, read as it
 * was heard or printed, so each row can be corrected or removed, and a removed row can come back. */
export function AddedFromVisit({ enc, flags, onChange }: {
  enc: Encounter;
  flags: Record<string, FlagReason>;
  onChange: (e: Entry) => void;
}) {
  const mine = enc.entries.filter((e) => e.added_as_read && !wasReplaced(e));
  if (!mine.length) return null;
  const tests = mine.filter((e) => e.kind === "test");
  const testIds = new Set(tests.map((t) => t.id));
  const reported = mine.filter((e) => e.kind === "symptom");
  const advice = mine.filter((e) => e.kind === "note" || e.kind === "follow_up");
  // Values of a test this card does not hold (an older visit's test still waiting, or none:
  // numbers written on a prescription) come last.
  const loose = mine.filter((e) => e.kind === "result" && !e.links.some((l) => testIds.has(l)));
  const count = mine.filter((e) => e.status !== "rejected").length;
  const row = (e: Entry, nested = false, extra?: ReactNode, text?: string) => (
    <AsReadRow key={e.id} entry={e} enc={enc} flag={flags[e.id]} onChange={onChange} nested={nested} text={text}>{extra}</AsReadRow>
  );
  const heading = (title: string, kind: Entry["kind"]) => (
    <h3 className="flex items-center gap-3 px-5 pt-4 pb-1 font-semibold">
      <KindIcon kind={kind} small /> {title}
    </h3>
  );

  return (
    <section className="space-y-3" aria-label="Added from this visit">
      <h2 className="section-title">
        Added from this visit <span className="font-semibold text-ink-400 tabular-nums">{count}</span>
      </h2>
      <div className="card overflow-hidden p-0 ring-1 ring-green-500/40">
        <p className="px-5 pt-4 text-sm text-ink-600">
          Added to your record as heard or printed. Fix anything that is wrong, or remove it.
        </p>
        {reported.length > 0 && (
          <div className="divide-y divide-ink-100">
            {heading("Symptoms you reported", "symptom")}
            {reported.map((e) => row(e))}
          </div>
        )}
        {tests.length > 0 && (
          <div className="divide-y divide-ink-100">
            {heading("Tests", "test")}
            {tests.map((t) => {
              const values = valuesOf(mine, t);
              const live = values.some((v) => v.status !== "rejected");
              return (
                <div key={t.id} className="divide-y divide-ink-100">
                  {row(t, false, t.status !== "rejected" && !live && <AddReport visitId={enc.id} test={t} />)}
                  {values.map((v) => row(v, true))}
                </div>
              );
            })}
          </div>
        )}
        {advice.length > 0 && (
          <div className="divide-y divide-ink-100">
            {heading("Advice and notes", "note")}
            {groupByPrefix(advice, (e) => e.label).map((g) =>
              g.title === null ? (
                row(g.items[0].item)
              ) : (
                <div key={g.title} className="divide-y divide-ink-100">
                  <h4 className="px-5 pt-3 pb-1 text-xs font-bold tracking-wider text-ink-600 uppercase">
                    {g.title}{" "}
                    <span className="text-ink-400 tabular-nums">
                      {g.items.filter((i) => i.item.status !== "rejected").length}
                    </span>
                  </h4>
                  {g.items.map(({ item, text }) => row(item, true, undefined, text))}
                </div>
              ),
            )}
          </div>
        )}
        {loose.length > 0 && (
          <div className="divide-y divide-ink-100">
            {heading("Other results", "result")}
            {loose.map((v) => row(v))}
          </div>
        )}
        <div className="h-3" />
      </div>
    </section>
  );
}

/** One fact read as it is. Proof, Edit and Remove; a removed row is struck through with "Add it
 * after all". Children (such as Add report) sit with the buttons. */
function AsReadRow({ entry, enc, flag, onChange, nested, text, children }: {
  entry: Entry;
  enc: Encounter;
  flag?: FlagReason;
  onChange: (e: Entry) => void;
  nested: boolean;
  text?: string; // what to show instead of the label (the title is shown once above the group)
  children?: ReactNode;
}) {
  // A row the code doubts (a note, a warning, unclear wording) starts with its details open.
  const needsLook = !entry.edits.length && (!!flag || entry.checks.length > 0);
  const [editing, setEditing] = useState(false);
  const [showDetails, setShowDetails] = useState(needsLook);
  const [showProof, setShowProof] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const detailsId = useId();
  const proofId = useId();
  const isValue = entry.kind === "result";
  const pad = nested ? "ml-5 border-l-2 border-ink-100 pl-4 pr-5" : "px-5";

  const act = async (body: Record<string, unknown>) => {
    setBusy(true);
    setError(null);
    try {
      onChange(await api.entryAction(entry.id, body));
      setEditing(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (entry.status === "rejected") {
    return (
      <div className={`flex flex-wrap items-center gap-3 py-3 ${pad}`}>
        <span className="font-semibold text-ink-600 line-through decoration-ink-300">{text ?? entry.label}</span>
        <button className="btn-light ml-auto py-1.5" disabled={busy} onClick={() => act({ action: "confirm" })}>
          Add back
        </button>
        <ErrorNote error={error} />
      </div>
    );
  }
  return (
    <article className={`py-2 ${pad}`}>
      {/* One line: the kind's icon and the fact. Everything else is one tap away. */}
      <div className="flex items-center gap-3 pt-1">
        {!nested && <KindIcon kind={entry.kind} small />}
        <div className="min-w-0 flex-1">
          <p className={`${nested ? "text-base" : "text-lg"} font-bold tracking-tight`}>{text ?? entry.label}</p>
          <EntryDetails entry={entry} />
        </div>
        <DisclosureButton
          label="Show details"
          variant="icon"
          open={showDetails}
          name={entry.label}
          controls={detailsId}
          onToggle={() => setShowDetails(!showDetails)}
        />
      </div>
      {/* A warning stays on the collapsed line, whatever the details panel is doing. */}
      {!entry.edits.length && (
        <div className="mt-1 space-y-2">
          <FlagNotes entry={entry} flag={flag} clash={clashLine(enc, entry.id)} />
        </div>
      )}
      {children && <div className="mt-1 flex flex-wrap gap-2">{children}</div>}
      <div className="flex flex-wrap items-center gap-x-5">
        <DisclosureButton
          label="See where this came from"
          name={entry.label}
          open={showProof}
          controls={proofId}
          onToggle={() => setShowProof(!showProof)}
        />
      </div>
      <div id={detailsId} hidden={!showDetails}>
        {showDetails && (
          <div className="space-y-2 pb-2">
            <div className="flex flex-wrap items-center gap-2">
              {editedByPatient(entry) && <span className="chip bg-amber-100 text-amber-900">Edited by you</span>}
              {!entry.edits.length && (
                <span className="chip bg-ink-100 text-ink-700">{isValue ? "Read from the document" : "Read from the visit"}</span>
              )}
            </div>
            {editing ? (
              isValue ? (
                <ValueForm entry={entry} busy={busy} onSave={(body) => act({ action: "edit", ...body })} onCancel={() => setEditing(false)} />
              ) : (
                <EditForm entry={entry} busy={busy} onSave={(body) => act({ action: "edit", ...body })} onCancel={() => setEditing(false)} />
              )
            ) : (
              <div className="flex flex-wrap gap-2">
                <button className="btn-light py-1.5" disabled={busy} onClick={() => setEditing(true)} aria-label={`Edit ${entry.label}`}>
                  <Pencil size={14} aria-hidden /> Edit
                </button>
                <button className="btn-light py-1.5" disabled={busy} onClick={() => act({ action: "reject" })} aria-label={`Remove ${entry.label}`}>
                  Remove
                </button>
              </div>
            )}
          </div>
        )}
      </div>
      <ErrorNote error={error} />
      <div id={proofId} hidden={!showProof}>
        {showProof && <Proof entry={entry} sources={enc.sources} />}
      </div>
    </article>
  );
}
