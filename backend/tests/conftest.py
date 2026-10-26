from fastapi.testclient import TestClient

from app.auth import COOKIE
from app.schemas.account import Account, AccountRole, Session

API = "http://testserver/api"


def api_client(app, *, person: str | None = None, account: Account | None = None) -> TestClient:
    """A client whose paths are relative to /api, e.g. client.get('/patients').

    `person` signs in as a patient account for that person's record; `account` as that account.
    Without either, the client is signed out.
    """
    client = TestClient(app, base_url=API)
    if account is None and person is not None:
        account = Account(role=AccountRole.PATIENT, name="Test patient", person_id=person)
    if account is not None:
        sign_in_as(client, account)
    return client


def sign_in_as(client: TestClient, account: Account) -> Account:
    """Switch this client to `account` (saved if new), as the sign-in routes would."""
    db = client.app.state.storage
    if db.get_account(account.id) is None:
        db.save_account(account)
    session, token = Session.start(account)
    db.save_session(session)
    client.cookies.set(COOKIE, token)
    return account
