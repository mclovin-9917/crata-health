import {
  AlertTriangle, Check, CircleCheck, Info, Loader2, Pencil, PartyPopper, ShieldAlert, Sparkles, X,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  api, isEssential, type Discrepancy, type Encounter, type Entry, type FlagReason, type ReviewResult, type SourceItem,
  type SourceType,
} from "../api";
import { AddedFromVisit } from "../asread";
import {
  Disclosure, EditForm, EntryDetails, ErrorNote, FlagNotes, KIND_GROUPS, KindIcon, MatchedFrom, Note, SaidBy, WhereFrom,
  addedToVisit, clashLine, genericLine,
} from "../components";
import { AddToVisit } from "../addToVisit";
import { DeleteVisit } from "../deleteVisit";
import { editedByPatient, formatDate, go, kindLabel, stepLabels, translated, useLoad } from "../lib";
import { isSettled, visitMedia, wasReplaced } from "../media";
import { AddReport, valuesOf } from "../results";
import { STAY_KIND, StayCard } from "../stay";

const POLL_MS = 1500;

export default function EncounterScreen({ encounterId }: { encounterId: string }) {
  const { data: enc, error, reload, setData } = useLoad(() => api.encounter(encounterId), [encounterId]);
  const [mediaFor, setMediaFor] = useState<string | null>(null);
  const mediaReady = mediaFor === encounterId; // the check for this browser's files has finished

  // This browser's copy of the files: for the proof and a careful re-read.
  useEffect(() => {
    visitMedia.restore(encounterId).catch(() => undefined).then(() => setMediaFor(encounterId));
  }, [encounterId]);

  // Once every suggestion is confirmed or rejected, the files are deleted from the browser.
  const settled = enc ? isSettled(enc) : false;
  useEffect(() => {
    if (settled) visitMedia.drop(encounterId);
  }, [settled, encounterId]);

  // Poll while processing (plan: polling, no streaming).
  useEffect(() => {
    if (enc?.status !== "processing") return;
    const t = setTimeout(reload, POLL_MS);
    return () => clearTimeout(t);
  }, [enc, reload]);

  if (error && /not found/i.test(error)) {
    // The visit was deleted (here or in another tab).
    return (
      <div className="card">
        <p>This visit is no longer in your record.</p>
      </div>
    );
  }
  if (error) return <ErrorNote error={error} />;
  if (!enc) return <p className="text-ink-500">Loading…</p>;
  if (enc.status === "processing") return <Processing enc={enc} />;
  if (enc.status === "failed") return <Failed enc={enc} onRestart={reload} />;

  // Functional update: several cards can answer at once, so always patch the latest state.
  const replace = (updated: Entry) => {
    setData((prev) =>
      prev && { ...prev, entries: prev.entries.map((e) => (e.id === updated.id ? updated : e)) },
    );
    // A medicine or an allergy changing can settle a check or start one (a rejected medicine
    // that is on the bill); the server works out which stand.
    // A test turned down (or added back) takes its values along on the server.
    if (updated.kind === "medication" || updated.kind === "allergy" || updated.kind === "test") reload();
  };
  // Confirm or turn down a whole box at once. The server leaves out any row that needs a look of
  // its own, and works the flags and checks out again, so the screen reloads after.
  const bulk = async (ids: string[], action: "confirm" | "reject"): Promise<ReviewResult> => {
    const res = await api.reviewMany(enc.id, action, ids);
    setData((prev) =>
      prev && { ...prev, entries: prev.entries.map((e) => res.entries.find((u) => u.id === e.id) ?? e) },
    );
    reload();
    return res;
  };
  const flags = enc.flagged ?? {};
  // A stay's own entry is shown by its card at the top, not as a suggestion to review. Facts read
  // as they are (what you reported, tests and their values, advice) are in their own card below.
  const shown = enc.entries.filter(
    (e) => !wasReplaced(e) && e.id !== enc.stay?.entry_id && !e.added_as_read,
  );
  const toReview = shown.filter((e) => e.status === "proposed");
  const inRecord = shown.filter((e) => e.status === "confirmed");
  const notAdded = shown.filter((e) => e.status === "rejected");
  const waiting = toReview.filter((e) => isEssential(e.kind)); // in boxes
  const waitingOlder = toReview.filter((e) => !isEssential(e.kind)); // visits read before 2026-10-19
  const readAsIs = enc.entries.some((e) => e.added_as_read && !wasReplaced(e));
  const replaced = enc.entries.filter((e) => wasReplaced(e) && !e.added_as_read).length;
  const row = (e: Entry, showKind: boolean) => (
    <EntryCard key={e.id} entry={e} enc={enc} known={enc.already_in_record?.[e.id]} flag={flags[e.id]} onChange={replace} showKind={showKind} />
  );
  const grouped = (entries: Entry[]) => <KindGroups entries={entries} row={row} />;

  return (
    <div className="space-y-6">
      {enc.stay ? (
        <header className="space-y-3">
          <h1 className="title">{STAY_KIND[enc.stay.kind]}</h1>
          <StayCard enc={enc} onChange={(updated) => setData((prev) => prev && { ...prev, ...updated })} />
          {toReview.length > 0 && <Progress done={shown.length - toReview.length} total={shown.length} />}
        </header>
      ) : (
        <header className="space-y-3">
          <div>
            <VisitHead
              enc={enc}
              onChange={(u) =>
                setData((prev) => prev && {
                  ...prev, title: u.title, title_source: u.title_source,
                  visit_date: u.visit_date, visit_date_source: u.visit_date_source,
                })
              }
            />
            <p className="mt-1 text-ink-600">
              You confirm allergies, medicines, diagnoses and events. The rest is already added, and you can change it.
            </p>
            {toReview.length > 0 && (
              <p className="mt-2 flex items-start gap-2 text-sm font-semibold text-tide-700">
                <Sparkles size={16} className="mt-0.5 shrink-0" aria-hidden />
                Suggested by AI. Not in your record until you confirm.
              </p>
            )}
          </div>
          {toReview.length > 0 && <Progress done={shown.length - toReview.length} total={shown.length} />}
        </header>
      )}

      {enc.discrepancies.length > 0 && <Checks found={enc.discrepancies} enc={enc} />}

      {enc.error && (
        <Note tone="amber" icon={<AlertTriangle size={16} aria-hidden />}>
          {enc.error}. Your suggestions have not changed. You can try again.
        </Note>
      )}

      {shown.length === 0 && !enc.stay && !readAsIs && (
        <div className="card text-ink-600">Nothing to add from this visit.</div>
      )}

      {toReview.length > 0 && (
        <Section title="To review" count={toReview.length}>
          {billOnly(enc) && toReview.some((e) => e.kind === "medication") && (
            <Note icon={<Info size={16} aria-hidden />}>
              This is a pharmacy bill only, so its medicines are suggested below. A bill can include medicines for
              someone else. Confirm only yours.
            </Note>
          )}
          <ReviewBoxes entries={waiting} known={enc.already_in_record ?? {}} flags={flags} row={row} onBulk={bulk} />
          {waitingOlder.length > 0 && grouped(waitingOlder)}
        </Section>
      )}
      {toReview.length === 0 && shown.length > 0 && <AllDone added={inRecord.length} />}
      <AddedFromVisit enc={enc} flags={flags} onChange={replace} />
      {inRecord.length > 0 && (
        <Section title="In your record" count={inRecord.length}>{grouped(inRecord)}</Section>
      )}
      {notAdded.length > 0 && (
        <Section title="Not added" count={notAdded.length}>{grouped(notAdded)}</Section>
      )}

      <AddToVisit encounterId={enc.id} onAdded={reload} />

      <ReadFooter enc={enc} settled={settled} replaced={replaced} mediaReady={mediaReady} onStarted={reload} />
      <div className="flex flex-wrap">
        <DeleteVisit visitId={enc.id} onDeleted={() => go("/record")} link linkLabel="Delete visit" />
      </div>
    </div>
  );
}

const DATE_SOURCE: Record<string, string> = {
  document: "", // the usual case: the date printed on the document, nothing to say
  upload: "Date is the day you uploaded it",
  patient: "Date set by you",
};
const SOURCE_ICON: Record<string, string> = { audio: "Recording", image: "Photo or PDF", text: "Typed notes" };

/** The top of a visit: the date and the place, then the visit's name as the heading. The name is
 * the AI's suggestion (marked) until the patient writes their own; the date can be corrected. */
function VisitHead({ enc, onChange }: { enc: Encounter; onChange: (updated: Encounter) => void }) {
  const [mode, setMode] = useState<"name" | "date" | null>(null);
  const [back, setBack] = useState<"name" | "date" | null>(null); // the button to give focus back to
  const [name, setName] = useState(enc.title ?? "");
  const [day, setDay] = useState(enc.visit_date ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nameButton = useRef<HTMLButtonElement>(null);
  const dateButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (mode === null && back) {
      (back === "name" ? nameButton : dateButton).current?.focus();
      setBack(null);
    }
  }, [mode, back]);

  const open = (m: "name" | "date") => {
    setName(enc.title ?? "");
    setDay(enc.visit_date ?? "");
    setError(null);
    setMode(m);
  };
  const close = () => {
    setBack(mode);
    setMode(null);
  };
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      onChange(mode === "name" ? await api.setVisitTitle(enc.id, name.trim()) : await api.setVisitDate(enc.id, day));
      close();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const form = (children: ReactNode, valid: boolean) => (
    <form className="mt-1 flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); if (valid) void save(); }}>
      {children}
      <button type="submit" className="btn" disabled={!valid || busy}>{mode === "name" ? "Save name" : "Save date"}</button>
      <button type="button" className="btn-light" disabled={busy} onClick={close}>Cancel</button>
    </form>
  );
  const files = enc.sources.filter((s) => !s.reread_of);
  const dateNote = DATE_SOURCE[enc.visit_date_source ?? "upload"];

  return (
    <div>
      {mode === "date" ? (
        form(
          <input type="date" className="input w-auto" value={day} max={new Date().toISOString().slice(0, 10)}
            onChange={(e) => setDay(e.target.value)} aria-label="Visit date" autoFocus />,
          day !== "",
        )
      ) : (
        <p className="flex flex-wrap items-baseline gap-x-2 text-sm font-semibold text-ink-500">
          <span>{formatDate(enc.visit_date)}{enc.facility ? `, ${enc.facility}` : ""}</span>
          <button ref={dateButton} className="link-quiet font-normal" onClick={() => open("date")}>Change date</button>
        </p>
      )}
      {mode !== "date" && dateNote && (
        <p className="text-xs text-ink-500">{dateNote}</p>
      )}
      {mode === "name" ? (
        form(
          <input className="input w-auto min-w-60" value={name} maxLength={60} placeholder="Name this visit"
            onChange={(e) => setName(e.target.value)} aria-label="Visit name" autoFocus />,
          name.trim() !== "",
        )
      ) : (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h1 className="title">{enc.title ?? "Visit"}</h1>
          {enc.title && enc.title_source === "ai" && (
            <span className="chip bg-tide-50 text-tide-800"><Sparkles size={12} aria-hidden /> Suggested</span>
          )}
          <button ref={nameButton} className="link-quiet inline-flex items-center gap-1" onClick={() => open("name")}>
            <Pencil size={14} aria-hidden /> {enc.title ? "Edit name" : "Add a name"}
          </button>
        </div>
      )}
      {files.length > 0 && (
        <p className="mt-1 text-xs text-ink-500">
          {files.map((s) => (
            <span key={s.id} className="mr-3">{SOURCE_ICON[s.source_type] ?? s.source_type}{s.filename ? `: ${s.filename}` : ""}</span>
          ))}
        </p>
      )}
      <ErrorNote error={error} />
    </div>
  );
}

const BACKUP_WORDS: Record<string, string> = {
  reading_documents: "read your documents",
  sorting_documents: "sorted your documents",
  finding_drug_names: "listed the medicine names",
  extracting: "found the facts",
  checking_medicines: "checked the medicine names",
  cross_checking: "checked the medicines against your allergies",
};

/** Only bills were read (no prescription, report, recording or notes), as the server decides
 * in `bills.bill_only`. */
function billOnly(enc: Encounter): boolean {
  const read = enc.sources.filter((s) => s.text);
  const billPage = (s: SourceItem) =>
    s.page_kinds.length === s.page_texts.length && s.page_kinds.length > 0
      ? s.page_kinds.every((k) => k === "bill")
      : s.document_kind === "bill";
  return read.length > 0 && read.every(billPage);
}

/** What a backup model did on this visit, in plain words. */
function backupSteps(enc: Encounter): string[] {
  return Object.entries(enc.models_used ?? {})
    .filter(([, models]) => models.some((m) => m.endsWith("(backup)")))
    .map(([step]) => BACKUP_WORDS[step] ?? step);
}

function Progress({ done, total }: { done: number; total: number }) {
  const pct = Math.round((done / total) * 100);
  return (
    <div className="card space-y-2 p-4">
      <div className="flex items-baseline justify-between text-sm">
        <span className="font-semibold">{done === total ? "All reviewed" : `${total - done} left to review`}</span>
        <span className="text-ink-500 tabular-nums">{done} of {total}</span>
      </div>
      <div
        className="h-2 overflow-hidden rounded-full bg-ink-100"
        role="progressbar"
        aria-valuenow={done}
        aria-valuemin={0}
        aria-valuemax={total}
        aria-label="Suggestions reviewed"
      >
        <div className="h-full rounded-full transition-[width] duration-500 [background:var(--grad-action)]" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function Section({ title, count, children }: { title: string; count: number; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="section-title">
        {title} <span className="font-semibold text-ink-500 tabular-nums">{count}</span>
      </h2>
      {children}
    </section>
  );
}

/** One card per kind of fact (Medicines, Allergies...), its entries as rows inside, so a visit
 * reads as a few blocks instead of one card per fact. The frame says what the rows are:
 * a solid tide rule on the left for AI suggestions, a green ring for facts in the record. */
function KindGroups({ entries, row }: {
  entries: Entry[];
  row: (e: Entry, showKind: boolean) => ReactNode;
}) {
  const groups = KIND_GROUPS.map(([title, kinds]) => ({
    title,
    kinds,
    items: entries.filter((e) => kinds.includes(e.kind)),
  })).filter((g) => g.items.length);
  const status = entries[0]?.status;
  const frame = status === "proposed"
    ? "border-l-4 border-l-tide-500"
    : status === "confirmed" ? "ring-1 ring-green-500/40" : "opacity-75";
  return (
    <div className="space-y-3">
      {groups.map((g) => (
        <section key={g.title} className={`card overflow-hidden p-0 ${frame}`} aria-label={g.title}>
          <h3 className="flex items-center gap-3 px-5 pt-4 pb-1 font-semibold">
            <KindIcon kind={g.kinds[0]} small />
            {g.title}
            <span className="font-semibold text-ink-500 tabular-nums">{g.items.length}</span>
            {status === "proposed" && (
              <span className="chip ml-auto bg-tide-50 text-tide-800"><Sparkles size={12} aria-hidden /> Suggested</span>
            )}
            {status === "confirmed" && (
              <span className="chip ml-auto bg-green-50 text-green-800"><CircleCheck size={12} aria-hidden /> In your record</span>
            )}
          </h3>
          {/* Groups that mix kinds (events; advice with follow-ups) say which on each row. */}
          <div className="divide-y divide-ink-100">{g.items.map((e) => row(e, g.kinds.length > 1))}</div>
        </section>
      ))}
    </div>
  );
}

const CHECK_TITLE: Record<Discrepancy["check"], string> = {
  allergy_clash: "Possible link to an allergy", // amber; a red clash is titled "Possible allergy clash"
  billed_not_prescribed: "On the bill, not prescribed",
  quantity_mismatch: "Quantity doesn't match",
};

/** Cross-check findings, at the top of Review: red for an allergy clash, amber for everything
 * else. Decision support only: the wording says who to ask, never what to take. */
const CHECKS_NOTE =
  "Automatic checks, not medical advice. Ask your doctor or pharmacist what they mean for you.";

function Checks({ found, enc }: { found: Discrepancy[]; enc: Encounter }) {
  const bills = new Set(enc.sources.filter((s) => s.document_kind === "bill").map((s) => s.id));
  // The red clash first, then the bill and quantity checks.
  const ordered = [...found].sort((a, b) => Number(b.severity === "urgent") - Number(a.severity === "urgent"));
  // The "automatic checks" line sits under the red clash card (inside its last one); with no clash it
  // goes under the other cards.
  const lastRed = ordered.reduce((at, d, i) => (d.severity === "urgent" ? i : at), -1);
  return (
    <section className="space-y-3" aria-label="Checks on this visit">
      {ordered.map((d, i) => {
        const red = d.severity === "urgent";
        const quotes = d.provenance.filter((p) => p.quote);
        return (
          <article
            key={d.id}
            className={`space-y-2 rounded-3xl px-5 py-4 ring-1 ${
              red ? "bg-red-50 text-red-950 ring-red-200" : "bg-amber-50 text-amber-950 ring-amber-200"
            }`}
          >
            <h2 className="flex flex-wrap items-center gap-2 font-bold">
              {red ? <ShieldAlert size={20} className="text-red-600" aria-hidden /> : <AlertTriangle size={20} className="text-amber-600" aria-hidden />}
              {red ? "Possible allergy clash" : CHECK_TITLE[d.check]}
              {d.matched_by === "model" && (
                <span className="chip bg-white/70 font-semibold text-ink-700" title="The AI matched this medicine name">
                  <Sparkles size={12} aria-hidden /> Matched by AI
                </span>
              )}
            </h2>
            <p>{d.message}</p>
            {quotes.length > 0 && (
              <Disclosure label="See where this came from">
                <ul className="mb-2 space-y-3 rounded-2xl bg-white/70 p-4">
                  {quotes.map((p, j) => (
                    <li key={j} className="space-y-1">
                      <p className="text-sm text-ink-600">
                        {bills.has(p.source_id) ? "From the bill" : SOURCE_LABEL[p.source_type]}
                      </p>
                      <blockquote className="border-l-2 border-ink-300 pl-3 text-base font-semibold text-ink-900">“{p.quote}”</blockquote>
                    </li>
                  ))}
                </ul>
              </Disclosure>
            )}
            {i === lastRed && <p className="text-sm opacity-80">{CHECKS_NOTE}</p>}
          </article>
        );
      })}
      {lastRed === -1 && <p className="px-1 text-sm text-ink-600">{CHECKS_NOTE}</p>}
    </section>
  );
}

function AllDone({ added }: { added: number }) {
  return (
    <div className="hero space-y-4 sm:flex sm:items-center sm:gap-4 sm:space-y-0">
      <div className="flex min-w-0 flex-1 items-center gap-4">
        <span className="grid size-12 shrink-0 place-items-center rounded-2xl bg-white/20"><PartyPopper size={24} aria-hidden /></span>
        <div className="min-w-0">
          <p className="text-xl font-bold tracking-tight">All reviewed</p>
          <p className="text-white">{added} fact{added === 1 ? "" : "s"} from this visit {added === 1 ? "is" : "are"} now in your record.</p>
        </div>
      </div>
      <button
        className="w-full rounded-xl bg-white px-4 py-2.5 text-sm font-semibold text-tide-700 shadow-lg transition hover:bg-white/90 active:scale-[0.98] sm:w-auto"
        onClick={() => go("/record")}
      >
        See my record
      </button>
    </div>
  );
}

const SOURCE_LABEL: Record<SourceType, string> = {
  audio: "From the recording",
  image: "From a document",
  text: "From your typed notes",
  manual: "Added by you",
};

function EntryCard({ entry, enc, known, flag, onChange, showKind }: {
  entry: Entry;
  enc: Encounter;
  known?: string; // label of the matching fact already in the record
  flag?: FlagReason; // why this row needs a look of its own, if it does
  onChange: (e: Entry) => void;
  showKind: boolean; // the group header names the kind, except for mixed groups
}) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  // The words behind an entry: one "See where this came from" everywhere (the source's name,
  // the quote and its surroundings are inside).
  // Something the patient added has no document or recording behind it, so nothing to show.
  const proofButton = addedToVisit(entry) ? null : <WhereFrom entry={entry} sources={enc.sources} />;

  // Rejected: a quiet row, with a way to change your mind.
  if (entry.status === "rejected") {
    return (
      <div className="space-y-2 px-5 py-3">
        <div className="flex flex-wrap items-center gap-3">
          <span className="font-semibold text-ink-600 line-through decoration-ink-300">{entry.label}</span>
          {showKind && <span className="text-xs text-ink-500">{kindLabel(entry.kind)}</span>}
          <button className="btn-light ml-auto py-1.5" disabled={busy} onClick={() => act({ action: "confirm" })}>
            Add to record
          </button>
        </div>
        <ErrorNote error={error} />
      </div>
    );
  }

  const suggested = entry.status === "proposed";
  return (
    <article className="space-y-4 px-5 py-4">
      {/* "Suggested" or "In your record" is on the group header. "From a document" is in the quote box. */}
      {((entry.said_by && entry.said_by !== "document") || entry.edits.length > 0 || (known && suggested)) && (
        <div className="flex flex-wrap items-center gap-2">
          {entry.said_by !== "document" && <SaidBy speaker={entry.said_by} added={addedToVisit(entry)} />}
          {editedByPatient(entry) && <span className="chip bg-amber-100 text-amber-900">Edited by you</span>}
          {translated(entry) && <span className="chip bg-ink-100 text-ink-700" title="Put into English; the original words are kept">Translated</span>}
          {known && suggested && (
            <span className="chip bg-green-50 text-green-800" title={`Matches "${known}"`}>
              <CircleCheck size={12} aria-hidden /> Already in your record
            </span>
          )}
        </div>
      )}

      <div className="flex gap-3">
        <div className="min-w-0">
          {showKind && <p className="text-xs font-semibold tracking-wide text-ink-500 uppercase">{kindLabel(entry.kind)}</p>}
          <p className="text-xl font-bold tracking-tight">{entry.label}</p>
          {genericLine(entry) && (
            <p className="text-sm font-semibold text-ink-600">
              Contains {genericLine(entry)}
              <MatchedFrom medication={entry.medication} />
            </p>
          )}
          <EntryDetails entry={entry} />
        </div>
      </div>

      {suggested && <FlagNotes entry={entry} flag={flag} clash={clashLine(enc, entry.id)} />}

      {editing ? (
        <EditForm
          entry={entry}
          busy={busy}
          onSave={(body) => act({ action: "edit", ...body })}
          onCancel={() => setEditing(false)}
        />
      ) : suggested ? (
        /* Phone: Confirm on its own row, the other two below. Wider: one row. */
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-[1fr_auto_auto]">
          <button className="btn col-span-2 py-2.5 sm:col-span-1" disabled={busy} onClick={() => act({ action: "confirm" })} aria-label={`Confirm ${entry.label}`}>
            <Check size={16} aria-hidden /> Confirm
          </button>
          <button className="btn-light" disabled={busy} onClick={() => setEditing(true)} aria-label={`Edit ${entry.label}`}>
            <Pencil size={16} aria-hidden /> Edit
          </button>
          <button className="btn-light" disabled={busy} onClick={() => act({ action: "reject" })} aria-label={`Don't add ${entry.label}`}>
            <X size={16} aria-hidden /> Don't add
          </button>
        </div>
      ) : (
        <span className="flex gap-2">
          <button className="btn-light py-1.5" disabled={busy} onClick={() => setEditing(true)}>Edit</button>
          <button className="btn-light py-1.5" disabled={busy} onClick={() => act({ action: "reject" })}>Remove</button>
        </span>
      )}
      {/* The proof stays reachable while editing, so the quote can be checked against the words. */}
      <div className="-mt-2">{proofButton}</div>
      {entry.kind === "test" && !editing && !valuesOf(enc.entries, entry).some((v) => v.status !== "rejected") && (
        <div>
          <AddReport visitId={enc.id} test={entry} />
        </div>
      )}
      <ErrorNote error={error} />
    </article>
  );
}

/** One box per essential kind that has suggestions waiting (Allergies, Diagnosis, Medicines,
 * Events): confirm or turn down the whole box in one tap, or open it to go one by one. A row that
 * needs a look of its own (`flags`: a warning, a note from the checks, unclear wording) is always
 * shown open below and is never covered by the box's buttons. */
function ReviewBoxes({ entries, known, flags, row, onBulk }: {
  entries: Entry[];
  known: Record<string, string>;
  flags: Record<string, FlagReason>;
  row: (e: Entry, showKind: boolean) => ReactNode;
  onBulk: (ids: string[], action: "confirm" | "reject") => Promise<ReviewResult>;
}) {
  const boxes = KIND_GROUPS.map(([title, kinds]) => ({
    title,
    kinds,
    items: entries.filter((e) => kinds.includes(e.kind)),
  })).filter((b) => b.items.length);
  return (
    <div className="space-y-3">
      {boxes.map((b) => (
        <ReviewBox key={b.title} {...b} known={known} flags={flags} row={row} onBulk={onBulk} />
      ))}
    </div>
  );
}

function ReviewBox({ title, kinds, items, known, flags, row, onBulk }: {
  title: string;
  kinds: Entry["kind"][];
  items: Entry[];
  known: Record<string, string>;
  flags: Record<string, FlagReason>;
  row: (e: Entry, showKind: boolean) => ReactNode;
  onBulk: (ids: string[], action: "confirm" | "reject") => Promise<ReviewResult>;
}) {
  const [oneByOne, setOneByOne] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [left, setLeft] = useState(0); // rows the server left for the patient's own look
  const plain = items.filter((e) => !flags[e.id]);
  const needLook = items.filter((e) => flags[e.id]);
  const several = plain.length > 1;
  const others = needLook.length > 0; // "the other 3": the rows below are not covered

  const act = async (action: "confirm" | "reject") => {
    setBusy(true);
    setError(null);
    setLeft(0);
    try {
      const res = await onBulk(plain.map((e) => e.id), action);
      // The warnings are worked out again by the server on each request; one it found since the
      // screen loaded stays for the patient's own look, and the box says so.
      setLeft(res.skipped.filter((s) => s.reason !== "not_waiting").length);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card overflow-hidden border-l-4 border-l-tide-500 p-0" aria-label={title}>
      <h3 className="flex items-center gap-3 px-5 pt-4 pb-1 font-semibold">
        <KindIcon kind={kinds[0]} small />
        {title}
        <span className="font-semibold text-ink-500 tabular-nums">{items.length}</span>
        <span className="chip ml-auto bg-tide-50 text-tide-800"><Sparkles size={12} aria-hidden /> Suggested</span>
      </h3>

      {plain.length > 0 && !oneByOne && (
        <ul className="divide-y divide-ink-100">
          {plain.map((e) => (
            <li key={e.id} className="px-5 py-3">
              <p className="flex flex-wrap items-center gap-x-2 text-lg font-bold tracking-tight">
                {e.label}
                {known[e.id] && (
                  <span className="chip bg-green-50 text-green-800" title={`Matches "${known[e.id]}"`}>
                    <CircleCheck size={12} aria-hidden /> Already in your record
                  </span>
                )}
              </p>
              {genericLine(e) && <p className="text-sm font-semibold text-ink-600">Contains {genericLine(e)}</p>}
              <EntryDetails entry={e} />
            </li>
          ))}
        </ul>
      )}
      {plain.length > 0 && oneByOne && <div className="divide-y divide-ink-100">{plain.map((e) => row(e, kinds.length > 1))}</div>}

      {plain.length > 0 && (
        <div className="flex flex-wrap gap-2 px-5 py-4">
          <button className="btn" disabled={busy} onClick={() => act("confirm")}>
            <Check size={16} aria-hidden />
            {others ? `Confirm the other ${plain.length}` : several ? `Confirm all ${plain.length}` : "Confirm"}
          </button>
          <button className="btn-light" disabled={busy} onClick={() => act("reject")}>
            <X size={16} aria-hidden /> {others ? `Don't add these ${plain.length}` : several ? "Don't add any" : "Don't add"}
          </button>
          <button className="btn-light" aria-expanded={oneByOne} onClick={() => setOneByOne(!oneByOne)}>
            {oneByOne ? "Hide details" : several ? "Review one by one" : "Details and edit"}
          </button>
        </div>
      )}
      <ErrorNote error={error} />
      {left > 0 && (
        <div className="px-5 pb-4">
          <Note tone="amber" icon={<AlertTriangle size={16} aria-hidden />}>
            {left === 1 ? "One fact needs" : `${left} facts need`} a closer look because of a warning or note.{" "}
            {left === 1 ? "It is" : "They are"} open below.
          </Note>
        </div>
      )}

      {needLook.length > 0 && (
        <div className="divide-y divide-ink-100 border-t border-amber-200 bg-amber-50/40">
          <p className="flex items-center gap-2 px-5 pt-3 pb-1 text-sm font-semibold text-amber-900">
            <AlertTriangle size={16} aria-hidden />
            {needLook.length === 1 ? "This one needs a closer look" : `These ${needLook.length} need a closer look`}
          </p>
          {needLook.map((e) => row(e, kinds.length > 1))}
        </div>
      )}
    </section>
  );
}

/** The foot of Review, "How this visit was read": the notes about the reading itself (a backup
 * model, a careful read, suggestions left out, the files kept only in the browser) and "Try a
 * careful read". Plain grey text, behind "More info" (open when a backup model had to be used). */
function ReadFooter({ enc, settled, replaced, mediaReady, onStarted }: {
  enc: Encounter;
  settled: boolean;
  replaced: number;
  mediaReady: boolean; // false until the check for this browser's copy of the files has finished
  onStarted: () => void;
}) {
  const backup = backupSteps(enc);
  const photos = enc.sources.some((s) => s.source_type === "image" && !s.reread_of);
  const fileSources = enc.sources.some((s) => s.source_type === "image" || s.source_type === "audio");
  const filesGone = mediaReady && fileSources && !visitMedia.originals(enc);
  const again = photos && !settled;
  const left = enc.quote_failures.length;
  if (!backup.length && !replaced && !left && !filesGone && !again) return null;
  return (
    <section className="border-t border-ink-200 pt-2" aria-label="How this visit was read">
      <h2 className="text-sm font-semibold text-ink-700">How this visit was read</h2>
      <Disclosure label="More info" defaultOpen={backup.length > 0 || left > 0}>
        <div className="space-y-3 pb-2 text-sm text-ink-600">
          {backup.length > 0 && (
            <p>
              NVIDIA's model was busy, so a backup model on Nebius {backup.join(" and ")}. Look at these suggestions
              closely.
            </p>
          )}
          {replaced > 0 && (
            <p>The careful read replaced {replaced} earlier suggestion{replaced > 1 ? "s" : ""} you had not reviewed yet.</p>
          )}
          {left > 0 && (
            <p>
              {left} suggestion{left > 1 ? "s were" : " was"} left out because their exact words are not in what was
              read or heard: {enc.quote_failures.join(", ")}.
            </p>
          )}
          {filesGone && (
            <p>Your original files are not in this browser. Only the browser you uploaded from keeps them, until you finish reviewing.</p>
          )}
          {again && <ReadAgain enc={enc} onStarted={onStarted} filesGoneShown={filesGone} />}
        </div>
      </Disclosure>
    </section>
  );
}

/** Documents are read quickly with thinking off. This asks for a slow, careful read with
 * thinking on, from this browser's copy of the files. */
function ReadAgain({ enc, onStarted, filesGoneShown }: { enc: Encounter; onStarted: () => void; filesGoneShown: boolean }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const photos = enc.sources.filter((s) => s.source_type === "image" && !s.reread_of).length;
  if (photos === 0) return null; // a careful read only changes how documents are read
  const files = visitMedia.originals(enc);
  // "Your original files are not in this browser" above already says it cannot be done here.
  if (!files && filesGoneShown) return null;

  const start = async () => {
    if (!files) return;
    setBusy(true);
    setError(null);
    try {
      await api.reread(enc.id, files.map((f) => ({ sourceId: f.sourceId, file: f.file })));
      onStarted();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <p>
        {enc.sources.some((s) => s.careful)
          ? "Your documents had a careful read. If something still looks wrong, you can ask for another."
          : "Your documents were read quickly. If something is missing or wrong, try a careful read. It takes about two minutes per page and can catch small print."}
      </p>
      {!files ? (
        <p>A careful read needs your original files. Only the browser you uploaded from has them.</p>
      ) : asking ? (
        <div className="space-y-2 rounded-2xl bg-ink-50/80 p-3 ring-1 ring-ink-100">
          <p className="text-sm text-ink-700">
            This replaces suggestions you have not reviewed. What you confirmed or rejected stays.
          </p>
          <div className="flex flex-wrap gap-2">
            <button className="btn" disabled={busy} onClick={start}>
              <Sparkles size={16} aria-hidden /> {busy ? "Starting…" : "Start careful read"}
            </button>
            <button className="btn-light" disabled={busy} onClick={() => setAsking(false)}>Cancel</button>
          </div>
        </div>
      ) : (
        <button className="btn-light" onClick={() => setAsking(true)}>
          <Sparkles size={16} aria-hidden /> Try a careful read
        </button>
      )}
      <ErrorNote error={error} />
    </div>
  );
}

function Failed({ enc, onRestart }: { enc: Encounter; onRestart: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const files = visitMedia.originals(enc);
  const retry = async () => {
    if (!files) return;
    setError(null);
    try {
      await api.reread(enc.id, files.map((f) => ({ sourceId: f.sourceId, file: f.file })), false);
      onRestart();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <div className="card space-y-3">
      <h1 className="section-title text-red-700">Could not read this visit</h1>
      {enc.error?.startsWith("transcribing") ? (
        <p className="text-sm">
          Recordings need NVIDIA's model, and it is not answering right now. Try again in a few minutes, or
          type the key points as notes.
        </p>
      ) : (
        <p className="text-sm">Something went wrong while reading it. Try again, or add the visit again.</p>
      )}
      {enc.error && (
        <Disclosure label="More info">
          <p className="pb-2 text-sm text-ink-600">{enc.error}</p>
        </Disclosure>
      )}
      <div className="flex flex-wrap gap-2">
        {files && <button className="btn" onClick={retry}>Try again</button>}
        <button className="btn-light" onClick={() => go("/add")}>Add the visit again</button>
      </div>
      <div className="flex flex-wrap">
        <DeleteVisit visitId={enc.id} onDeleted={() => go("/record")} link linkLabel="Delete visit" />
      </div>
      <ErrorNote error={error} />
    </div>
  );
}

/** The steps this visit goes through, in order, from what was uploaded. */
function expectedSteps(enc: Encounter): string[] {
  const has = (t: SourceType) => enc.sources.some((s) => s.source_type === t);
  const careful = enc.sources.some((s) => s.careful && !s.text);
  const audio = has("audio") && (!careful || enc.sources.some((s) => s.careful && s.source_type === "audio"));
  const steps: string[] = [];
  if (audio) steps.push("preparing_audio");
  if (has("image")) steps.push("reading_documents", "sorting_documents");
  if (audio && has("image")) steps.push("finding_drug_names");
  if (audio) steps.push("transcribing");
  steps.push("extracting", "checking_medicines", "cross_checking");
  return steps;
}

function Processing({ enc }: { enc: Encounter }) {
  const steps = expectedSteps(enc);
  const current = enc.step ? steps.indexOf(enc.step) : -1;
  const careful = enc.sources.some((s) => s.careful && !s.text);
  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm font-semibold text-ink-500">{careful ? "Careful read" : enc.stay ? STAY_KIND[enc.stay.kind] : "New visit"}</p>
        <h1 className="title">{enc.stay ? "Reading your documents" : "Reading your visit"}</h1>
        <p className="mt-1 text-ink-600">
          {careful ? "A careful read takes about two minutes per page." : "This usually takes under a minute."} You can leave
          this page. It keeps going.
        </p>
      </div>
      <ol className="card space-y-1 p-3">
        {steps.map((s, i) => {
          const done = current === -1 ? s in enc.step_seconds : i < current;
          const now = i === current;
          return (
            <li key={s} className={`flex items-center gap-3 rounded-xl px-3 py-2.5 ${now ? "bg-tide-50" : ""}`}>
              <span
                className={`grid size-7 shrink-0 place-items-center rounded-full ${
                  done ? "bg-lagoon-700 text-white" : now ? "text-tide-700" : "bg-ink-100 text-ink-500"
                }`}
              >
                {done ? <Check size={15} aria-hidden /> : now ? <Loader2 size={18} className="motion-safe:animate-spin" aria-hidden /> : <span className="text-xs font-bold">{i + 1}</span>}
              </span>
              <span className={`flex-1 ${now ? "font-semibold text-ink-900" : done ? "text-ink-700" : "text-ink-500"}`}>
                {stepLabels[s] ?? s}
              </span>
              {done && enc.step_seconds[s] !== undefined && (
                <span className="text-xs text-ink-500 tabular-nums">{Math.round(enc.step_seconds[s])} s</span>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
