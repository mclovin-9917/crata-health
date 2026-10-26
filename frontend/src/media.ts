import type { Encounter, SourceItem } from "./api";

// A visit's photos and recording, kept only in this browser: the server never stores them.
// They stay until every suggestion from the visit is confirmed or rejected (decided 2026-10-12),
// so the patient can see the proof and ask for a careful re-read, then they are deleted.
// IndexedDB keeps them across reloads on this device; where it is unavailable (some private
// windows) they live only in this tab.

type Kind = "image" | "audio";
interface Saved { sourceId: string; encounterId: string; kind: Kind; file: File }
interface Local extends Saved { url: string }

const memory = new Map<string, Local>(); // by source id

const DB_NAME = "healthrecord-media";
const STORE = "files";
let opening: Promise<IDBDatabase | null> | null = null;

function openDb(): Promise<IDBDatabase | null> {
  opening ??= new Promise((resolve) => {
    try {
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => {
        req.result.createObjectStore(STORE, { keyPath: "sourceId" }).createIndex("encounterId", "encounterId");
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => resolve(null);
      req.onblocked = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
  return opening;
}

/** One request on the store; null when IndexedDB is unavailable or the request fails. */
async function withStore<T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T | null> {
  const db = await openDb();
  if (!db) return null;
  return new Promise((resolve) => {
    try {
      const req = fn(db.transaction(STORE, mode).objectStore(STORE));
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}

function remember(s: Saved) {
  if (!memory.has(s.sourceId)) memory.set(s.sourceId, { ...s, url: URL.createObjectURL(s.file) });
}

export const visitMedia = {
  /** Keep a file just uploaded for a visit. */
  async keep(encounterId: string, sourceId: string, file: File, kind: Kind) {
    const saved: Saved = { sourceId, encounterId, kind, file };
    remember(saved);
    await withStore("readwrite", (s) => s.put(saved));
  },

  /** Load this visit's files from the browser's storage into the tab (after a reload). */
  async restore(encounterId: string) {
    const rows = await withStore<Saved[]>("readonly", (s) => s.index("encounterId").getAll(encounterId));
    rows?.forEach(remember);
  },

  /** The file behind a source. A careful re-read is a new source for the same file. */
  of(source: SourceItem | undefined): Local | undefined {
    if (!source) return undefined;
    return memory.get(source.id) ?? (source.reread_of ? memory.get(source.reread_of) : undefined);
  },

  /** The original files of a visit, in the order of its sources, if this browser has them all. */
  originals(enc: Encounter): Local[] | null {
    const wanted = enc.sources.filter((s) => !s.reread_of && (s.source_type === "image" || s.source_type === "audio"));
    const found = wanted.map((s) => memory.get(s.id));
    return wanted.length && found.every(Boolean) ? (found as Local[]) : null;
  },

  /** Delete a visit's files from the browser's storage. The open tab keeps its copy until it
   * closes, so the proof does not vanish the moment the last suggestion is reviewed. */
  async drop(encounterId: string) {
    const keys = await withStore<IDBValidKey[]>("readonly", (s) => s.index("encounterId").getAllKeys(encounterId));
    for (const k of keys ?? []) await withStore("readwrite", (s) => s.delete(k));
  },

  /** Delete the files of every visit that is settled (or gone). Run once at start-up. */
  async sweep(settled: (encounterId: string) => Promise<boolean>) {
    const rows = await withStore<Saved[]>("readonly", (s) => s.getAll());
    const ids = new Set((rows ?? []).map((r) => r.encounterId));
    for (const id of ids) if (await settled(id)) await this.drop(id);
  },
};

/** Settled: read, with at least one suggestion, none still waiting for review, and no fact read
 * as it is that still carries a doubt (an amber note: its photo is the proof the patient needs). A
 * visit where nothing was found keeps its files, so it can still be read again carefully. */
export function isSettled(enc: Encounter): boolean {
  const doubtful = new Set(Object.keys(enc.flagged ?? {}));
  return (
    enc.status === "ready" &&
    enc.entries.length > 0 &&
    enc.entries.every((e) => e.status !== "proposed" && !(e.added_as_read && e.status === "confirmed" && doubtful.has(e.id)))
  );
}

/** Suggestions a careful re-read replaced: rejected by the system, not by the patient. */
export function wasReplaced(e: Encounter["entries"][number]): boolean {
  const last = e.status_history[e.status_history.length - 1];
  return e.status === "rejected" && last?.by === "system";
}
