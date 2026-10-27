"""SQLite storage. Each object is stored whole as JSON, plus the few columns we look things up by.

The rest of the app talks to `Storage` only, so the layout can change (or move to another
database) without touching the API or the pipeline. No media ever reaches this layer.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, TypeAdapter

from app.ai.search import SearchResult
from app.schemas.access import (
    AccessLogEntry,
    Alert,
    EmergencyCode,
    TrustedInvite,
    TrustedLink,
    append_log,
)
from app.schemas.account import Account, CodeChallenge, Session
from app.schemas.assist import VisitPrep
from app.schemas.medicine import BrandMatch, MedicineInfo
from app.schemas.patient import Patient
from app.schemas.record import Encounter, EncounterStatus, Entry, EntryStatus

T = TypeVar("T", bound=BaseModel)
_RESULTS = TypeAdapter(list[SearchResult])

_SCHEMA = """
CREATE TABLE IF NOT EXISTS patients (
    id TEXT PRIMARY KEY, sandbox_id TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS encounters (
    id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, encounter_id TEXT, status TEXT NOT NULL,
    data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS access_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, patient_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY, recipient_id TEXT NOT NULL, at TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS emergency_codes (
    id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY, phone TEXT UNIQUE, demo_key TEXT UNIQUE, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, account_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS code_challenges (
    id TEXT PRIMARY KEY, phone TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_sessions_account ON sessions (account_id);
CREATE INDEX IF NOT EXISTS ix_challenges_phone ON code_challenges (phone, created_at);
CREATE TABLE IF NOT EXISTS medicine_info (
    id TEXT PRIMARY KEY, data TEXT NOT NULL);  -- id: the medicine's key
CREATE TABLE IF NOT EXISTS brand_matches (
    id TEXT PRIMARY KEY, data TEXT NOT NULL);  -- id: the brand name's key
CREATE TABLE IF NOT EXISTS trusted_links (
    patient_id TEXT NOT NULL, trusted_id TEXT NOT NULL, PRIMARY KEY (patient_id, trusted_id));
CREATE TABLE IF NOT EXISTS trusted_invites (
    id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, phone TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_invites_patient ON trusted_invites (patient_id);
CREATE INDEX IF NOT EXISTS ix_invites_phone ON trusted_invites (phone);
CREATE TABLE IF NOT EXISTS visit_preps (
    id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_visit_preps_patient ON visit_preps (patient_id);
CREATE TABLE IF NOT EXISTS ask_log (  -- one row per question asked, for the daily limit
    seq INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL, at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_ask_account ON ask_log (account_id, at);
CREATE TABLE IF NOT EXISTS search_cache (  -- id: the normalised query and its sites
    id TEXT PRIMARY KEY, data TEXT NOT NULL, fetched_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_encounters_patient ON encounters (patient_id, created_at);
CREATE INDEX IF NOT EXISTS ix_entries_patient ON entries (patient_id, status);
CREATE INDEX IF NOT EXISTS ix_entries_encounter ON entries (encounter_id);
CREATE INDEX IF NOT EXISTS ix_access_patient ON access_log (patient_id, seq);
CREATE INDEX IF NOT EXISTS ix_alerts_recipient ON alerts (recipient_id, at);
"""


class Storage:
    """One connection shared across threads (FastAPI runs background tasks in a thread pool),
    guarded by a lock. Fine for one process and demo traffic."""

    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.RLock()
        with self._tx() as db:
            db.executescript(_SCHEMA)

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """Lock plus transaction: commits on success, rolls back on error."""
        with self._lock, self._conn:
            yield self._conn

    def _one(self, model: type[T], sql: str, *args: object) -> T | None:
        with self._tx() as db:
            row = db.execute(sql, args).fetchone()
        return model.model_validate_json(row[0]) if row else None

    def _many(self, model: type[T], sql: str, *args: object) -> list[T]:
        with self._tx() as db:
            rows = db.execute(sql, args).fetchall()
        return [model.model_validate_json(r[0]) for r in rows]

    def close(self) -> None:
        self._conn.close()

    # ---- patients ---------------------------------------------------------
    def save_patient(self, p: Patient) -> None:
        with self._tx() as db:
            _upsert(db, "patients", id=p.id, sandbox_id=p.sandbox_id, data=p.model_dump_json())

    def get_patient(self, patient_id: str) -> Patient | None:
        return self._one(Patient, "SELECT data FROM patients WHERE id = ?", patient_id)

    def list_patients(self, sandbox_id: str | None = None) -> list[Patient]:
        return self._many(
            Patient, "SELECT data FROM patients WHERE sandbox_id IS ? ORDER BY id", sandbox_id
        )

    # ---- accounts, sessions, one-time codes ------------------------------
    def save_account(self, a: Account) -> None:
        with self._tx() as db:
            _upsert(db, "accounts", id=a.id, phone=a.phone, demo_key=a.demo_key,
                    data=a.model_dump_json())

    def get_account(self, account_id: str) -> Account | None:
        return self._one(Account, "SELECT data FROM accounts WHERE id = ?", account_id)

    def account_by_phone(self, phone: str) -> Account | None:
        return self._one(Account, "SELECT data FROM accounts WHERE phone = ?", phone)

    def account_by_demo_key(self, key: str) -> Account | None:
        return self._one(Account, "SELECT data FROM accounts WHERE demo_key = ?", key)

    def account_by_person(self, person_id: str) -> Account | None:
        return self._one(Account, "SELECT data FROM accounts WHERE json_extract(data, "
                                  "'$.person_id') = ?", person_id)

    def demo_accounts(self) -> list[Account]:
        return self._many(Account, "SELECT data FROM accounts WHERE demo_key IS NOT NULL "
                                   "ORDER BY rowid")

    def save_session(self, s: Session) -> None:
        with self._tx() as db:
            _upsert(db, "sessions", id=s.id, account_id=s.account_id, data=s.model_dump_json())

    def get_session(self, session_id: str) -> Session | None:
        return self._one(Session, "SELECT data FROM sessions WHERE id = ?", session_id)

    def delete_session(self, session_id: str) -> None:
        with self._tx() as db:
            db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def save_challenge(self, c: CodeChallenge) -> None:
        with self._tx() as db:
            _upsert(db, "code_challenges", id=c.id, phone=c.phone,
                    created_at=c.created_at.isoformat(), data=c.model_dump_json())

    def get_challenge(self, challenge_id: str) -> CodeChallenge | None:
        return self._one(CodeChallenge, "SELECT data FROM code_challenges WHERE id = ?",
                         challenge_id)

    def count_challenges(self, phone: str, since: datetime) -> int:
        with self._tx() as db:
            row = db.execute("SELECT COUNT(*) FROM code_challenges WHERE phone = ? AND "
                             "created_at >= ?", (phone, since.isoformat())).fetchone()
        return int(row[0])

    # ---- medicine summaries (about a medicine, not a patient) -------------
    def save_medicine_info(self, info: MedicineInfo) -> None:
        with self._tx() as db:
            _upsert(db, "medicine_info", id=info.key, data=info.model_dump_json())

    def get_medicine_info(self, key: str) -> MedicineInfo | None:
        return self._one(MedicineInfo, "SELECT data FROM medicine_info WHERE id = ?", key)

    def save_brand_match(self, m: BrandMatch) -> None:
        with self._tx() as db:
            _upsert(db, "brand_matches", id=m.key, data=m.model_dump_json())

    def get_brand_match(self, key: str) -> BrandMatch | None:
        return self._one(BrandMatch, "SELECT data FROM brand_matches WHERE id = ?", key)

    # ---- before a visit, and Ask about my health --------------------------
    def save_visit_prep(self, prep: VisitPrep) -> None:
        with self._tx() as db:
            _upsert(db, "visit_preps", id=prep.id, patient_id=prep.patient_id,
                    data=prep.model_dump_json())

    def get_visit_prep(self, prep_id: str) -> VisitPrep | None:
        return self._one(VisitPrep, "SELECT data FROM visit_preps WHERE id = ?", prep_id)

    def list_visit_preps(self, patient_id: str) -> list[VisitPrep]:
        """Newest first."""
        preps = self._many(VisitPrep, "SELECT data FROM visit_preps WHERE patient_id = ?",
                           patient_id)
        return sorted(preps, key=lambda p: p.created_at, reverse=True)

    def delete_visit_prep(self, prep_id: str) -> None:
        with self._tx() as db:
            db.execute("DELETE FROM visit_preps WHERE id = ?", (prep_id,))

    def record_ask(self, account_id: str, at: datetime) -> None:
        with self._tx() as db:
            db.execute("INSERT INTO ask_log (account_id, at) VALUES (?, ?)",
                       (account_id, at.isoformat()))

    def count_asks(self, account_id: str, since: datetime) -> int:
        with self._tx() as db:
            row = db.execute("SELECT COUNT(*) FROM ask_log WHERE account_id = ? AND at >= ?",
                             (account_id, since.isoformat())).fetchone()
        return int(row[0])

    def get_search_cache(self, key: str, newer_than: datetime) -> list[SearchResult] | None:
        """The saved results of one search, if fetched after `newer_than`."""
        with self._tx() as db:
            row = db.execute("SELECT data FROM search_cache WHERE id = ? AND fetched_at >= ?",
                             (key, newer_than.isoformat())).fetchone()
        return _RESULTS.validate_json(row[0]) if row else None

    def save_search_cache(self, key: str, results: list[SearchResult], at: datetime) -> None:
        with self._tx() as db:
            _upsert(db, "search_cache", id=key, data=_RESULTS.dump_json(results).decode(),
                    fetched_at=at.isoformat())

    # ---- entries ----------------------------------------------------------
    def save_entry(self, e: Entry) -> None:
        with self._tx() as db:
            _upsert_entry(db, e)

    def update_entry(self, e: Entry) -> bool:
        """Save changes to an entry that is still stored; never insert. A background job that
        finishes after the patient deleted the visit must not bring its entries back."""
        with self._tx() as db:
            cur = db.execute(
                "UPDATE entries SET status = ?, data = ? WHERE id = ?",
                (e.status.value, e.model_dump_json(), e.id),
            )
        return cur.rowcount > 0

    def get_entry(self, entry_id: str) -> Entry | None:
        return self._one(Entry, "SELECT data FROM entries WHERE id = ?", entry_id)

    def list_entries(self, patient_id: str, status: EntryStatus | None = None) -> list[Entry]:
        if status is None:
            return self._many(
                Entry, "SELECT data FROM entries WHERE patient_id = ? ORDER BY rowid", patient_id
            )
        return self._many(
            Entry,
            "SELECT data FROM entries WHERE patient_id = ? AND status = ? ORDER BY rowid",
            patient_id,
            status.value,
        )

    # ---- encounters -------------------------------------------------------
    def save_encounter(self, enc: Encounter) -> None:
        """Entries go to their own table (they are confirmed and edited one by one)."""
        for e in enc.entries:
            e.patient_id, e.encounter_id = enc.patient_id, enc.id
        with self._tx() as db:
            _upsert(
                db,
                "encounters",
                id=enc.id,
                patient_id=enc.patient_id,
                created_at=enc.created_at.isoformat(),
                data=enc.model_dump_json(exclude={"entries"}),
            )
            for e in enc.entries:
                _upsert_entry(db, e)

    def get_encounter(self, encounter_id: str) -> Encounter | None:
        enc = self._one(Encounter, "SELECT data FROM encounters WHERE id = ?", encounter_id)
        if enc is not None:
            enc.entries = self._many(
                Entry,
                "SELECT data FROM entries WHERE encounter_id = ? ORDER BY rowid",
                encounter_id,
            )
        return enc

    def processing_encounters(self) -> list[Encounter]:
        """Visits marked as being read (without their entries). On a start there are no jobs
        running, so any of these was interrupted."""
        return [e for e in self._many(Encounter, "SELECT data FROM encounters")
                if e.status == EncounterStatus.PROCESSING]

    def delete_encounter(self, encounter_id: str) -> None:
        """Erase a visit for good: the visit, its entries and the text read from its files."""
        with self._tx() as db:
            db.execute("DELETE FROM entries WHERE encounter_id = ?", (encounter_id,))
            db.execute("DELETE FROM encounters WHERE id = ?", (encounter_id,))

    def list_encounters(self, patient_id: str) -> list[Encounter]:
        """Newest first, without their entries (use get_encounter for one in full)."""
        return self._many(
            Encounter,
            "SELECT data FROM encounters WHERE patient_id = ? ORDER BY created_at DESC",
            patient_id,
        )

    # ---- access log (hash-chained, append-only) ---------------------------
    def append_access(self, entry: AccessLogEntry) -> AccessLogEntry:
        """Chain to the patient's last entry and store, atomically."""
        with self._tx() as db:
            row = db.execute(
                "SELECT data FROM access_log WHERE patient_id = ? ORDER BY seq DESC LIMIT 1",
                (entry.patient_id,),
            ).fetchone()
            tail = [AccessLogEntry.model_validate_json(row[0])] if row else []
            append_log(tail, entry)
            db.execute(
                "INSERT INTO access_log (patient_id, data) VALUES (?, ?)",
                (entry.patient_id, entry.model_dump_json()),
            )
        return entry

    def access_log(self, patient_id: str) -> list[AccessLogEntry]:
        """Oldest first, the order `verify_chain` expects."""
        return self._many(
            AccessLogEntry,
            "SELECT data FROM access_log WHERE patient_id = ? ORDER BY seq",
            patient_id,
        )

    # ---- alerts -----------------------------------------------------------
    def save_alert(self, a: Alert) -> None:
        with self._tx() as db:
            _upsert(
                db,
                "alerts",
                id=a.id,
                recipient_id=a.recipient_id,
                at=a.at.isoformat(),
                data=a.model_dump_json(),
            )

    def get_alert(self, alert_id: str) -> Alert | None:
        return self._one(Alert, "SELECT data FROM alerts WHERE id = ?", alert_id)

    def list_alerts(self, recipient_id: str) -> list[Alert]:
        """Newest first."""
        return self._many(
            Alert,
            "SELECT data FROM alerts WHERE recipient_id = ? ORDER BY at DESC",
            recipient_id,
        )

    # ---- emergency codes (short-lived, single use) -------------------------
    def save_code(self, c: EmergencyCode) -> None:
        with self._tx() as db:
            _upsert(db, "emergency_codes", id=c.code, patient_id=c.patient_id,
                    data=c.model_dump_json())

    def get_code(self, code: str) -> EmergencyCode | None:
        return self._one(EmergencyCode, "SELECT data FROM emergency_codes WHERE id = ?", code)

    def use_code(self, code: str, patient_id: str, used_by: str) -> EmergencyCode | None:
        """Mark a code used, in one locked step so it can only ever be used once. None if it
        does not exist, belongs to another patient, has expired or was used already."""
        with self._tx() as db:
            row = db.execute("SELECT data FROM emergency_codes WHERE id = ?", (code,)).fetchone()
            c = EmergencyCode.model_validate_json(row[0]) if row else None
            if c is None or c.patient_id != patient_id or not c.usable():
                return None
            c.used_at, c.used_by = datetime.now(UTC), used_by
            _upsert(db, "emergency_codes", id=c.code, patient_id=c.patient_id,
                    data=c.model_dump_json())
        return c

    # ---- trusted people ---------------------------------------------------
    def add_trusted(self, link: TrustedLink) -> None:
        with self._tx() as db:
            db.execute(
                "INSERT OR IGNORE INTO trusted_links (patient_id, trusted_id) VALUES (?, ?)",
                (link.patient_id, link.trusted_id),
            )

    def trusted_people(self, patient_id: str) -> list[str]:
        with self._tx() as db:
            rows = db.execute(
                "SELECT trusted_id FROM trusted_links WHERE patient_id = ? ORDER BY trusted_id",
                (patient_id,),
            ).fetchall()
        return [r[0] for r in rows]

    def patients_trusting(self, trusted_id: str) -> list[str]:
        with self._tx() as db:
            rows = db.execute(
                "SELECT patient_id FROM trusted_links WHERE trusted_id = ? ORDER BY patient_id",
                (trusted_id,),
            ).fetchall()
        return [r[0] for r in rows]

    def remove_trusted(self, patient_id: str, trusted_id: str) -> bool:
        """End a trust link. True if there was one."""
        with self._tx() as db:
            cur = db.execute(
                "DELETE FROM trusted_links WHERE patient_id = ? AND trusted_id = ?",
                (patient_id, trusted_id),
            )
        return cur.rowcount > 0

    # ---- invitations to be a trusted person --------------------------------
    def save_invite(self, inv: TrustedInvite) -> None:
        with self._tx() as db:
            _upsert(db, "trusted_invites", id=inv.id, patient_id=inv.patient_id, phone=inv.phone,
                    data=inv.model_dump_json())

    def get_invite(self, invite_id: str) -> TrustedInvite | None:
        return self._one(TrustedInvite, "SELECT data FROM trusted_invites WHERE id = ?", invite_id)

    def invites_from(self, patient_id: str) -> list[TrustedInvite]:
        return self._many(TrustedInvite, "SELECT data FROM trusted_invites WHERE patient_id = ? "
                          "ORDER BY rowid", patient_id)

    def invites_to(self, phone: str) -> list[TrustedInvite]:
        return self._many(TrustedInvite, "SELECT data FROM trusted_invites WHERE phone = ? "
                          "ORDER BY rowid", phone)

    # ---- demo reset --------------------------------------------------------
    def clear_people(self, person_ids: list[str]) -> None:
        """Remove everything these people have: visits, entries, access log, alerts, codes,
        trust links and invitations they sent. Their accounts and sessions stay, so a signed-in
        demo visitor stays signed in. Used to build the demo people again on every start."""
        marks = ", ".join("?" * len(person_ids))
        with self._tx() as db:
            for table in ("entries", "encounters", "access_log", "emergency_codes",
                          "trusted_invites", "visit_preps"):
                db.execute(f"DELETE FROM {table} WHERE patient_id IN ({marks})", person_ids)
            db.execute(f"DELETE FROM alerts WHERE recipient_id IN ({marks}) "
                       f"OR json_extract(data, '$.patient_id') IN ({marks})", person_ids * 2)
            db.execute(f"DELETE FROM trusted_links WHERE patient_id IN ({marks}) "
                       f"OR trusted_id IN ({marks})", person_ids * 2)
            db.execute(f"DELETE FROM patients WHERE id IN ({marks})", person_ids)


def _upsert(db: sqlite3.Connection, table: str, **row: object) -> None:
    """Insert, or update in place on the same id. Unlike INSERT OR REPLACE, the row keeps its
    position, so lists ordered by insertion (review cards) don't reshuffle on every save."""
    cols = list(row)
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "id")
    db.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}",
        tuple(row.values()),
    )


def _upsert_entry(db: sqlite3.Connection, e: Entry) -> None:
    if not e.patient_id:
        raise ValueError("An entry must belong to a patient before it is stored")
    _upsert(
        db,
        "entries",
        id=e.id,
        patient_id=e.patient_id,
        encounter_id=e.encounter_id,
        status=e.status.value,
        data=e.model_dump_json(),
    )
