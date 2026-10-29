import { useCallback, useEffect, useState } from "react";

// ---- hash routing: #/review/abc -> ["review", "abc"] ------------------------
export function useRoute(): string[] {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const onChange = () => setHash(window.location.hash);
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return hash.replace(/^#\/?/, "").split("/").filter(Boolean);
}

/** Edits the patient made (not the translation pass's "system"); mirrors `Entry.edited_by_patient` on the server. */
export const editedByPatient = (e: { patient_id: string | null; edits: { by: string }[] }) =>
  e.edits.some((x) => (e.patient_id ? x.by === e.patient_id : x.by !== "system"));
export const translated = (e: { edits: { by: string }[] }) => e.edits.some((x) => x.by === "system");

export const go = (path: string) => {
  window.location.hash = path;
};

// ---- where to go after signing in (a doctor who came from a patient's QR code) -------------
const RETURN_KEY = "crata.returnTo";

export function rememberReturn(hash: string) {
  try {
    sessionStorage.setItem(RETURN_KEY, hash);
  } catch {
    // storage blocked: the doctor lands on their home page instead
  }
}

/** The page to go back to after signing in, once. */
export function takeReturn(): string | null {
  try {
    const hash = sessionStorage.getItem(RETURN_KEY);
    sessionStorage.removeItem(RETURN_KEY);
    return hash;
  } catch {
    return null;
  }
}

// ---- load data with loading / error state, and a reload function ------------
export function useLoad<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(load, deps); // reloads when deps change, like useEffect
  const reload = useCallback(() => {
    setError(null);
    run().then(setData, (e: Error) => setError(e.message));
  }, [run]);
  useEffect(reload, [reload]);
  return { data, error, reload, setData };
}

/** How an access log entry reads to the patient. Worded from what the entry records, so older
 * entries (trusted people saw the whole card before 2026-10-12) still read truthfully. */
type AccessLike = {
  actor_role: string; via: string | null; place: string | null; tiers: string[];
  reason: string;
};

/** A trusted person starting an emergency (they see the basics only and open nothing). */
const startedEmergency = (e: AccessLike) => e.actor_role === "trusted" && e.tiers.length === 0;

/** True only when the entry is someone actually opening the emergency card: not a trusted person
 * starting one. */
export const openedCard = (e: AccessLike) => e.reason === "emergency" && !startedEmergency(e);

/** An alert about the emergency card being opened: the only kind shown in red (trusted-person
 * news is not an emergency). */
export function emergencyAlert(a: { log_entry_id: string | null }, log: ({ id: string } & AccessLike)[]): boolean {
  const entry = a.log_entry_id ? log.find((e) => e.id === a.log_entry_id) : undefined;
  return !!entry && entry.reason === "emergency";
}

/** What happened, without the place ("Opened your emergency card"). */
export function accessWhat(e: AccessLike): string {
  return startedEmergency(e)
    ? "Started an emergency and saw only the basics"
    : e.via
      ? `Opened your emergency card with ${e.via}'s QR code`
      : "Opened your emergency card";
}

/** True when a trusted person started an emergency (they see the basics only). */
export const startedEmergencyEntry = (e: AccessLike) => e.reason === "emergency" && startedEmergency(e);

export function accessLine(e: AccessLike): string {
  const what = accessWhat(e);
  return e.place ? `${what} at ${e.place}` : what;
}

// ---- small formatters --------------------------------------------------------
export const kindLabel = (k: string) => {
  if (k === "result") return "Test result";
  if (k === "follow_up") return "Follow-up";
  const t = k.replace(/_/g, " ");
  return t.charAt(0).toUpperCase() + t.slice(1);
};

/** "2026-10-12" -> "12 Oct 2026" (local, no time-zone shift for plain dates). A moment
 * ("2026-10-22T20:41:38Z") gives its local day, which in India may be the next one. */
export function formatDate(iso: string | null): string {
  if (!iso) return "Date unknown";
  const opts = { day: "numeric", month: "short", year: "numeric" } as const;
  if (iso.length > 10) return new Date(iso).toLocaleDateString(undefined, opts);
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, opts);
}

/** "6:49 pm" (no seconds, no "06:49 AM"). */
export function formatTime(iso: string | null): string {
  if (!iso) return "";
  const t = new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", hour12: true });
  return t.replace(/\s?(AM|PM)$/, (_m, ap: string) => ` ${ap.toLowerCase()}`);
}

/** A moment as "12 Oct 2026, 6:49 pm". */
export function formatDateTime(iso: string | null): string {
  if (!iso) return "";
  const day = new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  return `${day}, ${formatTime(iso)}`;
}

export function age(birthYear: number | null): string {
  return birthYear ? `${new Date().getFullYear() - birthYear} years` : "";
}

/** "F" and "M" in words ("female", "male"); anything else as written, in lower case. */
export function sexWord(sex: string | null): string {
  return sex ? ({ F: "female", M: "male" } as Record<string, string>)[sex.toUpperCase()] ?? sex.toLowerCase() : "";
}

// ---- dose patterns in plain words -------------------------------------------
// A few common Indian prescription patterns. The original stays; the words go in brackets.
const DOSE_WORDS: Record<string, string> = {
  "1-0-1": "morning and night",
  "1-1-1": "morning, afternoon and night",
  "1-0-0": "morning",
  "0-0-1": "night",
  "0-1-0": "afternoon",
  "1-1-0": "morning and afternoon",
  "0-1-1": "afternoon and night",
  "1-1-1-1": "four times a day",
  "sos": "only when needed",
  "bd": "twice a day",
  "bid": "twice a day",
  "tds": "three times a day",
  "tid": "three times a day",
  "hs": "at bedtime",
};

/** "1-0-1" -> "1-0-1 (morning and night)", "SOS" -> "SOS (only when needed)". A pattern that is
 * not in the table (or already has words) comes back exactly as written. */
export function dosePattern(text: string): string {
  const words = doseWords(text);
  return words ? `${text.trim()} (${words})` : text;
}

/** The plain words alone for a pattern in the table ("1-0-1" -> "morning and night"), else null. */
export function doseWords(text: string): string | null {
  return DOSE_WORDS[text.trim().replace(/\s*-\s*/g, "-").toLowerCase()] ?? null;
}

export const stepLabels: Record<string, string> = {
  preparing_audio: "Preparing the recording",
  reading_documents: "Reading your documents",
  sorting_documents: "Sorting your documents",
  finding_drug_names: "Finding medicine names",
  transcribing: "Listening to the recording",
  extracting: "Finding facts",
  checking_medicines: "Checking medicine names",
  cross_checking: "Checking the bill and your allergies",
  matching_report: "Checking whether it belongs to an earlier visit",
};

/** Seconds from a locator like "00:26-01:00" (start of the span). */
export function locatorStart(locator: string | null): number | null {
  const m = locator?.match(/^(\d+):(\d+)/);
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}
