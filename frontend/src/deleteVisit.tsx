import { AlertTriangle, Trash2 } from "lucide-react";
import { useState } from "react";
import { api, type Removal } from "./api";
import { ErrorNote, Note } from "./components";
import { formatDate } from "./lib";
import { visitMedia } from "./media";

/** "Delete this visit": a quiet button that opens a short confirmation saying what goes. The visit
 * is erased for good (its facts, test values and the text read from its files), so the panel names
 * what leaves the record, and above all what leaves the emergency card. */
export function DeleteVisit({ visitId, onDeleted, link, linkLabel = "Delete visit" }: {
  visitId: string;
  onDeleted: () => void;
  link?: boolean; // a small text link (on a visit card, on Review) instead of a button
  linkLabel?: string; // the link's words
}) {
  const [open, setOpen] = useState(false);
  const [removal, setRemoval] = useState<Removal | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ask = async () => {
    setOpen(true);
    setError(null);
    try {
      setRemoval(await api.removal(visitId));
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const close = () => {
    setOpen(false);
    setRemoval(null);
  };
  const erase = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.deleteVisit(visitId);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
      return;
    }
    // The visit is gone; this browser's copy of its files goes too, and a problem with that must
    // not make the patient try to delete it again.
    await visitMedia.drop(visitId).catch(() => undefined);
    onDeleted();
  };

  if (!open) {
    return link ? (
      <button className="link-quiet" onClick={ask}>{linkLabel}</button>
    ) : (
      <button className="btn-light" onClick={ask}><Trash2 size={16} aria-hidden /> Delete visit</button>
    );
  }

  const card = removal?.leaves_card ?? [];
  const back = removal?.joins_card ?? [];
  const onCard = new Set(card.map((x) => `${x.kind}|${x.label}`));
  const others = removal?.leaving.filter((x) => !onCard.has(`${x.kind}|${x.label}`)) ?? [];
  return (
    <div className="w-full basis-full space-y-3 rounded-2xl bg-ink-50/80 p-4 text-sm ring-1 ring-ink-100" role="group" aria-label="Delete visit">
      <p className="text-base font-bold tracking-tight">
        Delete {removal?.stay ? "this stay" : "this visit"}{removal?.visit_date ? ` of ${formatDate(removal.visit_date)}` : ""}?
      </p>
      {!removal && !error && <p className="text-ink-600">Checking what is in it…</p>}
      {removal && (
        <>
          <p className="text-ink-700">
            This deletes it for good: {removal.confirmed} fact{removal.confirmed === 1 ? "" : "s"} in your record
            {removal.values > 0 ? ` (${removal.values} test result${removal.values === 1 ? "" : "s"})` : ""}
            {removal.waiting > 0 ? `, ${removal.waiting} suggestion${removal.waiting === 1 ? "" : "s"} to review` : ""}
            , and the text read from its files. You cannot undo this.
          </p>
          {card.length > 0 && (
            <Note tone="amber" icon={<AlertTriangle size={16} aria-hidden />}>
              <span>
                <strong>These leave your emergency card:</strong> {card.map((x) => x.label).join(", ")}.
              </span>
            </Note>
          )}
          {back.length > 0 && (
            <p className="text-ink-700">
              An older visit also lists these, so they will show on your emergency card:{" "}
              {back.map((x) => x.label).join(", ")}.
            </p>
          )}
          {others.length > 0 && (
            <p className="text-ink-700">
              Only this visit says these, so they leave your record too: {others.map((x) => x.label).join(", ")}.
            </p>
          )}
        </>
      )}
      <ErrorNote error={error} />
      <div className="flex flex-wrap gap-2">
        <button className="btn-light font-bold" disabled={busy || !removal} onClick={erase}>
          <Trash2 size={16} aria-hidden /> {busy ? "Deleting…" : "Delete visit"}
        </button>
        <button className="btn" disabled={busy} onClick={close} autoFocus>Keep visit</button>
      </div>
    </div>
  );
}
