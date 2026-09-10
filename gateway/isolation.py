"""
Jak se pouští session uživatele, aby neviděla na cizí.

Claude Code umí číst soubory a spouštět příkazy. Na jednom stroji to je věc
jeho majitele; jakmile na bránu pustíš víc lidí, je to najednou způsob, jak si
můžou přečíst navzájem Obsidian, klíče i zbytek serveru. Izolace proto není
něco, co se doladí potom — bez ní brána nemá běžet.

Tři režimy, každý vrací hotové argv, kterým se session spustí:

  none    bez izolace. Na vývoj na vlastním stroji. Brána ho odmítne pustit,
          jakmile poslouchá jinam než na loopback — viz `check()`.
  bwrap   bubblewrap: session vidí systém jen ke čtení a ze zápisu jenom svůj
          domov. Nepotřebuje démona ani práva správce.
  docker  kontejner na session. Nejtvrdší hranice, ale chce Docker na stroji.

POZOR na jednu věc, kterou izolace nevyřeší: session se prokazuje přihlášením
Claude Code, které leží v domovské složce. Kdo v sandboxu spustí Claude Code,
dosáhne i na ten soubor. Mezi kolegy, kteří si tak jako tak věří, to je únosné;
pro cizí lidi je jediná čistá cesta API klíč, který drží brána a session ho
nikdy nevidí.
"""
import os
import shutil

MODES = ("none", "bwrap", "docker")
DOCKER_IMAGE = "claude-hub-workspace"

# Kolik si smí jedna session vzít. Naměřeno: session Claude Code drží kolem
# 400-500 MB, takže 1,5 GB je pohodlný strop i pro delší konverzaci a zároveň
# se jich na 8GB stroj vejde několik, aniž by shodily weby nebo mail.
LIMITS = {"memory": "1500M", "tasks": 256, "cpu_weight": 100}


def available():
    """Které režimy jde na tomhle stroji doopravdy použít."""
    out = ["none"]
    if shutil.which("bwrap"):
        out.append("bwrap")
    if shutil.which("docker"):
        out.append("docker")
    return out


def check(mode, host):
    """Vrátí důvod, proč tenhle režim nepustit, nebo ''.

    Pravidlo je jediné a tvrdé: bez izolace jen na loopback. Brána bez izolace
    vystavená do sítě je vzdálený shell pro kohokoli, kdo uhodne heslo.
    """
    if mode not in MODES:
        return f"Neznámý režim izolace {mode!r}, znám {', '.join(MODES)}."
    if mode not in available():
        return f"Režim {mode} tu není nainstalovaný."
    if mode == "none" and host not in ("127.0.0.1", "localhost", "::1"):
        return ("Bez izolace smí brána poslouchat jen na 127.0.0.1. "
                "Na síť nastav isolation na bwrap nebo docker.")
    return ""


def wrap(mode, argv, home, extra_ro=(), extra_rw=(), limits=None):
    """argv, kterým se session doopravdy spustí.

    `home` je domov uživatele na bráně — jediné místo, kam smí zapisovat.
    `extra_ro` jsou cesty, které má vidět jen ke čtení (zdroj hubu, sdílené
    přihlášení, když nemá přežít úpravy session).
    `extra_rw` jsou cesty vně domova, do kterých session **smí** zapsat — jediný
    případ je sdílené přihlášení Claude Code, které si samo obnovuje token, a
    obnovený token má vidět i zbytek session (jinak by po expiraci vypadly
    všechny naráz). Drž `extra_rw` co nejmenší; každá cesta v něm je díra
    v izolaci.
    `limits` je strop na paměť, procesy a podíl na procesoru; None = LIMITS.
    """
    limits = LIMITS if limits is None else limits
    if mode == "none":
        return _limited(list(argv), limits)
    if mode == "bwrap":
        return _limited(_bwrap(argv, home, extra_ro, extra_rw), limits)
    if mode == "docker":
        # Docker si limity řeší sám, přes systemd by se počítaly dvakrát.
        return _docker(argv, home, extra_ro, limits, extra_rw)
    raise ValueError(f"Neznámý režim izolace: {mode}")


def _limited(argv, limits):
    """Obalí příkaz cgroupou, aby jedna session neshodila zbytek serveru.

    bwrap odděluje, co session *vidí*, ale ne kolik si vezme — paměť ani
    procesy neomezuje vůbec. Na stroji, kde vedle běží weby a mail, je to
    zásadní: bez stropu stačí jedna ukecaná session a OOM killer sáhne po tom,
    co má nejvíc paměti, tedy nejspíš po databázi.

    MemorySwapMax=0 tam není navíc. Se samotným MemoryMax proces neumře, jen
    přeteče do swapu a celý stroj se začne plazit — naměřeno: bez něj 300 MB
    při stropu 200 MB v klidu projde, s ním skončí kódem 137.
    """
    if not limits or not shutil.which("systemd-run"):
        return argv
    scope = ["systemd-run", "--user", "--scope", "--quiet", "--collect"]
    if limits.get("memory"):
        scope += ["-p", f"MemoryMax={limits['memory']}", "-p", "MemorySwapMax=0"]
    if limits.get("tasks"):
        scope += ["-p", f"TasksMax={limits['tasks']}"]
    if limits.get("cpu_weight"):
        scope += ["-p", f"CPUWeight={limits['cpu_weight']}"]
    return scope + argv


def _bwrap(argv, home, extra_ro, extra_rw=()):
    cmd = ["bwrap",
           # Systém ke čtení. Zápis nikam mimo domov: i kdyby session někoho
           # napadlo sáhnout na /usr, nemá kam.
           "--ro-bind", "/usr", "/usr",
           "--ro-bind", "/etc", "/etc",
           "--proc", "/proc",
           "--dev", "/dev",
           "--tmpfs", "/tmp",
           "--tmpfs", "/run",
           # Domov uživatele je jediné zapisovatelné místo a je na stejné
           # cestě jako venku, aby si Claude Code nestěžoval na jinou realitu.
           "--bind", home, home,
           "--setenv", "HOME", home,
           "--chdir", home,
           # Síť potřebujeme (API Anthropicu), všechno ostatní se odděluje.
           "--unshare-pid", "--unshare-ipc", "--unshare-uts",
           # POZN.: schválně BEZ `--die-with-parent`. Když bránu (systemd user
           # službu) pouští `systemd-run --user --scope`, systemd-run v tom
           # kontextu po založení scope skončí — a die-with-parent by pak
           # sandbox okamžitě zabil (session by umřela pár vteřin po startu).
           # Úklid proto řeší brána sama (HubProc.stop pkillem podle domova
           # + úklid osiřelých při startu).
           "--new-session"]
    # /bin a /lib jsou na dnešních distribucích symlinky do /usr. Přeskočit je
    # nejde: v sandboxu by pak nebyl ani shell („execvp /bin/sh: No such file“).
    # Symlink se proto uvnitř vyrobí znovu, ne sváže.
    for path in ("/bin", "/sbin", "/lib", "/lib32", "/lib64"):
        if os.path.islink(path):
            cmd += ["--symlink", os.readlink(path), path]
        elif os.path.isdir(path):
            cmd += ["--ro-bind", path, path]
    # DNS. /etc/resolv.conf je na systemd distribucích symlink do /run, jenže
    # /run jsme právě přebili tmpfs — symlink by uvnitř visel do prázdna a
    # session by se nedostala na API. Vázat se proto musí složka, na kterou
    # symlink míří, ne on sám: bind na visící symlink bwrap odmítne
    # („Can't create file at /etc/resolv.conf“).
    resolv = os.path.realpath("/etc/resolv.conf")
    if resolv != "/etc/resolv.conf" and os.path.isfile(resolv):
        cmd += ["--ro-bind", os.path.dirname(resolv), os.path.dirname(resolv)]
    for path in extra_ro:
        if os.path.exists(path):
            cmd += ["--ro-bind", path, path]
    for path in extra_rw:
        if os.path.exists(path):
            cmd += ["--bind", path, path]
    return cmd + ["--"] + list(argv)


def _docker(argv, home, extra_ro, limits=None, extra_rw=()):
    limits = LIMITS if limits is None else limits
    cmd = ["docker", "run", "--rm", "-i",
           "--network", "bridge",
           # Bez práv navíc a bez možnosti si je vzít.
           "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
           # Aby jeden uživatel nemohl serveru sníst paměť ani procesor.
           "--memory", str(limits.get("memory", "1500M")),
           "--memory-swap", str(limits.get("memory", "1500M")),
           "--pids-limit", str(limits.get("tasks", 256)),
           "-v", f"{home}:{home}:rw",
           "-e", f"HOME={home}",
           "-w", home]
    for path in extra_ro:
        if os.path.exists(path):
            cmd += ["-v", f"{path}:{path}:ro"]
    for path in extra_rw:
        if os.path.exists(path):
            cmd += ["-v", f"{path}:{path}:rw"]
    return cmd + [DOCKER_IMAGE] + list(argv)
