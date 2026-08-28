#!/usr/bin/env python3
"""Bouwt de Premier League-stand en de vorm per club.

Waar het vandaan komt
---------------------
De stand rekenen we zelf uit de officiele FPL-API (/fixtures/). Die geeft per
wedstrijd de eindstand zodra hij is afgelopen: team_h_score, team_a_score,
finished. Daaruit volgt alles wat ook op premierleague.com staat — gespeeld,
winst, gelijk, verlies, doelpunten voor en tegen, saldo, punten — plus de vorm
over de laatste vijf duels. Geen tussenpartij, geen scrape: dezelfde bron
waar de rest van dit dashboard op draait.

Waarom niet de velden 'played', 'points' en 'position' uit bootstrap-static:
die vult FPL pas na verloop van tijd en ze stonden op 16-08-2026 allemaal op
nul terwijl de fixtures wel al bestonden. Zelf optellen is betrouwbaarder en
je kunt het narekenen.

Oefenwedstrijden: waarom die er NIET in zitten
----------------------------------------------
Justin vroeg om de vorm inclusief de voorbereiding. Die wedstrijden staan niet
in de FPL-API — daar zit alleen de competitie in. Ik heb TheSportsDB getest als
aanvulling en hem laten vallen, om vier redenen die stuk voor stuk genoeg zijn:

  1. Hun gratis laag geeft precies EEN wedstrijd per club terug, geen vijf.
  2. Hun eigen clublijst voor de Premier League is verouderd: er staan clubs
     uit League One en Two in.
  3. Zoeken op naam koppelde 12 van de 20 clubs; de rest viel af of leverde
     het vrouwenteam op.
  4. Na een handvol verzoeken volgt HTTP 429, en een uitslag die eruit kwam
     (Bournemouth 10-1) was niet te verifieren.

Data waar je niet op kunt bouwen is erger dan geen data. Wil je de voorbereiding
er alsnog bij, dan is een betaalde sleutel bij TheSportsDB of football-data.org
de weg; dan komt dit blok terug met een bron die wel klopt.

Gebruik:  python3 stand.py
"""
import json
import os
import re
import sys
import unicodedata
import urllib.request
from datetime import datetime, timezone

HIER = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HIER, "state")
BASE = "https://fantasy.premierleague.com/api"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) fpl-cockpit"}


def get(url, timeout=25):
    try:
        return json.load(urllib.request.urlopen(
            urllib.request.Request(url, headers=UA), timeout=timeout))
    except Exception:
        return None


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", " ", s).strip()


def main():
    bs = get(BASE + "/bootstrap-static/")
    fixtures = get(BASE + "/fixtures/")
    if not bs or fixtures is None:
        sys.exit("FPL API niet bereikbaar")

    teams = {t["id"]: {"naam": t["name"], "kort": t["short_name"], "id": t["id"]}
             for t in bs["teams"]}
    leeg = {"gespeeld": 0, "winst": 0, "gelijk": 0, "verlies": 0,
            "voor": 0, "tegen": 0, "punten": 0, "vorm": [], "duels": []}
    # LET OP: dict(leeg, ...) kopieert alleen de buitenste laag. De lijsten
    # "vorm" en "duels" bleven daardoor door alle twintig clubs gedeeld, en
    # elke append kwam in dezelfde lijst terecht. Gevolg: iedere club toonde
    # dezelfde vorm en dezelfde acht wedstrijden.
    import copy as _copy
    tabel = {t["short_name"]: dict(_copy.deepcopy(leeg), naam=t["name"], kort=t["short_name"])
             for t in bs["teams"]}

    klaar = [f for f in fixtures
             if f.get("finished") and f.get("team_h_score") is not None]
    klaar.sort(key=lambda f: f.get("kickoff_time") or "")

    for f in klaar:
        h, a = teams[f["team_h"]]["kort"], teams[f["team_a"]]["kort"]
        hs, as_ = f["team_h_score"], f["team_a_score"]
        for kort, eigen, ander, thuis, opp in ((h, hs, as_, True, a), (a, as_, hs, False, h)):
            r = tabel[kort]
            r["gespeeld"] += 1
            r["voor"] += eigen
            r["tegen"] += ander
            if eigen > ander:
                r["winst"] += 1; r["punten"] += 3; uit = "W"
            elif eigen == ander:
                r["gelijk"] += 1; r["punten"] += 1; uit = "G"
            else:
                r["verlies"] += 1; uit = "V"
            r["vorm"].append(uit)
            r["duels"].append({"gw": f.get("event"), "opp": opp, "thuis": thuis,
                               "voor": eigen, "tegen": ander, "uit": uit,
                               "datum": (f.get("kickoff_time") or "")[:10],
                               "soort": "competitie"})

    for r in tabel.values():
        r["saldo"] = r["voor"] - r["tegen"]
        r["vorm"] = r["vorm"][-5:]
        r["duels"] = r["duels"][-8:]
        # vormpunten over de laatste vijf: waar staat de ploeg nu
        r["vormpunten"] = sum({"W": 3, "G": 1, "V": 0}[x] for x in r["vorm"])
        r["vormmax"] = len(r["vorm"]) * 3

    rangschikking = sorted(tabel.values(),
                           key=lambda r: (-r["punten"], -r["saldo"], -r["voor"], r["naam"]))
    for i, r in enumerate(rangschikking, 1):
        r["positie"] = i

    data = {
        "_bron": "FPL API /fixtures/ — de eindstanden van de afgeronde wedstrijden",
        "_methode": ("De stand is opgeteld uit de eindstanden in /fixtures/: 3 punten voor "
                     "winst, 1 voor gelijk. Gelijk in punten wordt gescheiden op doelsaldo, "
                     "dan op doelpunten voor, precies zoals de Premier League het doet. "
                     "Vorm is de laatste vijf competitieduels."),
        "_oefen": ("Niet opgenomen. De FPL-API kent alleen competitieduels, en de gratis "
                   "bronnen voor oefenwedstrijden bleken onbetrouwbaar: een wedstrijd per "
                   "club, een verouderde clublijst, 12 van de 20 clubs koppelbaar en een "
                   "uitslag die niet klopte. Zie de toelichting boven in stand.py."),
        "_opgehaald": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "_gespeeld": len(klaar),
        "tabel": rangschikking,
    }
    with open(os.path.join(STATE, "stand.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    print(f"stand.json: {len(klaar)} competitiewedstrijden verwerkt")
    if klaar:
        print("  pos  club                 G  W  G  V   DV  DT  DS  Ptn  vorm")
        for r in rangschikking[:6]:
            print(f"  {r['positie']:3d}  {r['naam'][:20]:20s} {r['gespeeld']:2d} {r['winst']:2d} "
                  f"{r['gelijk']:2d} {r['verlies']:2d}  {r['voor']:3d} {r['tegen']:3d} "
                  f"{r['saldo']:+3d}  {r['punten']:3d}  {''.join(r['vorm'])}")
    else:
        print("  Nog geen competitiewedstrijd gespeeld — de tabel vult zich vanaf GW1.")


if __name__ == "__main__":
    main()
