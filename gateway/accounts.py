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

**Neúspěšná přihlášení** se zapisují sem (`login_fails`), ne do paměti brány:
zámek po hádání hesla tak přežije restart brány (noční aktualizace) a správce
ho zruší z CLI (`claude-hub-admin zamky odemknout`).
"""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time

from . import totp

# N=2^15, r=8 → scrypt si řekne o 128*N*r = přesně 32 MiB, což je zároveň
# výchozí strop OpenSSL. Bez `maxmem` to spadne na „memory limit exceeded“,
# tak se strop říká explicitně a s rezervou.
SCRYPT = dict(n=2 ** 15, r=8, p=1, dklen=32, maxmem=128 * 1024 * 1024)
TOKEN_BYTES = 32
ROLES = ("user", "admin")
# Čím se v daném prostoru ověřuje Claude: `central` = klíč API brány (platí se
# podle spotřeby), `own` = uživatel má v svém domově vlastní přihlášení.
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
CREATE TABLE IF NOT EXISTS login_fails (
    key     TEXT PRIMARY KEY,           -- ip:…|email:…, ip:…, email:…
    hits    TEXT NOT NULL DEFAULT '[]', -- časy neúspěchů, které se ještě počítají
    locked  REAL NOT NULL DEFAULT 0,    -- do kdy je zamčeno
    updated REAL NOT NULL
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


def _times(raw):
    """Časy neúspěchů uložené jako JSON seznam čísel."""
    try:
        data = json.loads(raw or "[]")
    except ValueError:
        return []
    return [t for t in data if isinstance(t, (int, float))] if isinstance(data, list) else []


def _codes(raw):
    """Otisky záložních kódů uložené jako JSON seznam."""
    try:
        data = json.loads(raw or "[]")
    except ValueError:
        return []
    return [c for c in data if isinstance(c, str)] if isinstance(data, list) else []


class Accounts:
    """Účty a přihlašovací tokeny. Bezpečné volat z více vláken (RLock)."""

    def __init__(self, path, require_mfa=False):
        # Brána s povinným dvoufázovým ověřením: platí jen tokeny vydané po
        # druhém kroku (tokens.mfa). Správa z CLI tokeny nepoužívá.
        self.require_mfa = require_mfa
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
        # Dvoufázové ověření (2.7.0): tajemství aplikace, poslední použité
        # okno (proti opakovanému použití kódu) a otisky záložních kódů.
        if "totp_secret" not in have:
            self.db.execute("ALTER TABLE users ADD COLUMN totp_secret TEXT NOT"
                            " NULL DEFAULT ''")
        if "totp_last" not in have:
            self.db.execute("ALTER TABLE users ADD COLUMN totp_last INTEGER NOT"
                            " NULL DEFAULT 0")
        if "recovery" not in have:
            self.db.execute("ALTER TABLE users ADD COLUMN recovery TEXT NOT"
                            " NULL DEFAULT ''")
        tokens = {r["name"] for r in self.db.execute("PRAGMA table_info(tokens)")}
        if "mfa" not in tokens:
            # Tokeny z doby před 2FA mají 0 — s povinným ověřením neplatí.
            self.db.execute("ALTER TABLE tokens ADD COLUMN mfa INTEGER NOT"
                            " NULL DEFAULT 0")

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
                " disabled, totp_secret != '' AS twofa FROM users ORDER BY email")]

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

    # ── neúspěšná přihlášení ─────────────────────────────────────────────────
    # `keys` = [(klíč, limit)] — pravidla skládá brána (server._fail_keys).
    def fail_wait(self, keys):
        """Kolik sekund ještě drží zámek na některém z klíčů; 0 = volno."""
        names = [key for key, _limit in keys]
        if not names:
            return 0
        now = time.time()
        with self._lock:
            rows = self.db.execute(
                "SELECT locked FROM login_fails WHERE key IN (%s)" % ",".join("?" * len(names)),
                names).fetchall()
        return max([row["locked"] - now for row in rows] + [0])

    def fail_record(self, keys, window, lock):
        """Zapíše neúspěch. Kde se tím dosáhne limitu za `window` sekund, zamkne
        na `lock` sekund. Vrací (sekund do odemčení, kolik pokusů zbývá,
        klíče zamčené právě teď)."""
        now = time.time()
        wait, left, locked_now = 0, None, []
        with self._lock:
            for key, limit in keys:
                row = self.db.execute("SELECT hits, locked FROM login_fails WHERE key = ?",
                                      (key,)).fetchone()
                hits = [t for t in _times(row["hits"] if row else "") if now - t < window]
                locked = row["locked"] if row and row["locked"] > now else 0
                hits.append(now)
                if not locked and len(hits) >= limit:
                    # Po odemčení se začíná znovu od nuly, ne od starých pokusů.
                    locked, hits = now + lock, []
                    locked_now.append(key)
                self.db.execute(
                    "INSERT OR REPLACE INTO login_fails (key, hits, locked, updated)"
                    " VALUES (?, ?, ?, ?)", (key, json.dumps(hits), locked, now))
                wait = max(wait, locked - now) if locked else wait
                remaining = 0 if locked else max(0, limit - len(hits))
                left = remaining if left is None else min(left, remaining)
            self.db.execute("DELETE FROM login_fails WHERE locked < ? AND updated < ?",
                            (now, now - window))
        return wait, (left or 0), locked_now

    def fail_clear(self, key):
        """Povedené přihlášení: pokusy na tenhle klíč se zapomenou."""
        with self._lock:
            self.db.execute("DELETE FROM login_fails WHERE key = ?", (key,))

    def fail_list(self):
        """Zámky a počítané neúspěchy pro správu: [{"key", "hits", "locked"}]."""
        now = time.time()
        with self._lock:
            rows = self.db.execute("SELECT key, hits, locked FROM login_fails"
                                   " ORDER BY locked DESC, updated DESC").fetchall()
        return [{"key": r["key"], "hits": len(_times(r["hits"])),
                 "locked": r["locked"] if r["locked"] > now else 0} for r in rows]

    def fail_unlock(self, target):
        """Zruší zámky a pokusy pro e-mail nebo adresu. Vrací, kolik záznamů smazal."""
        target = (target or "").strip().lower()
        if not target:
            raise ValueError("Zadej e-mail nebo IP adresu.")
        mark = ("email:" if "@" in target else "ip:") + target
        with self._lock:
            keys = [r["key"] for r in self.db.execute("SELECT key FROM login_fails")
                    if mark in r["key"].split("|")]
            for key in keys:
                self.db.execute("DELETE FROM login_fails WHERE key = ?", (key,))
        return len(keys)

    @staticmethod
    def _public(row):
        """Co o uživateli smí ven — nikdy salt ani hash."""
        return {"id": row["id"], "email": row["email"], "name": row["name"],
                "role": row["role"], "vault": row["vault"],
                "claude_auth": row["claude_auth"]}

    def issue_token(self, user, label="", mfa=False):
        """Vydá token pro zařízení už prokázanému uživateli.

        Oddělené od `login()` schválně: heslo není jediný způsob, jak se dá
        prokázat totožnost. Předání přihlášení z hubu na počítači i pozdější
        přihlášení přes cizího poskytovatele potřebují tenhle krok bez hesla.
        `mfa` = uživatel prošel i druhým krokem (kód z aplikace).
        """
        token = secrets.token_urlsafe(TOKEN_BYTES)
        now = time.time()
        with self._lock:
            self.db.execute(
                "INSERT INTO tokens (fingerprint, user_id, label, created, seen, mfa)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (_fingerprint(token), user["id"], label or "", now, now,
                 1 if mfa else 0))
        return token

    def login(self, email, password, label=""):
        """Ověří heslo a vydá token pro zařízení. None = nepustit dál.
        Jen heslem — s povinným dvoufázovým ověřením takový token neplatí."""
        user = self.verify(email, password)
        if not user:
            return None
        return self.issue_token(user, label)

    # ── dvoufázové ověření ───────────────────────────────────────────────────
    def twofa(self, user_id):
        """{"enabled", "recovery_left"} — pro přihlášení, nastavení i správu."""
        with self._lock:
            row = self.db.execute("SELECT totp_secret, recovery FROM users WHERE id = ?",
                                  (user_id,)).fetchone()
        if not row:
            return {"enabled": False, "recovery_left": 0}
        return {"enabled": bool(row["totp_secret"]),
                "recovery_left": len(_codes(row["recovery"]))}

    def _store_recovery(self, user_id):
        codes = totp.recovery_codes()
        self.db.execute("UPDATE users SET recovery = ? WHERE id = ?",
                        (json.dumps([totp.recovery_hash(c) for c in codes]), user_id))
        return codes

    def enable_totp(self, user_id, secret):
        """Zapne ověřování s tajemstvím, které už člověk potvrdil kódem.
        Vrací záložní kódy — jediné místo, kde jsou k vidění v čitelné podobě."""
        with self._lock:
            self.db.execute("UPDATE users SET totp_secret = ?, totp_last = 0 WHERE id = ?",
                            (secret, user_id))
            return self._store_recovery(user_id)

    def new_recovery(self, user_id):
        """Nové záložní kódy; staré tím přestanou platit."""
        with self._lock:
            return self._store_recovery(user_id)

    def second_factor(self, user_id, code, allow_recovery=True):
        """Ověří kód z aplikace, nebo záložní kód. Vrací "totp", "recovery", nebo "".
        Použitý kód z aplikace ani záložní kód podruhé neprojde."""
        with self._lock:
            row = self.db.execute(
                "SELECT totp_secret, totp_last, recovery, disabled FROM users WHERE id = ?",
                (user_id,)).fetchone()
            if not row or row["disabled"] or not row["totp_secret"]:
                return ""
            counter = totp.verify(row["totp_secret"], code, row["totp_last"])
            if counter:
                self.db.execute("UPDATE users SET totp_last = ? WHERE id = ?",
                                (counter, user_id))
                return "totp"
            if allow_recovery and totp.looks_like_recovery(code):
                codes = _codes(row["recovery"])
                digest = totp.recovery_hash(code)
                if digest in codes:
                    codes.remove(digest)
                    self.db.execute("UPDATE users SET recovery = ? WHERE id = ?",
                                    (json.dumps(codes), user_id))
                    return "recovery"
        return ""

    def reset_totp(self, email):
        """Zruší ověřování (ztracený telefon i záložní kódy) a odhlásí všechna
        zařízení. Při příštím přihlášení si ho člověk nastaví znovu."""
        email = (email or "").strip().lower()
        with self._lock:
            cur = self.db.execute(
                "UPDATE users SET totp_secret = '', totp_last = 0, recovery = ''"
                " WHERE email = ?", (email,))
            if not cur.rowcount:
                raise ValueError(f"{email} tu žádný účet nemá.")
            self.db.execute(
                "DELETE FROM tokens WHERE user_id ="
                " (SELECT id FROM users WHERE email = ?)", (email,))

    def user_for_token(self, token):
        """Komu token patří, nebo None. Zaznamená, že se ozval."""
        if not token:
            return None
        fp = _fingerprint(token)
        with self._lock:
            row = self.db.execute(
                "SELECT u.*, t.mfa AS token_mfa FROM tokens t"
                " JOIN users u ON u.id = t.user_id WHERE t.fingerprint = ?",
                (fp,)).fetchone()
            if not row or row["disabled"]:
                return None
            # Token vydaný jen na heslo (třeba z doby před 2FA) s povinným
            # ověřením neplatí — člověk se přihlásí znovu i s kódem.
            if self.require_mfa and not row["token_mfa"]:
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
