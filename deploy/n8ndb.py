"""Read-only summary of the n8n database (copy of it): users, signing keys, workflows. Prints no secrets.
Usage: python3 deploy/n8ndb.py <path to the n8n data folder>"""
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

src = Path(sys.argv[1])
tmp = Path(tempfile.mkdtemp())
for name in ("database.sqlite", "database.sqlite-wal", "database.sqlite-shm"):
    if (src / name).exists():
        shutil.copy2(src / name, tmp / name)
db = sqlite3.connect(tmp / "database.sqlite")
tables = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}


def mask(email):
    if not email or "@" not in email:
        return repr(email)
    user, dom = email.split("@", 1)
    return f"{user[:3]}…@{dom}"


print("users:")
for r in db.execute("select email, disabled, mfaEnabled, roleSlug, password is not null, createdAt, lastActiveAt from user"):
    print(f"  {mask(r[0])} disabled={r[1]} mfa={r[2]} role={r[3]} has_password={r[4]} created={r[5]} last_active={r[6]}")
if "invalid_auth_token" in tables:
    print("invalid tokens:", db.execute("select count(*) from invalid_auth_token").fetchone()[0])
if "deployment_key" in tables:
    print("signing keys:")
    for r in db.execute("select type, status, algorithm, createdAt, updatedAt from deployment_key"):
        print("  ", *r)
print("workflows:")
for r in db.execute("select id, name, active from workflow_entity"):
    print("  ", *r)
for key in ("userManagement.isInstanceOwnerSetUp", "mfa.enforced"):
    row = db.execute("select value from settings where key=?", (key,)).fetchone()
    print(f"{key} = {row[0] if row else '-'}")
shutil.rmtree(tmp)
