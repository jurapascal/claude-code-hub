"""
Účty na bráně — kdo se smí připojit a čím se prokáže.

Nic z toho nepotřebuje knihovnu navíc: sqlite3 i scrypt jsou ve standardní
knihovně Pythonu, takže brána zůstává tím, čím je zbytek repa — spustitelná
na holém Pythonu.

Dvě věci, které stojí za vysvětlení:

* **Heslo se neukládá, ukládá se scrypt.** Kdyby někdo databázi odnesl, nemá
  z ní hesla. Parametry (N=2^15) jsou zvolené tak, aby ověření trvalo kolem
  desetiny vteřiny — dost na to, aby hádání po síti nemělo smysl, a málo na
  to, aby to při přihlášení bylo znát.

* **Token se taky ukládá jen jako otisk.** Token je heslo pro telefon: kdo ho
  má, je přihlášený. V databázi proto leží jen jeho SHA-256, stejně jako
  u hesla — z ukradené databáze se přihlásit nedá.
"""
import hashlib
import hmac
import os
import secrets
import sqlite3
import time

# N=2^15, r=8 → scrypt si řekne o 128*N*r = přesně 32 MiB, což je zároveň
# výchozí strop OpenSSL. Bez `maxmem` to spadne na „memory limit exceeded“,
# tak se strop říká explicitně a s rezervou.
SCRYPT = dict(n=2 ** 15, r=8, p=1, dklen=32, maxmem=128 * 1024 * 1024)
TOKEN_BYTES = 32
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id       INTEGER PRIMARY KEY,
    email    TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name     TEXT NOT NULL DEFAULT '',
    salt     BLOB NOT NULL,
    hash     BLOB NOT NULL,
    created  REAL NOT NULL,
    disabled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tokens (
    fingerprint BLOB PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label       TEXT NOT NULL DEFAULT '',
    created     REAL NOT NULL,
    seen        REAL NOT NULL
);
"""


def _hash(password, salt):
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, **SCRYPT)


def _fingerprint(token):
    return hashlib.sha256(token.encode("utf-8")).digest()


class Accounts:
    """Účty a přihlašovací tokeny. Bezpečné volat z více vláken."""

    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        # check_same_thread=False + serialized režim: brána obsluhuje každé
        # spojení ve vlastním vlákně a sqlite3 si zamykání pořeší samo.
        self.db = sqlite3.connect(path, check_same_thread=False,
                                  isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)

    # ── uživatelé ────────────────────────────────────────────────────────────
    def add(self, email, password, name=""):
        """Založí účet. Vrací id, nebo vyhodí ValueError."""
        email = (email or "").strip().lower()
        if "@" not in email:
            raise ValueError("E-mail nevypadá jako e-mail.")
        if len(password or "") < 10:
            raise ValueError("Heslo musí mít aspoň 10 znaků.")
        salt = secrets.token_bytes(16)
        try:
            cur = self.db.execute(
                "INSERT INTO users (email, name, salt, hash, created)"
                " VALUES (?, ?, ?, ?, ?)",
                (email, name or "", salt, _hash(password, salt), time.time()))
        except sqlite3.IntegrityError:
            raise ValueError(f"{email} už účet má.") from None
        return cur.lastrowid

    def set_password(self, email, password):
        if len(password or "") < 10:
            raise ValueError("Heslo musí mít aspoň 10 znaků.")
        salt = secrets.token_bytes(16)
        cur = self.db.execute(
            "UPDATE users SET salt = ?, hash = ? WHERE email = ?",
            (salt, _hash(password, salt), (email or "").strip().lower()))
        if not cur.rowcount:
            raise ValueError(f"{email} tu žádný účet nemá.")
        # Změna hesla odhlašuje: jinak by ukradený token přežil i to, kvůli
        # čemu se heslo mění.
        self.db.execute(
            "DELETE FROM tokens WHERE user_id ="
            " (SELECT id FROM users WHERE email = ?)",
            ((email or "").strip().lower(),))

    def set_disabled(self, email, disabled=True):
        email = (email or "").strip().lower()
        cur = self.db.execute("UPDATE users SET disabled = ? WHERE email = ?",
                              (1 if disabled else 0, email))
        if not cur.rowcount:
            raise ValueError(f"{email} tu žádný účet nemá.")
        if disabled:
            self.db.execute(
                "DELETE FROM tokens WHERE user_id ="
                " (SELECT id FROM users WHERE email = ?)", (email,))

    def remove(self, email):
        cur = self.db.execute("DELETE FROM users WHERE email = ?",
                              ((email or "").strip().lower(),))
        if not cur.rowcount:
            raise ValueError(f"{email} tu žádný účet nemá.")

    def list(self):
        return [dict(r) for r in self.db.execute(
            "SELECT id, email, name, created, disabled FROM users"
            " ORDER BY email")]

    # ── přihlášení ───────────────────────────────────────────────────────────
    def verify(self, email, password):
        """Uživatel při správném heslu, jinak None. Trvá stejně tak či tak."""
        row = self.db.execute(
            "SELECT * FROM users WHERE email = ?",
            ((email or "").strip().lower(),)).fetchone()
        # I na neexistující e-mail se počítá scrypt, aby se z délky odpovědi
        # nedalo poznat, které účty existují.
        salt = row["salt"] if row else b"\0" * 16
        want = row["hash"] if row else b"\0" * SCRYPT["dklen"]
        ok = hmac.compare_digest(_hash(password or "", salt), want)
        if not row or not ok or row["disabled"]:
            return None
        return {"id": row["id"], "email": row["email"], "name": row["name"]}

    def login(self, email, password, label=""):
        """Ověří heslo a vydá token pro zařízení. None = nepustit dál."""
        user = self.verify(email, password)
        if not user:
            return None
        token = secrets.token_urlsafe(TOKEN_BYTES)
        now = time.time()
        self.db.execute(
            "INSERT INTO tokens (fingerprint, user_id, label, created, seen)"
            " VALUES (?, ?, ?, ?, ?)",
            (_fingerprint(token), user["id"], label or "", now, now))
        return token

    def user_for_token(self, token):
        """Komu token patří, nebo None. Zaznamená, že se ozval."""
        if not token:
            return None
        row = self.db.execute(
            "SELECT u.id, u.email, u.name, u.disabled FROM tokens t"
            " JOIN users u ON u.id = t.user_id WHERE t.fingerprint = ?",
            (_fingerprint(token),)).fetchone()
        if not row or row["disabled"]:
            return None
        self.db.execute("UPDATE tokens SET seen = ? WHERE fingerprint = ?",
                        (time.time(), _fingerprint(token)))
        return {"id": row["id"], "email": row["email"], "name": row["name"]}

    def revoke(self, token):
        self.db.execute("DELETE FROM tokens WHERE fingerprint = ?",
                        (_fingerprint(token),))

    def devices(self, email):
        return [dict(r) for r in self.db.execute(
            "SELECT t.label, t.created, t.seen FROM tokens t JOIN users u"
            " ON u.id = t.user_id WHERE u.email = ? ORDER BY t.seen DESC",
            ((email or "").strip().lower(),))]
