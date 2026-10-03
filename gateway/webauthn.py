"""
Passkey (WebAuthn) jako druhý krok přihlášení — jen standardní knihovna.

Brána nemá běhové závislosti, takže tu je všechno, co ověření potřebuje:
malý dekodér CBOR, klíče COSE a ověření podpisu pro tři algoritmy, které
autentizátory používají:

- ES256 (ECDSA P-256 + SHA-256) — Apple, Google, Windows Hello, YubiKey
- RS256 (RSA PKCS#1 v1.5 + SHA-256) — starší Windows Hello
- EdDSA (Ed25519) — některé bezpečnostní klíče

Jen OVĚŘUJE podpisy veřejným klíčem (žádné tajemství tu není), takže
konstantní čas tu nehraje roli. Atestace se nevyžaduje („none") — brána
chce vědět, že je to pořád ten samý klíč, ne kdo ho vyrobil.
"""
import base64
import hashlib
import json
import struct

ALG_ES256, ALG_EDDSA, ALG_RS256 = -7, -8, -257
ALGS = (ALG_ES256, ALG_EDDSA, ALG_RS256)


class Chyba(ValueError):
    """Odpověď autentizátoru neprošla — text je pro log, člověku stačí „nesedí"."""


# ── base64url ────────────────────────────────────────────────────────────────
def b64e(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64d(text):
    text = str(text or "")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ── CBOR (RFC 8949), jen co WebAuthn potřebuje ───────────────────────────────
def cbor_decode(data, pos=0):
    """(hodnota, pozice za ní)."""
    if pos >= len(data):
        raise Chyba("CBOR: konec dat")
    first = data[pos]
    major, info = first >> 5, first & 0x1F
    pos += 1
    if info < 24:
        val = info
    elif info in (24, 25, 26, 27):
        n = 1 << (info - 24)
        if pos + n > len(data):
            raise Chyba("CBOR: konec dat")
        val = int.from_bytes(data[pos:pos + n], "big")
        pos += n
    else:
        raise Chyba("CBOR: neurčitá délka nepodporovaná")
    if major == 0:
        return val, pos
    if major == 1:
        return -1 - val, pos
    if major in (2, 3):
        if pos + val > len(data):
            raise Chyba("CBOR: konec dat")
        chunk = data[pos:pos + val]
        return (bytes(chunk) if major == 2 else chunk.decode("utf-8")), pos + val
    if major == 4:
        out = []
        for _ in range(val):
            item, pos = cbor_decode(data, pos)
            out.append(item)
        return out, pos
    if major == 5:
        out = {}
        for _ in range(val):
            k, pos = cbor_decode(data, pos)
            v, pos = cbor_decode(data, pos)
            out[k] = v
        return out, pos
    if major == 6:
        return cbor_decode(data, pos)           # tag — bere se obsah
    if major == 7:
        return {20: False, 21: True, 22: None}.get(info, None), pos
    raise Chyba("CBOR: neznámý typ")


# ── P-256 ────────────────────────────────────────────────────────────────────
_P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
_A = _P - 3
_B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
_G = (0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
      0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5)


def _ec_add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    (x1, y1), (x2, y2) = p1, p2
    if x1 == x2:
        if (y1 + y2) % _P == 0:
            return None
        lam = (3 * x1 * x1 + _A) * pow(2 * y1, -1, _P) % _P
    else:
        lam = (y2 - y1) * pow(x2 - x1, -1, _P) % _P
    x3 = (lam * lam - x1 - x2) % _P
    return x3, (lam * (x1 - x3) - y1) % _P


def _ec_mul(k, point):
    out = None
    while k:
        if k & 1:
            out = _ec_add(out, point)
        point = _ec_add(point, point)
        k >>= 1
    return out


def _na_krivce(x, y):
    return 0 <= x < _P and 0 <= y < _P and (y * y - (x * x * x + _A * x + _B)) % _P == 0


def _der_rs(sig):
    """ECDSA podpis v DER: SEQUENCE { INTEGER r, INTEGER s }."""
    def delka(pos):
        n = sig[pos]
        if n < 0x80:
            return n, pos + 1
        k = n & 0x7F
        return int.from_bytes(sig[pos + 1:pos + 1 + k], "big"), pos + 1 + k
    if len(sig) < 8 or sig[0] != 0x30:
        raise Chyba("ES256: podpis není DER")
    _, pos = delka(1)
    vals = []
    for _ in range(2):
        if sig[pos] != 0x02:
            raise Chyba("ES256: podpis není DER")
        n, pos = delka(pos + 1)
        vals.append(int.from_bytes(sig[pos:pos + n], "big"))
        pos += n
    return vals


def verify_es256(x, y, sig, msg):
    if not _na_krivce(x, y):
        raise Chyba("ES256: klíč není na křivce")
    r, s = _der_rs(sig)
    if not (1 <= r < _N and 1 <= s < _N):
        return False
    e = int.from_bytes(hashlib.sha256(msg).digest(), "big")
    w = pow(s, -1, _N)
    bod = _ec_add(_ec_mul(e * w % _N, _G), _ec_mul(r * w % _N, (x, y)))
    return bod is not None and bod[0] % _N == r


# ── RSA PKCS#1 v1.5 + SHA-256 ────────────────────────────────────────────────
_SHA256_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def verify_rs256(n, e, sig, msg):
    k = (n.bit_length() + 7) // 8
    if len(sig) != k or n.bit_length() < 2048:
        return False
    em = pow(int.from_bytes(sig, "big"), e, n).to_bytes(k, "big")
    tail = _SHA256_INFO + hashlib.sha256(msg).digest()
    want = b"\x00\x01" + b"\xff" * (k - len(tail) - 3) + b"\x00" + tail
    return em == want


# ── Ed25519 (RFC 8032, jen ověření) ──────────────────────────────────────────
_EP = 2 ** 255 - 19
_EL = 2 ** 252 + 27742317777372353535851937790883648493
_ED = -121665 * pow(121666, -1, _EP) % _EP
_EI = pow(2, (_EP - 1) // 4, _EP)


def _ed_add(p1, p2):
    x1, y1, z1, t1 = p1
    x2, y2, z2, t2 = p2
    a = (y1 - x1) * (y2 - x2) % _EP
    b = (y1 + x1) * (y2 + x2) % _EP
    c = 2 * t1 * t2 * _ED % _EP
    d = 2 * z1 * z2 % _EP
    e, f, g, h = b - a, d - c, d + c, b + a
    return e * f % _EP, g * h % _EP, f * g % _EP, e * h % _EP


def _ed_mul(k, p):
    out = (0, 1, 1, 0)
    while k:
        if k & 1:
            out = _ed_add(out, p)
        p = _ed_add(p, p)
        k >>= 1
    return out


def _ed_dekomprimuj(data):
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    if y >= _EP:
        return None
    x2 = (y * y - 1) * pow(_ED * y * y + 1, -1, _EP) % _EP
    x = pow(x2, (_EP + 3) // 8, _EP)
    if (x * x - x2) % _EP:
        x = x * _EI % _EP
    if (x * x - x2) % _EP:
        return None
    if x == 0 and sign:
        return None
    if (x & 1) != sign:
        x = _EP - x
    return x, y, 1, x * y % _EP


def _ed_rovno(p1, p2):
    x1, y1, z1, _ = p1
    x2, y2, z2, _ = p2
    return (x1 * z2 - x2 * z1) % _EP == 0 and (y1 * z2 - y2 * z1) % _EP == 0


_EG = _ed_dekomprimuj(bytes.fromhex("5866666666666666666666666666666666666666666666666666666666666666"))


def verify_ed25519(pub, sig, msg):
    a = _ed_dekomprimuj(pub)
    if a is None or len(sig) != 64:
        return False
    r = _ed_dekomprimuj(sig[:32])
    s = int.from_bytes(sig[32:], "little")
    if r is None or s >= _EL:
        return False
    h = int.from_bytes(hashlib.sha512(sig[:32] + pub + msg).digest(), "little") % _EL
    return _ed_rovno(_ed_mul(s, _EG), _ed_add(r, _ed_mul(h, a)))


# ── COSE klíč a podpis ───────────────────────────────────────────────────────
def verify_cose(cose_bytes, sig, msg):
    key, _ = cbor_decode(cose_bytes)
    alg = key.get(3)
    if alg == ALG_ES256 and key.get(1) == 2 and key.get(-1) == 1:
        return verify_es256(int.from_bytes(key[-2], "big"), int.from_bytes(key[-3], "big"), sig, msg)
    if alg == ALG_RS256 and key.get(1) == 3:
        return verify_rs256(int.from_bytes(key[-1], "big"), int.from_bytes(key[-2], "big"), sig, msg)
    if alg == ALG_EDDSA and key.get(1) == 1 and key.get(-1) == 6:
        return verify_ed25519(key[-2], sig, msg)
    raise Chyba(f"nepodporovaný klíč (alg {alg})")


# ── WebAuthn ─────────────────────────────────────────────────────────────────
def _client_data(raw, typ, challenge, origin):
    try:
        cd = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise Chyba("clientDataJSON není JSON")
    if cd.get("type") != typ:
        raise Chyba(f"čekal jsem {typ}, přišlo {cd.get('type')}")
    if cd.get("challenge") != b64e(challenge):
        raise Chyba("výzva nesedí")
    if cd.get("origin") != origin:
        raise Chyba(f"jiný původ: {cd.get('origin')}")
    return cd


def _auth_data(data, rp_id):
    if len(data) < 37:
        raise Chyba("authenticatorData je krátké")
    if data[:32] != hashlib.sha256(rp_id.encode("utf-8")).digest():
        raise Chyba("rpIdHash nesedí")
    flags = data[32]
    if not flags & 0x01:
        raise Chyba("chybí přítomnost uživatele (UP)")
    count = struct.unpack(">I", data[33:37])[0]
    return flags, count


def registrace(client_data_json, attestation_object, challenge, origin, rp_id):
    """Ověří odpověď navigator.credentials.create(). Vrací
    {"id": b64url, "public_key": b64url COSE, "sign_count": int}."""
    _client_data(client_data_json, "webauthn.create", challenge, origin)
    att, _ = cbor_decode(attestation_object)
    if not isinstance(att, dict) or not isinstance(att.get("authData"), bytes):
        raise Chyba("attestationObject bez authData")
    data = att["authData"]
    flags, count = _auth_data(data, rp_id)
    if not flags & 0x40:
        raise Chyba("chybí údaje o klíči (AT)")
    if len(data) < 55:
        raise Chyba("authData je krátké")
    cid_len = struct.unpack(">H", data[53:55])[0]
    cred_id = data[55:55 + cid_len]
    if len(cred_id) != cid_len or not cid_len:
        raise Chyba("id klíče je useknuté")
    key, end = cbor_decode(data, 55 + cid_len)
    if not isinstance(key, dict) or key.get(3) not in ALGS:
        raise Chyba(f"nepodporovaný algoritmus {key.get(3) if isinstance(key, dict) else '?'}")
    return {"id": b64e(cred_id), "public_key": b64e(data[55 + cid_len:end]), "sign_count": count}


def prihlaseni(client_data_json, authenticator_data, signature, challenge, origin, rp_id,
               public_key_b64, stored_count):
    """Ověří odpověď navigator.credentials.get(). Vrací nový sign_count."""
    _client_data(client_data_json, "webauthn.get", challenge, origin)
    _, count = _auth_data(authenticator_data, rp_id)
    msg = authenticator_data + hashlib.sha256(client_data_json).digest()
    if not verify_cose(b64d(public_key_b64), signature, msg):
        raise Chyba("podpis nesedí")
    # Počítadlo, které necouvá: když jde zpět, někdo klíč zkopíroval.
    if count and stored_count and count <= stored_count:
        raise Chyba("počítadlo podpisů couvlo — klíč mohl být zkopírovaný")
    return count
