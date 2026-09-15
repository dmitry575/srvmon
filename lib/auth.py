"""Sign-in.

The password is stored only as a PBKDF2-HMAC-SHA256 hash with a per-user
salt; it cannot be recovered from the file. A session is a random token kept
in the database — only that token travels in the cookie.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from . import store

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USERS_PATH = os.path.join(BASE, "var", "users.json")
ITERATIONS = 240_000
SESSION_TTL = 12 * 3600


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return "pbkdf2_sha256$%d$%s$%s" % (
        ITERATIONS, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(password, stored):
    try:
        algo, iters, salt_b64, dk_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 base64.b64decode(salt_b64), int(iters))
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except (ValueError, TypeError):
        return False


def load_users():
    if not os.path.exists(USERS_PATH):
        return {}
    try:
        with open(USERS_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_users(users):
    os.makedirs(os.path.dirname(USERS_PATH), exist_ok=True)
    tmp = USERS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(users, fh, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, USERS_PATH)


def set_user(login, password):
    users = load_users()
    users[login] = {"password": hash_password(password), "created": int(time.time())}
    save_users(users)


def authenticate(login, password):
    user = load_users().get(login)
    if not user:
        # Hash anyway: otherwise response timing reveals whether the login exists
        hash_password(password)
        return False
    return verify_password(password, user["password"])


def create_session(login):
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    store.write("INSERT INTO sessions(token,user,created,expires) VALUES(?,?,?,?)",
                (token, login, now, now + SESSION_TTL))
    return token, SESSION_TTL


def session_user(token):
    if not token:
        return None
    row = store.one("SELECT user, expires FROM sessions WHERE token=?", (token,))
    if not row or row["expires"] < time.time():
        return None
    return row["user"]


def drop_session(token):
    if token:
        store.write("DELETE FROM sessions WHERE token=?", (token,))
