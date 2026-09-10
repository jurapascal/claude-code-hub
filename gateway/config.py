"""
Nastavení brány na jednom místě. Všechno jde přebít proměnnou prostředí, takže
systemd unit i test si řeknou o svoje bez zásahu do kódu.
"""
import os

# Kam se brána instaluje a kde má stav (databáze účtů).
GATEWAY_DIR = os.environ.get("HUB_GW_DIR", "/home/hub/gateway")
DB_PATH = os.environ.get("HUB_GW_DB", os.path.join(GATEWAY_DIR, "accounts.db"))

# Na čem brána poslouchá. Výchozí je loopback: TLS terminuje nginx před ní,
# takže brána sama nikdy nestojí holá na síti.
HOST = os.environ.get("HUB_GW_HOST", "127.0.0.1")
PORT = int(os.environ.get("HUB_GW_PORT", "8800"))

# Čím se pouští session uživatele. Na serveru vždy `bwrap` — `none` si brána
# dovolí jen na loopbacku bez nginx, viz isolation.check().
ISOLATION = os.environ.get("HUB_GW_ISOLATION", "bwrap")

# Cookie s přihlašovacím tokenem. Token sám je v databázi jen jako otisk.
SESSION_COOKIE = "gw_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30      # měsíc; jinak by se pořád přihlašovalo

# Kolik instancí hubu smí běžet naráz a kdy uspat nečinnou. Změřeno v README
# brány: na 8GB stroj s weby a mailem se vejdou realisticky čtyři.
MAX_SESSIONS = int(os.environ.get("HUB_GW_MAX_SESSIONS", "4"))
IDLE_SLEEP = int(os.environ.get("HUB_GW_IDLE_SLEEP", str(30 * 60)))
