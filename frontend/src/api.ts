// Types mirror the backend's pydantic models (backend/app/schemas, app/views.py).
// Every call goes to /api; in development Vite forwards it to the backend on :8000.

export type EntryKind =
  | "medication" | "diagnosis" | "symptom" | "test" | "result" | "allergy" | "follow_up" | "note"
  | "emergency_visit" | "admission" | "procedure" | "surgery" | "implant";
export type EntryStatus = "proposed" | "confirmed" | "rejected";
export type SourceType = "audio" | "image" | "text" | "manual";
export type MedicationState = "current" | "end_unknown" | "ended" | "stopped";
export type Speaker = "doctor" | "patient" | "document";
export type VisitDateSource = "document" | "upload" | "patient";

export const EVENT_KINDS: EntryKind[] = [
  "emergency_visit", "admission", "procedure", "surgery", "implant",
];

export interface Patient {
  id: string;
  name: string;
  birth_year: number | null;
  sex: string | null;
  blood_group: string | null;
}

export interface Provenance {
  source_id: string;
  source_type: SourceType;
  locator: string | null;
  quote: string | null;
}

export interface Medication {
  dose: string | null;
  doses_per_day: number | null;
  duration_days: number | null;
  started_on: string | null;
  ongoing: boolean;
  stopped_on: string | null;
  /** Active ingredients, e.g. ["paracetamol"]; from the curated table or the model. */
  generic_names: string[];
  /** table: the curated brand table; search: read off a trusted site (`generic_source`);
   * model: the AI's own match. */
  generic_from: "table" | "search" | "model" | null;
  generic_source: string | null;
}

/** A measured value as printed on a report or prescription. Comparing it with the printed
 * range is code on the server; the screens only show the range. */
export interface TestResult {
  value: number | null;
  /** A result in words ("Nil", "Non-reactive") or with a sign ("<0.5"). */
  value_text: string | null;
  unit: string | null;
  ref_low: number | null;
  ref_high: number | null;
  ref_text: string | null;
  /** The report's date (the upload day if none was printed). */
  measured_on: string | null;
}

/** Something code noticed that the patient should look at before confirming. */
export type EntryCheck = "medicine_unnamed" | "name_not_in_quote" | "not_english";

/** `by` is the patient, or "system" for the translation into English. */
export interface FieldEdit { field: string; old: string | null; new: string | null; at: string; by: string }
export interface StatusChange { status: EntryStatus; at: string; by: string; note: string | null }

export interface Entry {
  id: string;
  patient_id: string | null;
  encounter_id: string | null;
  kind: EntryKind;
  label: string;
  fields: Record<string, string>;
  provenance: Provenance[];
  status: EntryStatus;
  status_history: StatusChange[];
  confidence: number | null;
  tier: string;
  said_by: Speaker | null;
  medication: Medication | null;
  /** Test values only. */
  result: TestResult | null;
  /** In the record without a confirm tap (a value read from a report). */
  added_as_read: boolean;
  /** Ids of entries this one belongs to; a test value links to its test. */
  links: string[];
  edits: FieldEdit[];
  occurred_on: string | null;
  unverified: boolean;
  checks: EntryCheck[];
}

export interface TranscriptSegment { start_s: number; end_s: number; text: string }

export interface SourceItem {
  id: string;
  source_type: SourceType;
  filename: string | null;
  text: string | null;
  segments: TranscriptSegment[];
  /** A careful re-read is a new source for the same file. */
  reread_of: string | null;
  careful: boolean;
  recording_consent: boolean | null;
  /** Images only. Bills are read into bill lines, not suggestions. */
  document_kind: "prescription" | "bill" | "report" | "other" | null;
  /** PDFs only: page count, each page's text, and (multi-page) what each page is. */
  pages: number | null;
  page_texts: string[];
  page_kinds: ("prescription" | "bill" | "report" | "other")[];
}

/** One line of a pharmacy bill: what was sold. Compared with the prescription by the checks. */
export interface BillLine {
  id: string;
  name: string;
  quantity: number | null;
  unit: string | null;
  units_per_pack: number | null;
  is_drug: boolean;
  generic_names: string[];
  /** table: the curated brand table; search: read off a trusted site (`generic_source`);
   * model: the AI's own match. */
  generic_from: "table" | "search" | "model" | null;
  generic_source: string | null;
  provenance: Provenance;
}

/** Sources that disagree, found by the cross-checks. Allergy clashes: "urgent" is red (same
 * ingredient or family), "review" is amber (a related family). */
export interface Discrepancy {
  id: string;
  check: "allergy_clash" | "billed_not_prescribed" | "quantity_mismatch";
  message: string;
  entry_ids: string[];
  bill_line_ids: string[];
  provenance: Provenance[];
  severity: "info" | "review" | "urgent";
  matched_by: "table" | "model";
}

/** An emergency visit or hospital admission the patient logged. Open until `ended_on` is set. */
export interface HospitalStay {
  kind: "emergency_visit" | "admission";
  place: string | null;
  reason: string | null;
  started_on: string;
  ended_on: string | null;
  entry_id: string;
  /** Read off a discharge summary: only a suggestion until the patient uses it. */
  suggested_end: string | null;
  suggested_end_quote: string | null;
  suggested_end_source_id: string | null;
}

export interface Encounter {
  id: string;
  patient_id: string;
  created_at: string;
  visit_date: string | null;
  visit_date_source: VisitDateSource | null;
  /** The visit's name: the AI's suggestion ("ai") until the patient writes their own ("patient"). */
  title: string | null;
  title_source: "ai" | "patient" | null;
  /** Hospital, clinic or lab, as printed on a document. */
  facility: string | null;
  status: "processing" | "ready" | "failed";
  step: string | null;
  error: string | null;
  sources: SourceItem[];
  entries: Entry[];
  /** Shown at the top of Review; ones the patient's review settled are left out. */
  discrepancies: Discrepancy[];
  bill_lines: BillLine[];
  stay: HospitalStay | null;
  step_seconds: Record<string, number>;
  /** Step -> models that answered, e.g. "nebius:google/gemma-3-27b-it (backup)". */
  models_used: Record<string, string[]>;
  quote_failures: string[];
  /** Proposed entry id -> the matching label already in the record (Review hint). */
  already_in_record?: Record<string, string>;
  /** Entry id -> why it needs a look of its own: a note from the checks, a clash with an allergy
   * or the bill, or wording that was unclear. Such rows stay open and Confirm all skips them. */
  flagged?: Record<string, FlagReason>;
}

export type FlagReason = "check" | "clash" | "unsure";

/** What waits for the patient's tap, one box per kind; everything else is read as it is. Mirrors
 * `ESSENTIAL_KINDS` in backend/app/schemas/record.py. */
export const ESSENTIAL_KINDS: EntryKind[] = ["medication", "allergy", "diagnosis", ...EVENT_KINDS];
export const isEssential = (k: EntryKind) => ESSENTIAL_KINDS.includes(k);

// ---- My health numbers (worked out on the server: backend/app/views.py `numbers`) -------------
export interface NumberRange {
  low: number | null;
  high: number | null;
  /** The date of the report that printed it. */
  on: string | null;
  encounter_id: string | null;
  /** This reading had none of its own: it is compared with the latest one printed. */
  borrowed: boolean;
}

export interface NumberPoint {
  entry_id: string;
  on: string;
  value: number;
  unit: string | null;
  source: "report" | "you";
  encounter_id: string | null;
  range: NumberRange | null;
  /** Against `range`; null when there is nothing to compare with. */
  position: "low" | "high" | "within" | null;
  /** The patient took it out: listed struck through, not charted. */
  removed: boolean;
  edited: boolean;
}

export interface NumberSeries {
  key: string;
  name: string;
  unit: string | null;
  /** Values whose unit was not printed, kept apart from the ones that have one. */
  unit_missing: boolean;
  points: NumberPoint[];
  latest: NumberPoint | null;
  range: NumberRange | null;
}

export interface NumbersGroup {
  key: string;
  name: string;
  series: NumberSeries[];
  latest_on: string | null;
  count: number;
  /** The newest reading of some line is outside its range. */
  flagged: boolean;
}

/** A common measure Add a reading offers. Blood pressure is two numbers (`parts`). */
export interface Preset { name: string; unit: string | null; parts: string[] }
export interface Numbers { groups: NumbersGroup[]; presets: Preset[] }

/** What deleting one visit takes out of the record: shown before the patient confirms. */
export interface Removal {
  visit_date: string | null;
  stay: boolean;
  confirmed: number;
  values: number;
  waiting: number;
  /** Facts only this visit says: they leave the record. */
  leaving: { kind: EntryKind; label: string }[];
  /** On the emergency card today, and not any more afterwards (worked out from the card itself:
   * it shows each fact's latest statement, so this can include a fact another visit also says). */
  leaves_card: { kind: EntryKind; label: string }[];
  /** Not on the card today, there afterwards (an older statement comes back). */
  joins_card: { kind: EntryKind; label: string }[];
}

export interface ReviewResult {
  entries: Entry[];
  skipped: { id: string; reason: FlagReason | "not_waiting" }[];
}

export interface Sighting { entry_id: string; encounter_id: string | null; on: string; said_by: Speaker | null }
/** One fact, shown once, with every visit it was confirmed in (newest first). */
export interface Fact { key: string; kind: EntryKind; latest: Entry; sightings: Sighting[] }

export interface Visit {
  encounter_id: string | null; // null: entries added by hand
  visit_date: string | null;
  visit_date_source: VisitDateSource | null;
  title: string | null;
  title_source: "ai" | "patient" | null;
  facility: string | null;
  uploaded_at: string | null;
  status: Encounter["status"] | null;
  sources: { source_type: SourceType; filename: string | null }[];
  entries: Entry[];
  pending: number;
  stay: HospitalStay | null;
}

export interface CardMedication { entry: Entry; state: MedicationState }

/** "In hospital since": a stay the patient logged and has not closed. */
export interface OpenStay { kind: HospitalStay["kind"]; place: string | null; reason: string | null; since: string }

export interface EmergencySummary {
  patient: Patient;
  open_stays: OpenStay[];
  allergies: Entry[];
  medications: CardMedication[];
  conditions: Entry[];
  implants: Entry[];
  timeline: Entry[];
}

export interface PatientRecord {
  patient: Patient;
  facts: Fact[];
  visits: Visit[];
  timeline: Entry[];
}

export interface AccessLogEntry {
  id: string;
  actor_id: string;
  actor_role: "patient" | "trusted" | "doctor";
  actor_label: string;
  actor_registry_id: string | null;
  verified: boolean;
  place: string | null;
  at: string;
  /** A doctor who opened the card with a trusted person's code: that person's name. */
  via: string | null;
  /** What the opening could see; empty for a trusted person starting an emergency. */
  tiers: string[];
  /** own: the patient; emergency: the emergency card. */
  reason: "own" | "emergency";
  hash: string;
  prev_hash: string;
}

export interface Alert {
  id: string;
  recipient_id: string;
  patient_id: string;
  /** The access log entry it is about; null for alerts about trusted people. */
  log_entry_id: string | null;
  message: string;
  at: string;
  read: boolean;
  reported: boolean;
}

export interface TrustedPerson { id: string; name: string }
/** An open invitation the patient sent (the number they typed). */
export interface SentInvite { id: string; phone: string; created_at: string; expires_at: string }
/** An open invitation to the signed-in person's own number. */
export interface ReceivedInvite { id: string; from_name: string; created_at: string; expires_at: string }
export interface EmergencyAccess { summary: EmergencySummary; log_entry: AccessLogEntry; alerted: string[] }
export interface Meta {
  perceive: { provider: string; model: string };
  reason: { provider: string; model: string };
}

/** A signed-in doctor sends only place and code: who they are comes from the session. A doctor
 * without an account gives a name and registry ID. */
export interface EmergencyRequest {
  actor_label?: string;
  registry_id?: string;
  place?: string;
  /** A trusted person's short-lived code, from their QR. */
  code?: string;
}

/** What a trusted person gets: the basics for paramedics and a code for a doctor. */
export interface TrustedEmergency {
  code: string;
  expires_at: string;
  basics: { name: string; blood_group: string | null; allergies: { label: string; reaction: string | null }[] };
}

/** General information about a medicine from trusted sites (not part of the record). */
export interface InfoPoint { text: string; source: number; quote: string }
export interface MedicineInfo {
  key: string;
  name: string;
  generic_names: string[];
  found: boolean;
  uses: InfoPoint[];
  how_taken: InfoPoint[];
  side_effects: InfoPoint[];
  warnings: InfoPoint[];
  sources: { title: string; url: string; site: string }[];
  fetched_at: string;
}

/** The signed-in account (phone number masked). */
export interface MeView {
  id: string;
  role: "patient" | "doctor";
  name: string;
  phone: string | null;
  person_id: string | null;
  registry_id: string | null;
  admin: boolean;
  demo: boolean;
}
export interface DemoAccount { key: string; name: string; role: MeView["role"]; about: string }
export interface CodeSent { challenge_id: string; phone: string; simulated_code: string | null }
export interface SignUpBody {
  challenge_id: string;
  role: MeView["role"];
  name: string;
  registry_id?: string | null;
  birth_year?: number | null;
  blood_group?: string | null;
}

/** One test value outside its printed range (may be missing on older saves). */
export interface OutOfRangeTest {
  name: string;
  value: string | number;
  unit: string | null;
  date: string | null;
  direction: "high" | "low";
  range_text: string | null;
}

/** Before a visit: a saved summary for the doctor. */
export interface VisitPrep {
  id: string;
  patient_id: string;
  created_at: string;
  text: string;
  since: string | null;
  urgent: boolean;
  warning_signs: string[];
  helpline?: string | null;
  symptoms: { text: string; quote: string }[];
  specialty: string;
  specialty_label: string;
  reason: string;
  questions: string[];
  record: {
    allergies: string[];
    medicines: string[];
    conditions: string[];
    events: string[];
    out_of_range: string[];
    tests_out_of_range?: OutOfRangeTest[];
  };
  models_used: string[];
}

export type AskCategory = "good_to_know" | "be_careful" | "your_conditions";
export interface AskPoint {
  text: string;
  quote: string;
  site: string;
  title: string;
  url: string;
  category?: AskCategory;
  source?: number;
}
export interface AskSource { n: number; site: string; title: string; url: string }

/** Ask about my health: an answer from trusted sites (not stored on the server). */
export interface AskAnswer {
  urgent: boolean;
  warning_signs: string[];
  helpline?: string | null;
  health_question: boolean;
  points: AskPoint[];
  sources?: AskSource[];
  allergy_notes: string[];
  considered: string[];
  note: string;
  models_used: string[];
}

/** An error from the server; `status` is the HTTP status (429 limit, 413 too big, 422 unusable). */
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

/** Fired when the server says the session is gone (expired, or signed out elsewhere). */
export const SIGNED_OUT = "crata:signed-out";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, init);
  if (res.status === 401 && !path.startsWith("/auth/")) window.dispatchEvent(new Event(SIGNED_OUT));
  if (!res.ok) {
    // App.tsx's media sweep reads "not found" in the message, so a 404 keeps those words.
    let message = res.status === 404 ? "Not found." : "Something went wrong. Try again in a moment.";
    try {
      const body = await res.json();
      // A validation error (422) comes as a list of {msg}: show the words, not the JSON.
      const words = Array.isArray(body.detail)
        ? body.detail.map((d: { msg?: unknown }) => (typeof d?.msg === "string" ? d.msg : "")).filter(Boolean).join(". ")
        : "";
      message = typeof body.detail === "string" ? body.detail : words || JSON.stringify(body.detail);
    } catch {
      // keep the plain default message
    }
    throw new ApiError(message, res.status);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  auth: {
    me: () => request<MeView>("/auth/me"),
    demoAccounts: () => request<DemoAccount[]>("/auth/demo-accounts"),
    demo: (key: string) => request<MeView>("/auth/demo", json("POST", { key })),
    code: (phone: string) => request<CodeSent>("/auth/code", json("POST", { phone })),
    verify: (challengeId: string, code: string) =>
      request<{ status: "signed_in" | "needs_profile"; me: MeView | null }>(
        "/auth/verify", json("POST", { challenge_id: challengeId, code })),
    signup: (body: SignUpBody) => request<MeView>("/auth/signup", json("POST", body)),
    logout: () => request<void>("/auth/logout", { method: "POST" }),
  },
  patients: () => request<Patient[]>("/patients"),
  patient: (id: string) => request<Patient>(`/patients/${id}`),
  encounter: (id: string) => request<Encounter>(`/encounters/${id}`),
  setVisitDate: (id: string, visitDate: string) =>
    request<Encounter>(`/encounters/${id}`, json("PATCH", { visit_date: visitDate })),
  setVisitTitle: (id: string, title: string) =>
    request<Encounter>(`/encounters/${id}`, json("PATCH", { title })),
  createEncounter: (patientId: string, form: FormData) =>
    request<Encounter>(`/patients/${patientId}/encounters`, { method: "POST", body: form }),
  /** Read a visit again from this browser's copy of its files. */
  reread: (id: string, files: { sourceId: string; file: File }[], careful = true) => {
    const form = new FormData();
    files.forEach((f) => {
      form.append("files", f.file);
      form.append("source_ids", f.sourceId);
    });
    form.append("careful", String(careful));
    return request<Encounter>(`/encounters/${id}/reread`, { method: "POST", body: form });
  },
  entryAction: (id: string, body: Record<string, unknown>) =>
    request<Entry>(`/entries/${id}`, json("PATCH", body)),
  numbers: (patientId: string) => request<Numbers>(`/patients/${patientId}/numbers`),
  /** Readings the patient took themselves; a blood pressure is two. `on` defaults to today. */
  addReadings: (patientId: string, readings: { name: string; value: number; unit?: string | null }[], on?: string) =>
    request<Entry[]>(`/patients/${patientId}/readings`, json("POST", { readings, on: on || null })),
  removal: (encounterId: string) => request<Removal>(`/encounters/${encounterId}/removal`),
  /** Erase a visit for good (its facts, test values and the text read from its files). */
  deleteVisit: (encounterId: string) => request<void>(`/encounters/${encounterId}`, { method: "DELETE" }),
  /** Confirm or turn down several waiting suggestions of one visit (a box on Review). */
  reviewMany: (encounterId: string, action: "confirm" | "reject", entryIds: string[]) =>
    request<ReviewResult>(`/encounters/${encounterId}/review`, json("POST", { action, entry_ids: entryIds })),
  logStay: (patientId: string, body: Record<string, unknown>) =>
    request<Encounter>(`/patients/${patientId}/stays`, json("POST", body)),
  addDocuments: (encounterId: string, form: FormData) =>
    request<Encounter>(`/encounters/${encounterId}/documents`, { method: "POST", body: form }),
  updateStay: (encounterId: string, body: Record<string, unknown>) =>
    request<Encounter>(`/encounters/${encounterId}/stay`, json("PATCH", body)),
  addEvent: (patientId: string, body: Record<string, unknown>) =>
    request<Entry>(`/patients/${patientId}/events`, json("POST", body)),
  /** Something the patient adds to a visit that missed it (their own); in the record at once. */
  addToVisit: (encounterId: string, body: Record<string, unknown>) =>
    request<Entry>(`/encounters/${encounterId}/entries`, json("POST", body)),
  record: (patientId: string) => request<PatientRecord>(`/patients/${patientId}/record`),
  emergency: (patientId: string) => request<EmergencySummary>(`/patients/${patientId}/emergency`),
  /** The signed-in trusted person starts an emergency for someone who trusts them. */
  trustedEmergency: (patientId: string) =>
    request<TrustedEmergency>(`/patients/${patientId}/access/trusted-emergency`, { method: "POST" }),
  emergencyAccess: (patientId: string, body: EmergencyRequest) =>
    request<EmergencyAccess>(`/patients/${patientId}/access/emergency`, json("POST", body)),
  accessLog: (patientId: string) =>
    request<{ entries: AccessLogEntry[]; chain_intact: boolean }>(`/patients/${patientId}/access-log`),
  /** The signed-in person's own alerts. */
  alerts: () => request<Alert[]>("/alerts"),
  updateAlert: (id: string, body: { read?: boolean; reported?: boolean }) =>
    request<Alert>(`/alerts/${id}`, json("PATCH", body)),
  trusted: (patientId: string) => request<TrustedPerson[]>(`/patients/${patientId}/trusted`),
  trustedBy: (personId: string) => request<TrustedPerson[]>(`/people/${personId}/trusted-by`),
  removeTrusted: (patientId: string, trustedId: string) =>
    request<TrustedPerson[]>(`/patients/${patientId}/trusted/${trustedId}`, { method: "DELETE" }),
  sentInvites: (patientId: string) => request<SentInvite[]>(`/patients/${patientId}/trusted/invites`),
  invite: (patientId: string, phone: string) =>
    request<SentInvite>(`/patients/${patientId}/trusted/invites`, json("POST", { phone })),
  cancelInvite: (patientId: string, inviteId: string) =>
    request<void>(`/patients/${patientId}/trusted/invites/${inviteId}`, { method: "DELETE" }),
  myInvites: () => request<ReceivedInvite[]>("/invites"),
  acceptInvite: (id: string) => request<TrustedPerson[]>(`/invites/${id}/accept`, { method: "POST" }),
  declineInvite: (id: string) => request<void>(`/invites/${id}/decline`, { method: "POST" }),
  stepDown: (personId: string, patientId: string) =>
    request<TrustedPerson[]>(`/people/${personId}/trusted-by/${patientId}`, { method: "DELETE" }),
  meta: () => request<Meta>("/meta"),
  createVisitPrep: (patientId: string, symptoms: string, since: string | null) =>
    request<VisitPrep>(`/patients/${patientId}/visit-preps`, json("POST", { symptoms, since: since || null })),
  visitPreps: (patientId: string) => request<VisitPrep[]>(`/patients/${patientId}/visit-preps`),
  visitPrep: (id: string) => request<VisitPrep>(`/visit-preps/${id}`),
  deleteVisitPrep: (id: string) => request<void>(`/visit-preps/${id}`, { method: "DELETE" }),
  ask: (patientId: string, question: string, previous?: { question: string; points: string[] } | null) =>
    request<AskAnswer>(`/patients/${patientId}/ask`, json("POST", { question, previous: previous ?? null })),
  /** Speech to text for the "Speak instead" button. The recording is not kept. */
  voice: (patientId: string, audio: Blob) => {
    const form = new FormData();
    const type = audio.type || "audio/webm";
    const ext = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";
    form.append("audio", audio, `voice.${ext}`);
    return request<{ text: string; models_used: string[] }>(`/patients/${patientId}/voice`, { method: "POST", body: form });
  },
  medicineInfo: (name: string, generics: string[]) => {
    const q = new URLSearchParams({ name });
    generics.forEach((g) => q.append("generics", g));
    return request<MedicineInfo>(`/medicine-info?${q}`);
  },
};
