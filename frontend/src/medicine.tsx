import { ExternalLink, Info, Pill, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, type Entry, type InfoPoint, type MedicineInfo } from "./api";
import { ErrorNote } from "./components";
import { formatDate } from "./lib";

// Tap a medicine's name to see general information about it from trusted sites (Tavily search,
// summed up by Nemotron, every point checked against its page on the server). The points keep
// the sites' own words, so each one is shown as what that site says (decided 2026-10-12).

const SECTIONS: [keyof Pick<MedicineInfo, "uses" | "how_taken" | "side_effects" | "warnings">, string][] = [
  ["uses", "What it is used for"],
  ["how_taken", "How it is usually taken"],
  ["side_effects", "Side effects"],
  ["warnings", "Warnings"],
];

/** A medicine's name that opens its summary. Other entries show a plain label. */
export function MedicineName({ entry, className = "font-medium" }: { entry: Entry; className?: string }) {
  const [open, setOpen] = useState(false);
  // Only a named medicine can be looked up ("anti-malarial tablets" is a purpose, not a medicine).
  if (entry.kind !== "medication" || entry.checks.includes("medicine_unnamed")) {
    return <span className={className}>{entry.label}</span>;
  }
  return (
    <>
      <button
        type="button"
        className={`${className} relative inline-flex items-baseline gap-1 text-left before:absolute before:-inset-x-1 before:-inset-y-3 before:content-[''] underline decoration-lagoon-400 decoration-dotted underline-offset-4 hover:text-lagoon-700`}
        onClick={() => setOpen(true)}
        aria-label={`About ${entry.label}`}
      >
        {entry.label}
        <Info size={14} className="shrink-0 self-center text-lagoon-600" aria-hidden />
      </button>
      {open && <MedicineDialog entry={entry} onClose={() => setOpen(false)} />}
    </>
  );
}

function MedicineDialog({ entry, onClose }: { entry: Entry; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [info, setInfo] = useState<MedicineInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const generics = entry.medication?.generic_names ?? [];

  useEffect(() => {
    ref.current?.showModal();
  }, []);

  useEffect(() => {
    let live = true;
    setError(null);
    api.medicineInfo(entry.label, generics)
      .then((r) => live && setInfo(r))
      .catch((e) => live && setError((e as Error).message));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entry.label, attempt]);

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => e.target === ref.current && ref.current?.close()} // tap outside to close
      className="m-auto w-[calc(100%-2rem)] max-w-lg rounded-card bg-white p-0 text-ink-900 shadow-2xl backdrop:bg-ink-900/40"
      aria-labelledby="medicine-title"
    >
      <div className="max-h-[85vh] space-y-4 overflow-y-auto p-5">
        <div className="flex items-start gap-3">
          <span className="tile"><Pill size={20} aria-hidden /></span>
          <div className="min-w-0 grow">
            <h2 id="medicine-title" className="text-xl font-bold">{entry.label}</h2>
            {generics.length > 0 && <p className="text-sm text-ink-600">Contains {generics.join(" + ")}</p>}
          </div>
          <button className="btn-light px-2" onClick={() => ref.current?.close()} aria-label="Close">
            <X size={18} aria-hidden />
          </button>
        </div>

        <p className="rounded-2xl bg-ink-50 px-4 py-3 text-sm text-ink-700 ring-1 ring-ink-100">
          General information from trusted health sites. It is not advice for you. Ask your doctor or
          pharmacist before changing how you take a medicine.
        </p>

        {error ? (
          <div>
            <ErrorNote error={error} />
            <button className="btn-light" onClick={() => setAttempt((n) => n + 1)}>Try again</button>
          </div>
        ) : !info ? (
          <p className="animate-pulse text-sm text-ink-600" role="status">
            Looking this up on trusted sites. The first time can take up to a minute.
          </p>
        ) : !info.found ? (
          <p className="text-sm text-ink-700">
            The trusted sites had nothing clear about this medicine. Your pharmacist can tell you more.
          </p>
        ) : (
          <>
            {SECTIONS.map(([key, title]) => (
              info[key].length > 0 && (
                <section key={key}>
                  <h3 className="section-title mb-1">{title}</h3>
                  <ul className="list-disc space-y-1.5 pl-5 text-sm text-ink-800">
                    {info[key].map((p, i) => <Point key={i} point={p} info={info} />)}
                  </ul>
                </section>
              )
            ))}
            <section className="border-t border-ink-100 pt-3 text-xs text-ink-500">
              <p className="mb-1">
                Summarised by AI from these pages on {formatDate(info.fetched_at)}. Each point was checked
                against its page.
              </p>
              <ul className="space-y-1">
                {info.sources.map((s) => (
                  <li key={s.url}>
                    <a className="inline-flex items-center gap-1 underline" href={s.url} target="_blank" rel="noreferrer">
                      {s.site}: {s.title} <ExternalLink size={12} aria-hidden />
                    </a>
                  </li>
                ))}
              </ul>
            </section>
          </>
        )}
      </div>
    </dialog>
  );
}

function Point({ point, info }: { point: InfoPoint; info: MedicineInfo }) {
  const source = info.sources[point.source];
  return (
    <li>
      {point.text}{" "}
      {source && (
        <a className="text-xs whitespace-nowrap text-lagoon-700 underline" href={source.url} target="_blank" rel="noreferrer">
          {source.site}
        </a>
      )}
    </li>
  );
}
