#!/usr/bin/env python3
"""
Zkrátí řádky v MEMORY.md, aby se celý rozcestník vešel do kontextu.

MEMORY.md se načítá na začátku každé session celý — ale jen do určité velikosti.
Jakmile ji překročí, načte se z něj **jen kus** a na zbylé poznámky se prostě
zapomene: nikde to nevyskočí, jen se přestanou nabízet. Nejde přitom o počet
poznámek, ale o délku řádků; jeden řádek popisu klidně narostl na 780 znaků,
i když detail stejně žije v odkazovaném souboru.

Odkaz `- [Titulek](soubor.md)` zůstává vždycky celý, zkracuje se jen popisek za
pomlčkou, a to na hranici slova.

    python3 tools/memory_index_trim.py <cesta k MEMORY.md> [--max 185] [--apply]

Bez `--apply` jen ukáže, co by se stalo.
"""
import argparse
import os
import re
import shutil
import sys

# "- [Titulek](soubor.md) — popisek"
ENTRY = re.compile(r"^(\s*[-*]\s*\[[^\]]*\]\([^)]*\)\s*)([—–-]\s*)(.*)$")


def trim(text, budget):
    """Popisek zkrácený na hranici slova, s výpustkou. Nikdy neuseknout uprostřed
    odkazu — `[[wikilink]]` nebo `[text](cíl)` v půlce je horší než nic."""
    if budget <= 1:
        return ""
    cut = text[:budget]
    # kdyby řez spadl doprostřed odkazu, couvni před jeho začátek
    for opener in ("[[", "["):
        last_open = cut.rfind(opener)
        if last_open > cut.rfind("]"):
            cut = cut[:last_open]
    space = cut.rfind(" ")
    if space > budget * 0.5:
        cut = cut[:space]
    # otevřená závorka bez protějšku vypadá jako chyba, ne jako zkrácení
    opened = cut.rfind("(")
    if opened > cut.rfind(")"):
        cut = cut[:opened]
    return cut.rstrip(" ,;:.·—–-*_`(") + "…"


def process(lines, limit):
    out, changed = [], 0
    for line in lines:
        stripped = line.rstrip("\n")
        if len(stripped) <= limit:
            out.append(line)
            continue
        m = ENTRY.match(stripped)
        if not m:
            out.append(line)          # nadpis, odstavec, cokoli jiného — nesahat
            continue
        head, dash, tail = m.groups()
        budget = limit - len(head) - len(dash)
        new = head + dash + trim(tail, budget)
        out.append(new + "\n")
        changed += 1
    return out, changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--max", type=int, default=185,
                    help="maximální délka řádku ve znacích (výchozí 185)")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if not os.path.isfile(args.path):
        print(f"není soubor: {args.path}")
        return 1
    with open(args.path, encoding="utf-8") as fh:
        lines = fh.readlines()

    before = sum(len(l.encode("utf-8")) for l in lines)
    out, changed = process(lines, args.max)
    after = sum(len(l.encode("utf-8")) for l in out)

    print(f"{args.path}")
    print(f"  před:    {before:>6} B   ({len(lines)} řádků)")
    print(f"  po:      {after:>6} B   (zkráceno {changed} řádků)")
    print(f"  úspora:  {before - after:>6} B")

    if not args.apply:
        print("\n  (bez --apply se nic nezapsalo)")
        return 0

    shutil.copy2(args.path, args.path + ".backup")
    with open(args.path, "w", encoding="utf-8") as fh:
        fh.writelines(out)
    print(f"\n  zapsáno; záloha: {os.path.basename(args.path)}.backup")
    return 0


if __name__ == "__main__":
    sys.exit(main())
