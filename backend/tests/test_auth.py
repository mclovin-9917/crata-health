from datetime import UTC, datetime, timedelta

import pytest

from app.api.auth import seed_demo_accounts
from app.auth import COOKIE, AuthError, check_code, normalize_phone, send_code
from app.config import Settings
from app.main import create_app
from app.schemas.account import Account, AccountRole, CodeChallenge, Session, digest
from app.schemas.patient import Patient
from app.seed import seed_demo
from app.sms import SimulatedSms
from app.storage import Storage
from tests.conftest import api_client

PHONE = "+919800000001"  # synthetic


def _client():
    db, sms = Storage(), SimulatedSms()
    return api_client(create_app(db, sms=sms)), db, sms


def _code(client, phone="98000 00001"):
    r = client.post("/auth/code", json={"phone": phone})
    assert r.status_code == 200, r.text
    return r.json()


def test_phone_numbers():
    assert normalize_phone("98000 00001") == PHONE
    assert normalize_phone("098000-00001") == PHONE
    assert normalize_phone("+91 98000 00001") == PHONE
    assert normalize_phone("919800000001") == PHONE
    assert normalize_phone("+44 7700 900123") == "+447700900123"
    for bad in ["12345", "5800000000", "+0123456789", "phone"]:
        with pytest.raises(AuthError):
            normalize_phone(bad)


def test_new_number_signs_up_as_a_patient():
    client, db, sms = _client()
    sent = _code(client)
    assert sent["phone"] == "+91 ••••• 001"
    code = sent["simulated_code"]
    assert len(code) == 6 and code in sms.sent[PHONE]  # the "SMS" carries the code

    wrong = client.post("/auth/verify", json={"challenge_id": sent["challenge_id"], "code": "000000"
                                              if code != "000000" else "111111"})
    assert wrong.status_code == 400 and "4 tries left" in wrong.json()["detail"]
    r = client.post("/auth/verify", json={"challenge_id": sent["challenge_id"], "code": code})
    assert r.json() == {"status": "needs_profile", "me": None}
    assert client.get("/auth/me").status_code == 401  # not signed in until the profile exists

    me = client.post("/auth/signup", json={"challenge_id": sent["challenge_id"], "role": "patient",
                                           "name": "Meera Iyer", "birth_year": 1990}).json()
    assert me["role"] == "patient" and me["name"] == "Meera Iyer" and not me["admin"]
    assert db.get_patient(me["person_id"]).birth_year == 1990  # her own record
    assert client.get("/auth/me").json()["id"] == me["id"]

    # The same challenge cannot make a second account.
    again = client.post("/auth/signup", json={"challenge_id": sent["challenge_id"],
                                              "role": "patient", "name": "Someone"})
    assert again.status_code in (400, 409)


def test_known_number_signs_in_and_out():
    client, db, _ = _client()
    db.save_account(Account(role=AccountRole.PATIENT, name="Meera Iyer", phone=PHONE))
    sent = _code(client)
    r = client.post("/auth/verify", json={"challenge_id": sent["challenge_id"],
                                          "code": sent["simulated_code"]})
    assert r.json()["status"] == "signed_in"
    cookie = r.headers["set-cookie"]
    assert f"{COOKIE}=" in cookie and "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    assert client.get("/auth/me").status_code == 200

    token = client.cookies.get(COOKIE)
    assert db.get_session(token) is None and db.get_session(digest(token))  # only the digest
    assert client.post("/auth/logout").status_code == 204
    assert client.get("/auth/me").status_code == 401
    client.cookies.set(COOKIE, token)  # the old cookie is dead too
    assert client.get("/auth/me").status_code == 401


def test_doctors_sign_up_with_a_registry_number():
    client, _, _ = _client()
    sent = _code(client)
    client.post("/auth/verify", json={"challenge_id": sent["challenge_id"],
                                      "code": sent["simulated_code"]})
    body = {"challenge_id": sent["challenge_id"], "role": "doctor", "name": "Dr. K. Rao"}
    assert client.post("/auth/signup", json=body).status_code == 422
    me = client.post("/auth/signup", json={**body, "registry_id": "SYN-0042"}).json()
    assert me["role"] == "doctor" and me["registry_id"] == "SYN-0042" and me["person_id"] is None


def test_code_limits():
    db, sms = Storage(), SimulatedSms()
    now = datetime(2026, 10, 12, 10, 0, tzinfo=UTC)
    c, code = send_code(db, sms, PHONE, now)
    assert c.code_digest and code not in c.model_dump_json()  # only a digest is stored
    with pytest.raises(AuthError, match="expired"):
        check_code(db, c.id, code, now + timedelta(minutes=11))
    for _ in range(2):
        send_code(db, sms, PHONE, now)
    with pytest.raises(AuthError, match="Too many codes") as too_many:
        send_code(db, sms, PHONE, now)
    assert too_many.value.status == 429
    send_code(db, sms, PHONE, now + timedelta(minutes=16))  # the window has passed

    c, code = CodeChallenge.issue(PHONE, now)
    db.save_challenge(c)
    for _ in range(5):
        with pytest.raises(AuthError):
            check_code(db, c.id, "999999" if code != "999999" else "888888", now)
    with pytest.raises(AuthError, match="Too many wrong tries"):
        check_code(db, c.id, code, now)  # even the right code, after 5 wrong ones
    with pytest.raises(AuthError, match="already used or does not exist"):
        check_code(db, "nope", code, now)


def test_sessions_expire_by_role():
    patient = Account(role=AccountRole.PATIENT, name="P")
    doctor = Account(role=AccountRole.DOCTOR, name="D", registry_id="SYN-1")
    now = datetime(2026, 10, 12, tzinfo=UTC)
    p, _ = Session.start(patient, now)
    d, _ = Session.start(doctor, now)
    assert p.expires_at - now == timedelta(days=30) and d.expires_at - now == timedelta(hours=12)
    assert d.valid(now + timedelta(hours=11)) and not d.valid(now + timedelta(hours=13))


def test_demo_accounts():
    db = Storage()
    db.save_patient(Patient(id="demo-asha", name="Asha Verma"))
    db.save_patient(Patient(id="demo-ravi", name="Ravi Verma"))
    seed_demo_accounts(db)
    seed_demo_accounts(db)  # runs on every start; adds nothing twice
    client = api_client(create_app(db, sms=SimulatedSms()))
    listed = client.get("/auth/demo-accounts").json()
    # Meera, Kabir and Rohan have no record in this database, so no account.
    assert [(a["key"], a["role"]) for a in listed] == [
        ("asha", "patient"), ("ravi", "patient"), ("dr-mehta", "doctor"), ("dr-rao", "doctor")]
    me = client.post("/auth/demo", json={"key": "asha"}).json()
    assert me["person_id"] == "demo-asha" and me["demo"] and me["phone"] is None
    assert client.get("/auth/me").json()["name"] == "Asha Verma"
    assert client.post("/auth/demo", json={"key": "nobody"}).status_code == 404


def test_demo_people_get_the_testing_guide_numbers_unless_taken():
    db = Storage()
    taken = Account(role=AccountRole.PATIENT, name="Meera (registered)", phone="+919000010001")
    db.save_account(taken)
    seed_demo(db, visits=False)
    seed_demo_accounts(db)
    assert db.account_by_demo_key("kabir").phone == "+919000010002"
    assert db.account_by_demo_key("dr-rao").phone == "+919000010003"
    assert db.account_by_demo_key("meera").phone is None  # someone registered it first
    assert db.account_by_phone("+919000010001").id == taken.id

    # Signing in with Kabir's number reaches the demo Kabir.
    client = api_client(create_app(db, sms=SimulatedSms()))
    sent = client.post("/auth/code", json={"phone": "90000 10002"}).json()
    r = client.post("/auth/verify", json={"challenge_id": sent["challenge_id"],
                                          "code": sent["simulated_code"]}).json()
    assert r["status"] == "signed_in" and r["me"]["person_id"] == "demo-kabir"


def test_demo_accounts_can_be_switched_off(monkeypatch):
    db = Storage()
    seed_demo_accounts(db)  # no demo people: only the doctor
    monkeypatch.setattr("app.api.auth.get_settings",
                        lambda: Settings(_env_file=None, demo_logins=False))
    client = api_client(create_app(db, sms=SimulatedSms()))
    assert client.get("/auth/demo-accounts").json() == []
    assert client.post("/auth/demo", json={"key": "dr-mehta"}).status_code == 404


def test_expired_session_is_signed_out():
    db = Storage()
    account = Account(role=AccountRole.DOCTOR, name="D", registry_id="SYN-1")
    db.save_account(account)
    session, token = Session.start(account, datetime.now(UTC) - timedelta(hours=13))
    db.save_session(session)
    client = api_client(create_app(db, sms=SimulatedSms()))
    client.cookies.set(COOKIE, token)
    assert client.get("/auth/me").status_code == 401
