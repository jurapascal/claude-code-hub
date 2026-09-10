"""
Účty na bráně — kdo se smí připojit a čím se prokáže.

Nic z toho nepotřebuje knihovnu navíc: sqlite3 i scrypt jsou ve standardní
knihovně Pythonu, takže brána zůstává tím, čím je zbytek repa — spustitelná
na holém Pythonu.

Tři věci, které stojí za vysvětlení:

* **Heslo se neukládá, ukládá se scrypt.** Kdyby někdo databázi odnesl, nemá
  z ní hesla. Parametry (N=2^15) jsou zvolené tak, aby ověření trvalo kolem
  desetiny vteřiny — dost na to, aby hádání po síti nemělo smysl, a málo na
  to, aby to při přihlášení bylo znát.

* **Token se taky ukládá jen jako otisk.** Token je heslo pro telefon: kdo ho
  má, je přihlášený. V databázi proto leží jen jeho SHA-256, stejně jako
  u hesla — z ukradené databáze se přihlásit nedá.

* **Přístup k DB je serializovaný RLockem.** Jedno sdílené sqlite připojení
  volané z víc vláken naráz (prohlížeč pálí požadavky paralelně) padá na
  „Recursive use of cursors not allowed“ a brána pak vrací 502/401. Zámek to
  převede na frontu; dotazy jsou tak krátké, že to není znát.

Účet má navíc **roli** a **jméno vaultu**:

* `role` je `admin`, nebo `user`. Admin spravuje účty z CLI; role nerozhoduje
  o izolaci session — tu drží `isolation.py` bez ohledu na roli.
* `vault` je jméno Obsidian paměti daného uživatele.
"""
import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time

# N=2^15, r=8 → scrypt si řekne o 128*N*r = přesně 32 MiB, což je zároveň
# výchozí strop OpenSSL. Bez `maxmem` to spadne na „memory limit exceeded“,
# tak se strop říká explicitně a s rezervou.
SCRYPT = dict(n=2 ** 15, r=8, p=1, dklen=32, maxmem=128 * 1024 * 1024)
TOKEN_BYTES = 32
ROLES = ("user", "admin")
# Čím se v daném prostoru ověřuje Claude: `central` = sdílené předplatné brány,
# `own` = uživatel má v svém (izolovaném) domově vlastní přihlášení / API klíč.
AUTHS = ("central", "own")
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id       INTEGER PRIMARY KEY,
    email    TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name     TEXT NOT NULL DEFAULT '',
    role     TEXT NOT NULL DEFAULT 'user',
    vault    TEXT NOT NULL DEFAULT '',
    claude_auth TEXT NOT NULL DEFAULT 'central',
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


def _clean_role(role):
    role = (role or "user").strip().lower()
    return role if role in ROLES else "user"


def _clean_auth(auth):
    auth = (auth or "central").strip().lower()
    return auth if auth in AUTHS else "central"


class Accounts:
    """Účty a přihlašovací tokeny. Bezpečné volat z více vláken (RLock)."""

    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        # check_same_thread=False: brána obsluhuje každé spojení ve vlastním
        # vlákně. Jedno sdílené připojení ale NENÍ bezpečné volat z víc vláken
        # naráz — Python sqlite3 na souběžných execute() padá („Recursive use
        # of cursors not allowed“) a prohlížeč přitom pálí assety paralelně.
        # Proto všechny veřejné metody serializuje RLock (re-entrantní, aby
        # login() mohl zavolat verify() bez zaseknutí).
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False,
                                  isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self):
        """Doplní sloupce, které starší databáze ještě nemá."""
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(users)")}
        if "role" not in have:
            self.db.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL"
                            " DEFAULT 'user'")
        if "vault" not in have:
            self.db.execute("ALTER TABLE users ADD COLUMN vault TEXT NOT NULL"
                            " DEFAULT ''")
        if "claude_auth" not in have:
            self.db.execute("ALTER TABLE users ADD COLUMN claude_auth TEXT NOT"
                            " NULL DEFAULT 'central'")

    # ── uživatelé ────────────────────────────────────────────────────────────
    def add(self, email, password, name="", role="user", vault="",
            claude_auth="central"):
        """Založí účet. Vrací id, nebo vyhodí ValueError."""
        email = (email or "").strip().lower()
        if "@" not in email:
            raise ValueError("E-mail nevypadá jako e-mail.")
        if len(password or "") < 10:
            raise ValueError("Heslo musí mít aspoň 10 znaků.")
        salt = secrets.token_bytes(16)
        with self._lock:
            try:
                cur = self.db.execute(
                    "INSERT INTO users (email, name, role, vault, claude_auth,"
                    " salt, hash, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (email, name or "", _clean_role(role), (vault or "").strip(),
                     _clean_auth(claude_auth), salt, _hash(password, salt),
                     time.time()))
            except sqlite3.IntegrityError:
                raise ValueError(f"{email} už účet má.") from None
            return cur.lastrowid

    def set_auth(self, email, claude_auth):
        """Přepne, čím se v prostoru ověřuje Claude: central / own."""
        with self._lock:
            cur = self.db.execute(
                "UPDATE users SET claude_auth = ? WHERE email = ?",
                (_clean_auth(claude_auth), (email or "").strip().lower()))
            if not cur.rowcount:
                raise ValueError(f"{email} tu žádný účet nemá.")

    def set_password(self, email, password):
        if len(password or "") < 10:
            raise ValueError("Heslo musí mít aspoň 10 znaků.")
        salt = secrets.token_bytes(16)
        email = (email or "").strip().lower()
        with self._lock:
            cur = self.db.execute(
                "UPDATE users SET salt = ?, hash = ? WHERE email = ?",
                (salt, _hash(password, salt), email))
            if not cur.rowcount:
                raise ValueError(f"{email} tu žádný účet nemá.")
            # Změna hesla odhlašuje: jinak by ukradený token přežil i to, kvůli
            # čemu se heslo mění.
            self.db.execute(
                "DELETE FROM tokens WHERE user_id ="
                " (SELECT id FROM users WHERE email = ?)", (email,))

    def set_role(self, email, role):
        """Přepne roli (admin/user). Poslednímu adminovi ji vzít nedovolí."""
        role = _clean_role(role)
        email = (email or "").strip().lower()
        with self._lock:
            row = self.db.execute("SELECT id, role FROM users WHERE email = ?",
                                  (email,)).fetchone()
            if not row:
                raise ValueError(f"{email} tu žádný účet nemá.")
            if (row["role"] == "admin" and role != "admin"
                    and self._admin_count() <= 1):
                raise ValueError("Tohle je poslední admin — nejdřív udělej"
                                 " adminem někoho jiného.")
            self.db.execute("UPDATE users SET role = ? WHERE email = ?",
                            (role, email))

    def set_vault(self, email, vault):
        with self._lock:
            cur = self.db.execute("UPDATE users SET vault = ? WHERE email = ?",
                                  ((vault or "").strip(),
                                   (email or "").strip().lower()))
            if not cur.rowcount:
                raise ValueError(f"{email} tu žádný účet nemá.")

    def set_disabled(self, email, disabled=True):
        email = (email or "").strip().lower()
        with self._lock:
            cur = self.db.execute(
                "UPDATE users SET disabled = ? WHERE email = ?",
                (1 if disabled else 0, email))
            if not cur.rowcount:
                raise ValueError(f"{email} tu žádný účet nemá.")
            if disabled:
                self.db.execute(
                    "DELETE FROM tokens WHERE user_id ="
                    " (SELECT id FROM users WHERE email = ?)", (email,))

    def remove(self, email):
        with self._lock:
            cur = self.db.execute("DELETE FROM users WHERE email = ?",
                                  ((email or "").strip().lower(),))
            if not cur.rowcount:
                raise ValueError(f"{email} tu žádný účet nemá.")

    def get(self, email):
        """Účet podle e-mailu, nebo None (bez hesla)."""
        with self._lock:
            row = self.db.execute(
                "SELECT id, email, name, role, vault, claude_auth, created,"
                " disabled FROM users WHERE email = ?",
                ((email or "").strip().lower(),)).fetchone()
        return dict(row) if row else None

    def by_id(self, user_id):
        """Účet podle id, a jen pokud není zablokovaný — vydává se na něj
        přihlášení, takže na `disabled` se tu musí koukat stejně jako u hesla."""
        if not user_id:
            return None
        with self._lock:
            row = self.db.execute(
                "SELECT * FROM users WHERE id = ? AND disabled = 0",
                (user_id,)).fetchone()
        return self._public(row) if row else None

    def list(self):
        with self._lock:
            return [dict(r) for r in self.db.execute(
                "SELECT id, email, name, role, vault, claude_auth, created,"
                " disabled FROM users ORDER BY email")]

    def _admin_count(self):
        return self.db.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0"
        ).fetchone()[0]

    def count(self):
        with self._lock:
            return self.db.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    # ── přihlášení ───────────────────────────────────────────────────────────
    def verify(self, email, password):
        """Uživatel při správném heslu, jinak None. Trvá stejně tak či tak."""
        with self._lock:
            row = self.db.execute(
                "SELECT * FROM users WHERE email = ?",
                ((email or "").strip().lower(),)).fetchone()
        # I na neexistující e-mail se počítá scrypt, aby se z délky odpovědi
        # nedalo poznat, které účty existují. (Mimo zámek — scrypt je pomalý.)
        salt = row["salt"] if row else b"\0" * 16
        want = row["hash"] if row else b"\0" * SCRYPT["dklen"]
        ok = hmac.compare_digest(_hash(password or "", salt), want)
        if not row or not ok or row["disabled"]:
            return None
        return self._public(row)

    @staticmethod
    def _public(row):
        """Co o uživateli smí ven — nikdy salt ani hash."""
        return {"id": row["id"], "email": row["email"], "name": row["name"],
                "role": row["role"], "vault": row["vault"],
                "claude_auth": row["claude_auth"]}

    def issue_token(self, user, label=""):
        """Vydá token pro zařízení už prokázanému uživateli.

        Oddělené od `login()` schválně: heslo není jediný způsob, jak se dá
        prokázat totožnost. Předání přihlášení z hubu na počítači i pozdější
        přihlášení přes cizího poskytovatele potřebují tenhle krok bez hesla.
        """
        token = secrets.token_urlsafe(TOKEN_BYTES)
        now = time.time()
        with self._lock:
            self.db.execute(
                "INSERT INTO tokens (fingerprint, user_id, label, created, seen)"
                " VALUES (?, ?, ?, ?, ?)",
                (_fingerprint(token), user["id"], label or "", now, now))
        return token

    def login(self, email, password, label=""):
        """Ověří heslo a vydá token pro zařízení. None = nepustit dál."""
        user = self.verify(email, password)
        if not user:
            return None
        return self.issue_token(user, label)

    def user_for_token(self, token):
        """Komu token patří, nebo None. Zaznamená, že se ozval."""
        if not token:
            return None
        fp = _fingerprint(token)
        with self._lock:
            row = self.db.execute(
                "SELECT u.* FROM tokens t"
                " JOIN users u ON u.id = t.user_id WHERE t.fingerprint = ?",
                (fp,)).fetchone()
            if not row or row["disabled"]:
                return None
            self.db.execute("UPDATE tokens SET seen = ? WHERE fingerprint = ?",
                            (time.time(), fp))
            return self._public(row)

    def revoke(self, token):
        with self._lock:
            self.db.execute("DELETE FROM tokens WHERE fingerprint = ?",
                            (_fingerprint(token),))

    def devices(self, email):
        with self._lock:
            return [dict(r) for r in self.db.execute(
                "SELECT t.label, t.created, t.seen FROM tokens t JOIN users u"
                " ON u.id = t.user_id WHERE u.email = ? ORDER BY t.seen DESC",
                ((email or "").strip().lower(),))]
