"""
Odhad ceny podle ceníku API (USD za milion tokenů).

Claude Code na předplatném (Max, Pro) se po tokenech neplatí — číslo je to,
co by tytéž tokeny stály přes API. Hodí se na srovnání projektů a modelů,
ne jako faktura.

Ceny: vstup / výstup za milion tokenů. Čtení z cache bývá 10 % vstupu
(u některých modelů míň, tam je uvedené zvlášť), zápis do cache 125 %.
Ceník se mění — když model v tabulce není, vezme se podle rodiny (opus /
sonnet / haiku / fable) a počítá se jako nejbližší známá verze.
"""
import re

# (regulární výraz na id modelu, vstup, výstup, čtení z cache nebo None = 10 % vstupu)
PRICES = [
    (r"fable", 10.0, 50.0, 0.25),
    (r"mythos", 10.0, 50.0, 0.25),
    (r"opus-5-5", 4.0, 20.0, 0.20),
    (r"opus-5\b", 5.0, 25.0, None),
    (r"opus-4-[5-9]", 5.0, 25.0, None),
    (r"opus-4(-[01])?($|-2)", 15.0, 75.0, None),     # Opus 4 a 4.1
    (r"opus", 5.0, 25.0, None),
    (r"sonnet-5-5", 2.0, 10.0, 0.20),
    (r"sonnet-5\b", 2.0, 10.0, None),
    (r"sonnet", 3.0, 15.0, None),                    # Sonnet 4.x a starší
    (r"haiku-4", 1.0, 5.0, None),
    (r"haiku", 0.8, 4.0, None),
    (r"synthetic|<", 0.0, 0.0, 0.0),
]
CACHE_WRITE = 1.25


def rates(model):
    name = str(model or "").lower()
    for pat, vstup, vystup, cteni in PRICES:
        if re.search(pat, name):
            return vstup, vystup, (vstup * 0.1 if cteni is None else cteni)
    return None


def cost(model, usage):
    """Odhad v USD; `usage` má klíče in, out, cache_w, cache_r (tokeny)."""
    r = rates(model)
    if not r:
        return 0.0
    vstup, vystup, cteni = r
    return (usage.get("in", 0) * vstup + usage.get("out", 0) * vystup
            + usage.get("cache_w", 0) * vstup * CACHE_WRITE
            + usage.get("cache_r", 0) * cteni) / 1e6


def known(model):
    return rates(model) is not None
