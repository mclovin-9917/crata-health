"""Make an account an admin (or not). Admin is never set from the app.

From the repo root:
    backend\\.venv\\Scripts\\python scripts\\set_admin.py --phone 98000 00000
    backend\\.venv\\Scripts\\python scripts\\set_admin.py --demo asha --off
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.auth import AuthError, normalize_phone
from app.config import get_settings
from app.storage import Storage


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument("--phone", help="The account's phone number")
    who.add_argument("--demo", help="A demo account key: asha, ravi, dr-mehta")
    parser.add_argument("--off", action="store_true", help="Take admin away instead")
    args = parser.parse_args()

    db = Storage(get_settings().database_path)
    try:
        account = (db.account_by_phone(normalize_phone(args.phone)) if args.phone
                   else db.account_by_demo_key(args.demo))
    except AuthError as exc:
        print(exc)
        return 1
    if account is None:
        print("No such account. Sign up in the app first.")
        return 1
    account.admin = not args.off
    db.save_account(account)
    print(f"{account.name}: admin {'on' if account.admin else 'off'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
