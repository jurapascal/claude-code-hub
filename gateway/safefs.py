"""
Čtení a zápis v domově uživatele tak, aby se nedal podvrhnout odkazem.

Brána běží bez sandboxu jako uživatel `hub` a do domovů zapisuje (nastavení
hubu, pokyny pro Clauda, přejmenování domova). Domov ale patří session: kdo
v prostoru pracuje, může v něm místo souboru nechat symbolický odkaz kamkoli na
server — třeba `~/.claude/settings.json.hub-tmp` → `/home/hub/users/.domovy.json`.
Obyčejné `open(cesta, "w")` by odkaz poslušně následovalo a brána by přepsala
registr domovů obsahem, který si session připravila, a dala jí cizí domov.

Proto se tu nic neotevírá celou cestou. Každá složka se otevře zvlášť
s `O_NOFOLLOW | O_DIRECTORY` od domova dolů a soubory se čtou i zapisují relativně
k ní (`dir_fd`). Odkaz na kterémkoli místě cesty se nenásleduje:

* **čtení** ho bere jako chybějící soubor — obsah cizího souboru se do domova
  nezkopíruje;
* **zápis** jde přes dočasný soubor s náhodným jménem (`O_EXCL`) a `rename`,
  který nahradí položku ve složce, ne to, kam odkaz míří;
* **složka**, do které se má zapisovat a je odkazem, se odloží stranou
  (`.claude.odkaz-<čas>`) a založí se skutečná. Jinak by session, která si ji
  rozbila, nešla spustit.

Jen Linux/macOS — brána na Windows neběží.
"""
import os
import secrets
import stat
import time

_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | _CLOEXEC
MAX_READ = 8 * 1024 * 1024


def _parts(rel):
    parts = [p for p in str(rel).replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise ValueError(f"Neplatná cesta v domově: {rel!r}")
    return parts


def open_dir(root, parts=(), create=False, heal=False, mode=0o755):
    """fd složky `root/parts…`. Žádná část cesty nesmí být odkaz.

    `create` = chybějící složky založit. `heal` = složku, která je odkazem nebo
    souborem, odložit stranou a založit skutečnou (jen s `create`). Bez `heal`
    skončí odkaz výjimkou OSError. Volající fd zavírá sám.
    """
    fd = os.open(root, _DIR)
    try:
        for part in parts:
            try:
                nfd = os.open(part, _DIR, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, mode, dir_fd=fd)
                except FileExistsError:
                    pass
                nfd = os.open(part, _DIR, dir_fd=fd)
            except OSError:
                # ELOOP (odkaz) nebo ENOTDIR (soubor) — skutečná složka to není.
                if not (create and heal):
                    raise
                aside = f"{part}.odkaz-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
                os.rename(part, aside, src_dir_fd=fd, dst_dir_fd=fd)
                os.mkdir(part, mode, dir_fd=fd)
                nfd = os.open(part, _DIR, dir_fd=fd)
            os.close(fd)
            fd = nfd
        return fd
    except BaseException:
        os.close(fd)
        raise


def makedirs(root, rel):
    """Založí složky `rel` pod `root`. Existující položky (i odkaz) nechá být —
    dovnitř se nic nepíše, takže kam míří, je jedno."""
    fd = os.open(root, _DIR)
    try:
        for part in _parts(rel):
            try:
                os.mkdir(part, 0o755, dir_fd=fd)
            except FileExistsError:
                pass
            try:
                nfd = os.open(part, _DIR, dir_fd=fd)
            except OSError:
                return False                 # odkaz nebo soubor — dál se nejde
            os.close(fd)
            fd = nfd
        return True
    finally:
        os.close(fd)


def read_bytes(root, rel, limit=MAX_READ):
    """Obsah obyčejného souboru, nebo None: chybí, je to odkaz, složka, roura…"""
    parts = _parts(rel)
    try:
        dfd = open_dir(root, parts[:-1])
    except OSError:
        return None
    try:
        try:
            # O_NONBLOCK: pojmenovaná roura místo souboru by jinak open zasekla.
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | _CLOEXEC,
                         dir_fd=dfd)
        except OSError:
            return None
        with os.fdopen(fd, "rb") as fh:
            if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                return None
            data = fh.read(limit + 1)
        return data if len(data) <= limit else None
    finally:
        os.close(dfd)


def read_text(root, rel, limit=MAX_READ, encoding="utf-8"):
    data = read_bytes(root, rel, limit)
    if data is None:
        return None
    try:
        return data.decode(encoding)
    except UnicodeDecodeError:
        return None


def write_bytes(root, rel, data, mode=0o644, heal=True):
    """Zapíše soubor atomicky. Chybějící složky založí, odkaz v cestě odloží
    stranou (`heal`). Soubor, který je odkazem, se nahradí — cíl odkazu zůstane."""
    parts = _parts(rel)
    dfd = open_dir(root, parts[:-1], create=True, heal=heal)
    try:
        tmp = f".{parts[-1]}.hub-{secrets.token_hex(6)}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | _CLOEXEC,
                     mode, dir_fd=dfd)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, parts[-1], src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
            raise
    finally:
        os.close(dfd)


def write_text(root, rel, text, mode=0o644, heal=True):
    write_bytes(root, rel, text.encode("utf-8"), mode, heal)


def create_text(root, rel, text, mode=0o644):
    """Založí soubor, jen když na jeho místě nic není. True = založen."""
    parts = _parts(rel)
    try:
        dfd = open_dir(root, parts[:-1])
    except OSError:
        return False
    try:
        try:
            fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                         | _CLOEXEC, mode, dir_fd=dfd)
        except OSError:
            return False
        with os.fdopen(fd, "wb") as fh:
            fh.write(text.encode("utf-8"))
        return True
    finally:
        os.close(dfd)


def lstat(root, rel):
    """os.lstat položky bez následování odkazů po cestě, nebo None."""
    parts = _parts(rel)
    try:
        dfd = open_dir(root, parts[:-1])
    except OSError:
        return None
    try:
        return os.lstat(parts[-1], dir_fd=dfd)
    except OSError:
        return None
    finally:
        os.close(dfd)


def is_file(root, rel):
    st = lstat(root, rel)
    return bool(st) and stat.S_ISREG(st.st_mode)


def remove(root, rel):
    """Smaže položku (soubor nebo odkaz, ne složku). True = smazáno."""
    parts = _parts(rel)
    try:
        dfd = open_dir(root, parts[:-1])
    except OSError:
        return False
    try:
        os.unlink(parts[-1], dir_fd=dfd)
        return True
    except OSError:
        return False
    finally:
        os.close(dfd)
