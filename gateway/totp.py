"""
Dvoufázové ověření kódem z aplikace v mobilu (TOTP, RFC 6238).

Google Authenticator, Microsoft Authenticator, Authy i správci hesel počítají
stejný šesticiferný kód ze sdíleného tajemství a z času. Brána ho spočítá taky
a porovná — HMAC-SHA1 i base32 jsou ve standardní knihovně, nic se nedoinstalovává.

* Kód platí 30 vteřin. Bere se i předchozí a následující okno, ať nevadí
  pár vteřin rozdílu hodin v telefonu.
* Stejný kód nejde použít dvakrát: účet si pamatuje poslední použité okno,
  takže kdo kód odkouká přes rameno, s ním už neprojde.
* Záložní kódy jsou pro ztracený telefon. Každý platí jednou a v databázi
  leží jen jejich otisk.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
import urllib.parse

STEP = 30
DIGITS = 6
WINDOW = 1
ISSUER = "Code Hub"
RECOVERY_COUNT = 8
# Bez znaků, které se při opisování pletou (0/o, 1/l/i).
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def new_secret():
    """Nové tajemství: 160 bitů v base32, jak ho aplikace čekají."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _key(secret):
    clean = "".join(str(secret).split()).upper()
    return base64.b32decode(clean + "=" * (-len(clean) % 8))


def code_at(secret, counter):
    """Kód pro dané časové okno (RFC 4226, dynamické zkrácení)."""
    digest = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def verify(secret, code, last=0, now=None):
    """Číslo časového okna, když kód sedí a nebyl už použitý (> last), jinak 0.
    Mezery a pomlčky v kódu nevadí — aplikace ho ukazují jako „123 456"."""
    digits = "".join(ch for ch in str(code or "") if ch.isdigit())
    if not secret or len(digits) != DIGITS or len(str(code).strip()) > DIGITS + 2:
        return 0
    current = int((time.time() if now is None else now) // STEP)
    for counter in range(current - WINDOW, current + WINDOW + 1):
        if counter > last and hmac.compare_digest(code_at(secret, counter), digits):
            return counter
    return 0


def uri(secret, account):
    """Odkaz otpauth:// — to je obsah QR kódu, který aplikace naskenuje."""
    label = urllib.parse.quote(f"{ISSUER}:{account}")
    query = urllib.parse.urlencode({"secret": secret, "issuer": ISSUER, "algorithm": "SHA1",
                                    "digits": DIGITS, "period": STEP})
    return f"otpauth://totp/{label}?{query}"


def grouped(secret):
    """Tajemství po čtveřicích — na ruční opsání do aplikace."""
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


def recovery_codes(count=RECOVERY_COUNT):
    def one():
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(10))
        return raw[:5] + "-" + raw[5:]
    return [one() for _ in range(count)]


def _clean_recovery(code):
    return "".join(ch for ch in str(code or "").lower() if ch.isalnum())


def looks_like_recovery(code):
    clean = _clean_recovery(code)
    return len(clean) == 10 and not clean.isdigit()


def recovery_hash(code):
    clean = _clean_recovery(code)
    return hashlib.sha256(clean.encode("ascii", "ignore")).hexdigest() if clean else ""
