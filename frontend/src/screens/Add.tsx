import { Building2, Camera, Download, FileText, FileUp, Mic, Square, Stethoscope, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import { useMe } from "../App";
import { Disclosure, ErrorNote } from "../components";
import { go } from "../lib";
import { visitMedia } from "../media";
import { LogStay } from "../stay";

/** `stayId`: adding documents to a hospital stay the patient logged earlier (#/add/<id>), or,
 * with `testId`, a report for one of a visit's tests (#/add/<visit>/<test>). */
export default function AddScreen({ stayId, testId }: { stayId?: string; testId?: string }) {
  const me = useMe();
  const [what, setWhat] = useState<"visit" | "stay">("visit");
  if (stayId) {
    return (
      <div className="space-y-4">
        <h1 className="title">{testId ? "Add a report" : "Add documents to your stay"}</h1>
        <VisitForm stayId={stayId} testId={testId} />
      </div>
    );
  }
  return (
    <div className="space-y-4">
      <h1 className="title">Add a visit</h1>
      <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-label="What are you adding?">
        {([["visit", "A doctor visit", Stethoscope], ["stay", "An emergency or hospital stay", Building2]] as const).map(
          ([value, label, Icon]) => (
            <button
              key={value}
              role="radio"
              aria-checked={what === value}
              className={`btn-light h-full justify-start text-left whitespace-normal ${what === value ? "ring-2 ring-lagoon-500" : ""}`}
              onClick={() => setWhat(value)}
            >
              <Icon size={18} className="shrink-0" aria-hidden /> {label}
            </button>
          ),
        )}
      </div>
      {what === "visit" ? <VisitForm /> : <LogStay patientId={me.id} />}
    </div>
  );
}

function VisitForm({ stayId, testId }: { stayId?: string; testId?: string }) {
  const me = useMe();
  const [audio, setAudio] = useState<File | null>(null);
  const [documents, setDocuments] = useState<File[]>([]);
  const [notes, setNotes] = useState("");
  const [consent, setConsent] = useState(false); // the doctor agreed to being recorded
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Preview URLs made once per file, not on every render (that would reset the player).
  const audioUrl = useMemo(() => (audio ? URL.createObjectURL(audio) : null), [audio]);
  const docUrls = useMemo(() => documents.map((f) => URL.createObjectURL(f)), [documents]);
  // Phones and tablets get a camera button; on a computer "Choose files" is enough.
  const touch = useMemo(() => window.matchMedia?.("(pointer: coarse)").matches ?? false, []);

  const submit = async () => {
    setBusy(true);
    setError(null);
    const form = new FormData();
    documents.forEach((f) => form.append("documents", f));
    if (audio) {
      form.append("audio", audio);
      form.append("recording_consent", String(consent));
    }
    if (notes.trim() && !testId) form.append("notes", notes.trim());
    if (testId) form.append("test_id", testId);
    try {
      const enc = stayId ? await api.addDocuments(stayId, form) : await api.createEncounter(me.id, form);
      // Keep this browser's copy for the proof view and a careful re-read: the server drops
      // the files. Deleted once the visit is reviewed. A stay's new files are its last sources.
      const all = enc.sources.filter((s) => s.source_type === "image");
      const images = documents.length ? all.slice(-documents.length) : [];
      const keeps = images.map((s, i) => documents[i] && visitMedia.keep(enc.id, s.id, documents[i], "image"));
      const audioSource = enc.sources.find((s) => s.source_type === "audio");
      if (audioSource && audio) keeps.push(visitMedia.keep(enc.id, audioSource.id, audio, "audio"));
      await Promise.all(keeps);
      try {
        localStorage.setItem("healthrecord.lastEncounter", enc.id);
      } catch {
        // not remembered in private mode
      }
      go(`/encounters/${enc.id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  const addDocuments = (files: File[]) => setDocuments((docs) => [...docs, ...files]);
  const nothing = !audio && documents.length === 0 && !notes.trim();
  const needsConsent = !!audio && !consent;
  return (
    <div className="space-y-4">
      <div>
        <Disclosure label="How this works">
          <div className="space-y-2 pb-2">
            <p className="text-ink-700">
              {testId
                ? "Upload the report for this test as a photo, screenshot or PDF. We copy the results as printed. You can correct any of them."
                : stayId
                ? "Add the discharge summary, reports, bills or prescriptions from your stay, or type a few notes. AI suggests what to add. You confirm allergies, medicines, diagnoses and events afterwards."
                : "Add prescriptions, bills or reports, record the consultation, or type a few notes. AI suggests what to add. You confirm allergies, medicines, diagnoses and events afterwards."}
            </p>
            <p className="text-sm text-ink-500">
              Our server reads your files and does not keep them. This browser keeps a copy until you finish
              reviewing, so you can see where each fact came from. Then it is deleted.
            </p>
            {!testId && (
              <p className="text-sm text-ink-500">
                A bill shows what was bought, not what you take, so it stays apart from your medicines.
              </p>
            )}
          </div>
        </Disclosure>
        {!testId && !stayId && <SampleFiles />}
      </div>

      {!stayId && <div className="card space-y-3">
        <h2 className="section-title">Recording</h2>
        <label
          htmlFor="recording-consent"
          className={`flex items-start gap-3 rounded-2xl px-4 py-3 text-sm ring-1 ${
            needsConsent ? "bg-amber-50 text-amber-900 ring-amber-300" : "bg-ink-50/80 text-ink-700 ring-ink-100"
          }`}
        >
          <input
            id="recording-consent"
            type="checkbox"
            className="mt-0.5 size-4"
            checked={consent}
            onChange={(e) => setConsent(e.target.checked)}
          />
          <span>Tick to confirm the doctor agrees to be recorded. Ask before you start.</span>
        </label>
        <Recorder onDone={setAudio} />
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm text-ink-500">or</span>
          <FilePick accept="audio/*" onPick={(files) => setAudio(files[0] ?? null)}>
            <Mic size={16} aria-hidden /> Upload a recording
          </FilePick>
        </div>
        {audioUrl && (
          <div className="flex items-center gap-2 text-sm">
            <audio controls src={audioUrl} />
            <button className="btn-light" onClick={() => setAudio(null)}>Remove recording</button>
          </div>
        )}
      </div>}

      <div className="card space-y-3">
        <div>
          <h2 className="section-title">
            {testId ? "The report" : stayId ? "Discharge summary, reports, bills" : "Prescriptions, bills, reports"}
          </h2>
          <p className="text-sm text-ink-600">
            Add photos, screenshots or PDFs (up to 10 pages each).
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {touch && (
            <FilePick accept="image/*" capture onPick={addDocuments}>
              <Camera size={16} aria-hidden /> Take a photo
            </FilePick>
          )}
          <FilePick accept="image/*,application/pdf,.pdf" multiple onPick={addDocuments}>
            <FileUp size={16} aria-hidden /> Choose files
          </FilePick>
        </div>
        {documents.length > 0 && (
          <ul className="flex flex-wrap gap-3">
            {documents.map((f, i) => (
              <li key={i} className="relative w-24 text-xs">
                {isPdf(f) ? (
                  <div className="grid h-24 place-items-center rounded-xl bg-white/85 text-lagoon-700 ring-1 ring-ink-200">
                    <FileText size={32} aria-hidden />
                  </div>
                ) : (
                  <img src={docUrls[i]} alt="" className="h-24 w-24 rounded-xl object-cover ring-1 ring-ink-200" />
                )}
                <p className="mt-1 truncate text-ink-600" title={f.name}>{f.name}</p>
                <button
                  className="absolute -top-2 -right-2 grid size-7 place-items-center rounded-full bg-white text-ink-700 shadow ring-1 ring-ink-200 hover:text-ink-900"
                  aria-label={`Remove ${f.name}`}
                  onClick={() => setDocuments(documents.filter((_, j) => j !== i))}
                >
                  <X size={14} aria-hidden />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {!testId && (
        <div className="card space-y-2">
          <h2 className="section-title">
            <label htmlFor="typed-notes">
              Typed notes (optional)
            </label>
          </h2>
          <textarea id="typed-notes" className="input h-24" value={notes} onChange={(e) => setNotes(e.target.value)} />
        </div>
      )}

      <ErrorNote error={error} />
      {needsConsent && (
        <p className="text-sm text-amber-900">Tick the box above to go on.</p>
      )}
      <button className="btn" disabled={busy || nothing || needsConsent} onClick={submit}>
        {busy ? "Uploading…" : testId ? "Add report" : stayId ? "Add documents" : "Add visit"}
      </button>
    </div>
  );
}

const SAMPLES: [file: string, what: string][] = [
  ["asha-prescription.png", "Sample prescription"],
  ["asha-bill.png", "Sample pharmacy bill (goes with the prescription above)"],
  ["meera-visit2-prescription.png", "Sample prescription 2"],
  ["meera-visit2-bill.png", "Sample pharmacy bill 2 (goes with prescription 2)"],
  ["meera-visit4-lab-report.png", "Sample lab report with test results"],
];

/** For someone with no documents to hand: made-up files to download, then add with "Choose files". */
function SampleFiles() {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div>
      <button
        type="button"
        className="inline-flex min-h-11 items-center gap-1.5 rounded-lg text-left text-sm font-semibold text-tide-700 underline underline-offset-2"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen(!open)}
      >
        <Download size={16} className="shrink-0" aria-hidden /> No documents to hand? Download sample files
      </button>
      <div id={id} hidden={!open}>
        {open && (
          <div className="card mt-1 space-y-2 p-4">
            <ul className="divide-y divide-ink-100 text-sm">
              {SAMPLES.map(([file, what]) => (
                <li key={file}>
                  <a
                    href={`/samples/${file}`}
                    download
                    className="flex min-h-11 items-center gap-2 py-1 text-tide-700 underline underline-offset-2"
                  >
                    <FileText size={16} className="shrink-0" aria-hidden /> {what}
                  </a>
                </li>
              ))}
            </ul>
            <p className="text-sm text-ink-600">All made up. Add them below with Choose files.</p>
          </div>
        )}
      </div>
    </div>
  );
}

/** Records from the microphone. */
function Recorder({ onDone }: { onDone: (f: File) => void }) {
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);

  useEffect(() => {
    if (!recording) return;
    const t = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [recording]);

  const start = async () => {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream);
      const parts: Blob[] = [];
      rec.ondataavailable = (e) => parts.push(e.data);
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const type = rec.mimeType || "audio/webm";
        const ext = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";
        onDone(new File(parts, `consult.${ext}`, { type }));
      };
      rec.start();
      recorder.current = rec;
      setSeconds(0);
      setRecording(true);
    } catch (e) {
      setError("We could not use your microphone. Check that the browser is allowed to use it, then try again.");
    }
  };

  const stop = () => {
    recorder.current?.stop();
    setRecording(false);
  };

  return (
    <div className="flex items-center gap-3">
      {recording ? (
        <button className="btn [background:var(--color-red-600)] [box-shadow:none]" onClick={stop}>
          <Square size={16} aria-hidden /> Stop recording ({seconds} s)
        </button>
      ) : (
        <button className="btn-light inline-flex items-center gap-2" onClick={start}>
          <Mic size={16} aria-hidden /> Start recording
        </button>
      )}
      <ErrorNote error={error} />
    </div>
  );
}

const isPdf = (f: File) => f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf");

/** A file input that looks and works like a button (the browser's own input looks like text). */
function FilePick({ accept, multiple, capture, onPick, children }: {
  accept: string;
  multiple?: boolean;
  capture?: boolean;
  onPick: (files: File[]) => void;
  children: ReactNode;
}) {
  return (
    <label className="btn-light cursor-pointer has-[input:focus-visible]:outline-2 has-[input:focus-visible]:outline-offset-2 has-[input:focus-visible]:outline-tide-500">
      {children}
      <input
        type="file"
        className="sr-only"
        accept={accept}
        multiple={multiple}
        capture={capture ? "environment" : undefined}
        onChange={(e) => {
          onPick(Array.from(e.target.files ?? []));
          e.target.value = ""; // so the same file can be picked again after removing it
        }}
      />
    </label>
  );
}
