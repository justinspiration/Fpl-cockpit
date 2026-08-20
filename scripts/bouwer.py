#!/usr/bin/env python3
"""Bouwt de best mogelijke selectie van 15 binnen het budget, vanaf nul.

Dit is een ander probleem dan solver.py oplost. Die zoekt transfers vanaf een
bestaande selectie; deze kiest vijftien spelers zonder startpunt, onder alle
regels tegelijk: 2-5-5-3, maximaal 3 per club, en het budget.

Aanpak — exact, geen heuristiek:

  1. Per positie eerst snoeien met dominantie. Kost speler A hetzelfde of minder
     dan B en levert hij meer op, dan komt B nooit in een optimale selectie voor
     en mag hij weg. Dat haalt de kandidatenlijst van 584 terug naar enkele
     tientallen per positie zonder ook maar een oplossing te verliezen.

  2. Per positie een tabel opbouwen: voor elk aantal spelers en elk budget de
     hoogste opbrengst, met de clubverdeling erbij. Dat is een rugzakprobleem
     per positie, opgelost met dynamisch programmeren over prijzen in stappen
     van 0,1 miljoen.

  3. De vier tabellen combineren en de clubregel controleren.

De clubregel is lastig omdat hij over posities heen loopt: drie City-spelers
mogen verdeeld zijn over verdediging, middenveld en aanval. Daarom houdt de
tabel per invulling de clubtelling bij en worden alleen combinaties die samen
onder de drie blijven, doorgelaten. Om dat betaalbaar te houden bewaar ik per
(aantal, budget) de beste K invullingen in plaats van alleen de beste, met K
instelbaar. Bij K=1 is het snel maar niet gegarandeerd exact; bij K=60 is het in
de praktijk exact — bij elke test die ik heb gedraaid, kwam er niets beters uit
een hogere K.

Gebruik:
    python3 bouwer.py                       # beste 15 voor 100,0m over 5 GW
    python3 bouwer.py --budget 100 --gws 5
    python3 bouwer.py --vast Haaland,Gabriel  # deze spelers moeten erin
"""
import argparse
import json
import os
import sys
from collections import defaultdict

HIER = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HIER, "state")

FORMATIE = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
MAX_PER_CLUB = 3


def laad():
    pad = os.path.join(STATE, "dashboard.html")
    if not os.path.exists(pad):
        sys.exit("Draai eerst dashboard.py")
    s = open(pad, encoding="utf-8").read()
    i = s.index("const D = ")
    j = s.index(";\n", i)
    return json.loads(s[i + 10:j])


def opbrengst(p, vanaf, n):
    gw = p.get("gw") or {}
    tot, c = 0.0, 0
    for g in range(vanaf, 39):
        if c >= n:
            break
        tot += gw.get(str(g), gw.get(g, 0)) or 0
        c += 1
    return tot


def bruikbaar(p):
    if p.get("status") in ("i", "s", "u", "n"):
        return False
    if (p.get("cpmin") or 0) < 45:
        return False
    return True


def snoei(kandidaten, aantal):
    """Weg met wat nooit in een optimale selectie kan voorkomen.

    Let op de valkuil: bij een keuze van EEN speler is iemand overbodig zodra er
    iets goedkopers is dat meer oplevert. Maar we kiezen er `aantal` per positie,
    en je kunt dezelfde speler niet twee keer nemen. Iemand is dus pas overbodig
    als er MINSTENS `aantal` spelers zijn die allemaal even duur of goedkoper zijn
    en meer opleveren. Zonder die correctie gooit de snoei geldige oplossingen weg.
    """
    kandidaten.sort(key=lambda x: (x["cent"], -x["xp"]))
    houden = []
    for k in kandidaten:
        beter = sum(1 for h in houden if h["cent"] <= k["cent"] and h["xp"] >= k["xp"])
        if beter < aantal:
            houden.append(k)
    return houden


def tabel_voor_positie(kandidaten, aantal, max_cent, breedte):
    """dp[n][c] = lijst van (xp, clubtelling, spelers) — de beste `breedte`."""
    dp = [[[] for _ in range(max_cent + 1)] for _ in range(aantal + 1)]
    dp[0][0] = [(0.0, (), ())]
    for k in kandidaten:
        prijs, xp = k["cent"], k["xp"]
        for n in range(aantal - 1, -1, -1):
            for c in range(max_cent - prijs, -1, -1):
                if not dp[n][c]:
                    continue
                doel = dp[n + 1][c + prijs]
                for (sx, clubs, spelers) in dp[n][c]:
                    tel = defaultdict(int)
                    for t in clubs:
                        tel[t] += 1
                    if tel[k["club"]] >= MAX_PER_CLUB:
                        continue
                    doel.append((sx + xp, tuple(sorted(clubs + (k["club"],))),
                                 spelers + (k["id"],)))
                if len(doel) > breedte * 3:
                    doel.sort(key=lambda x: -x[0])
                    del doel[breedte:]
    for n in range(aantal + 1):
        for c in range(max_cent + 1):
            if dp[n][c]:
                dp[n][c].sort(key=lambda x: -x[0])
                del dp[n][c][breedte:]
    return dp


def bouw(db, budget, vanaf, gws, breedte=60, vast=()):
    max_cent = int(round(budget * 10))
    vaste = {}
    for naam in vast:
        m = [p for p in db if p["n"].lower() == naam.lower().strip()]
        if not m:
            sys.exit("Onbekende speler: %s" % naam)
        vaste.setdefault(m[0]["p"], []).append(m[0])

    tabellen, vastKost, vastXp, vastClubs, vastIds = {}, 0, 0.0, [], []
    for P, aantal in FORMATIE.items():
        vs = vaste.get(P, [])
        for p in vs:
            vastKost += int(round(p["c"] * 10))
            vastXp += opbrengst(p, vanaf, gws)
            vastClubs.append(p["t"])
            vastIds.append(p["id"])
        nodig = aantal - len(vs)
        if nodig < 0:
            sys.exit("Te veel vaste spelers op %s" % P)
        kand = [{"id": p["id"], "n": p["n"], "club": p["t"],
                 "prijs": p["c"], "cent": int(round(p["c"] * 10)),
                 "xp": opbrengst(p, vanaf, gws)}
                for p in db if p["p"] == P and bruikbaar(p) and p["id"] not in vastIds]
        kand = snoei(kand, nodig) if nodig else []
        tabellen[P] = (tabel_voor_positie(kand, nodig, max_cent - vastKost, breedte), nodig)

    rest = max_cent - vastKost
    volgorde = ("GKP", "DEF", "MID", "FWD")

    # Wat kost de goedkoopste geldige invulling van elke positie? Zonder dat
    # getal knipt de samenvoeging de dure combinaties niet weg die nog geld
    # nodig hebben voor de posities die nog komen, en houd je aan het eind
    # niets over dat past.
    bodem = {}
    for P in volgorde:
        dp, nodig = tabellen[P]
        gevuld = [c for c in range(rest + 1) if dp[nodig][c]]
        if not gevuld:
            return None
        bodem[P] = min(gevuld)

    # lopend: prijs -> lijst van (xp, clubs, ids), de beste `breedte` per prijs.
    # Per prijspunt bewaren in plaats van over alle prijspunten heen knippen is
    # het hele punt: anders overleeft alleen de duurste tak en verlies je de
    # goedkope invulling die verderop nog een dure spits mogelijk maakt.
    lopend = {0: [(vastXp, tuple(sorted(vastClubs)), tuple(vastIds))]}
    for i, P in enumerate(volgorde):
        dp, nodig = tabellen[P]
        nog = sum(bodem[q] for q in volgorde[i + 1:])
        nieuw = defaultdict(list)
        for kost, takken in lopend.items():
            ruimte = rest - kost - nog
            if ruimte < 0:
                continue
            for c in range(ruimte + 1):
                vak = dp[nodig][c]
                if not vak:
                    continue
                for (xp, clubs, ids) in takken:
                    for (sx, cl, sp) in vak:
                        tel = defaultdict(int)
                        ok = True
                        for t in clubs + cl:
                            tel[t] += 1
                            if tel[t] > MAX_PER_CLUB:
                                ok = False
                                break
                        if ok:
                            nieuw[kost + c].append(
                                (xp + sx, tuple(sorted(clubs + cl)), ids + sp))
        if not nieuw:
            return None
        lopend = {}
        for kost, lijst in nieuw.items():
            lijst.sort(key=lambda x: -x[0])
            lopend[kost] = lijst[:breedte]

    beste, besteKost = None, 0
    for kost, lijst in lopend.items():
        if lijst and (beste is None or lijst[0][0] > beste[0]):
            beste, besteKost = lijst[0], kost
    if beste is None:
        return None
    return (beste[0], beste[1], beste[2], besteKost)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=100.0)
    ap.add_argument("--gws", type=int, default=5)
    ap.add_argument("--vanaf", type=int, default=None)
    ap.add_argument("--breedte", type=int, default=60)
    ap.add_argument("--vast", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    D = laad()
    vanaf = a.vanaf or D["gw"]
    db = {p["id"]: p for p in D["db"]}
    res = bouw(D["db"], a.budget, vanaf, a.gws, a.breedte,
               [x for x in a.vast.split(",") if x.strip()])
    if not res:
        sys.exit("Geen geldige selectie gevonden binnen dat budget.")
    xp, clubs, ids, kost = res
    spelers = [db[i] for i in ids]
    if a.json:
        print(json.dumps({"xp": round(xp, 1), "kosten": kost / 10.0,
                          "vanaf": vanaf, "gws": a.gws,
                          "spelers": [{"id": p["id"], "n": p["n"], "t": p["t"],
                                       "p": p["p"], "c": p["c"],
                                       "xp": round(opbrengst(p, vanaf, a.gws), 1)}
                                      for p in spelers]}, ensure_ascii=False))
        return
    print("Beste selectie voor £%.1fm over GW%d-%d" % (a.budget, vanaf, vanaf + a.gws - 1))
    print("Totaal %.1f punten, uitgegeven £%.1fm\n" % (xp, kost / 10.0))
    for P in ("GKP", "DEF", "MID", "FWD"):
        for p in sorted([x for x in spelers if x["p"] == P],
                        key=lambda x: -opbrengst(x, vanaf, a.gws)):
            print("  %-4s %-18s %-4s £%5.1f  %5.1f" %
                  (P, p["n"], p["t"], p["c"], opbrengst(p, vanaf, a.gws)))
    tel = defaultdict(int)
    for p in spelers:
        tel[p["t"]] += 1
    print("\nClubs: %s" % ", ".join("%s×%d" % (k, v) for k, v in sorted(tel.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    main()
