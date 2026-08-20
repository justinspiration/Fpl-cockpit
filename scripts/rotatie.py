#!/usr/bin/env python3
"""Rotatiesignaal: welke clubs spelen doordeweeks in Europa vóór een gameweek?

Waarom dit ertoe doet
---------------------
Een club die dinsdag in Madrid speelt en zaterdag om 12:30 thuis moet aantreden,
rouleert. Niet iedereen: een vaste kracht als Haaland of Saliba speelt vrijwel
altijd. Maar de tweede linie krijgt rust, en dat is precies waar rotatierisico
zit — bij spelers van 5,0 tot 7,0 miljoen die je opstelt omdat ze goedkoop zijn.

Wat dit script doet
-------------------
1. Het kent de Europese speeldagen 2026/27 (bron: UEFA via Wikipedia, zie DATA).
2. Het haalt de werkelijke Premier League-speeldata op uit de FPL API.
3. Per gameweek en per club rekent het uit hoeveel dagen er tussen het Europese
   duel en het competitieduel zitten.
4. Daaruit volgt een rustscore: minder dan drie dagen is krap, vier is normaal.

Wat dit NIET doet
-----------------
Het voorspelt niet wie er precies gerouleerd wordt. Dat hangt af van de trainer,
de stand en het belang van het duel — informatie die uit persconferenties komt,
en die verwerkt het nieuwsoverzicht. Dit is het structurele deel: welke clubs
staan onder druk, en in welke weken.

Gebruik:  python3 rotatie.py
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

HIER = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HIER, "state")
API = "https://fantasy.premierleague.com/api/"

# ── Europese speeldagen 2026/27 ───────────────────────────────────────
# Bron: UEFA, overgenomen via de Wikipedia-artikelen van de drie toernooien,
# opgehaald 15-08-2026. Datums zijn de speelvensters; UEFA verdeelt de duels
# over de genoemde dagen.
EUROPA = {
    "Champions League": {
        "clubs": ["ARS", "MCI", "MUN", "AVL", "LIV"],
        "speeldagen": [
            ("2026-09-08", "2026-09-10"), ("2026-10-13", "2026-10-14"),
            ("2026-10-20", "2026-10-21"), ("2026-11-03", "2026-11-04"),
            ("2026-11-24", "2026-11-25"), ("2026-12-08", "2026-12-09"),
            ("2027-01-19", "2027-01-20"), ("2027-01-27", "2027-01-27"),
            ("2027-02-16", "2027-02-17"), ("2027-02-23", "2027-02-24"),
            ("2027-03-09", "2027-03-10"), ("2027-03-16", "2027-03-17"),
            ("2027-04-06", "2027-04-07"), ("2027-04-13", "2027-04-14"),
            ("2027-04-27", "2027-04-28"), ("2027-05-04", "2027-05-05"),
        ],
    },
    "Europa League": {
        "clubs": ["CRY", "BOU", "SUN"],
        "speeldagen": [
            ("2026-09-16", "2026-09-17"), ("2026-10-15", "2026-10-15"),
            ("2026-10-22", "2026-10-22"), ("2026-11-05", "2026-11-05"),
            ("2026-11-26", "2026-11-26"), ("2026-12-10", "2026-12-10"),
            ("2027-01-21", "2027-01-21"), ("2027-01-28", "2027-01-28"),
            ("2027-02-18", "2027-02-25"), ("2027-03-11", "2027-03-18"),
            ("2027-04-08", "2027-04-15"), ("2027-04-29", "2027-05-06"),
        ],
    },
    "Conference League": {
        "clubs": ["BHA"],
        # UEFA publiceert de losse speeldagen van dit toernooi nog niet per
        # matchday; het venster loopt van 15-10-2026 tot 02-06-2027. Donderdag
        # is de vaste speeldag, dus die nemen we als benadering en markeren dat.
        "speeldagen": [],
        "benadering": {"van": "2026-10-15", "tot": "2027-06-02", "weekdag": 3},
    },
}
BRON = ("UEFA-speelkalenders 2026/27 via Wikipedia, opgehaald 15-08-2026. "
        "Champions League en Europa League met exacte speeldagen; "
        "Conference League als benadering op donderdagen, omdat UEFA de losse "
        "speeldagen daarvan nog niet per matchday publiceert.")


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "fpl-cockpit"})
    return json.load(urllib.request.urlopen(req, timeout=30))


def dag(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def europese_data(club):
    """De speelvensters van deze club, als (eerste dag, laatste dag, toernooi, exact).

    Belangrijk: een venster van 8 tot en met 10 september is EEN duel op een van
    die drie dagen, niet drie duels. UEFA maakt de precieze dag pas later bekend.
    Daarom houden we het venster heel en rapporteren we straks een bandbreedte
    in plaats van te doen alsof we de dag al weten.
    """
    uit = []
    for toernooi, d in EUROPA.items():
        if club not in d["clubs"]:
            continue
        for van, tot in d["speeldagen"]:
            uit.append((dag(van), dag(tot), toernooi, True))
        ben = d.get("benadering")
        if ben:
            a, b = dag(ben["van"]), dag(ben["tot"])
            while a <= b:
                if a.weekday() == ben["weekdag"]:
                    uit.append((a, a, toernooi, False))
                a += timedelta(days=1)
    return uit


def main():
    bs = get(API + "bootstrap-static/")
    fixtures = get(API + "fixtures/")
    sn = {t["id"]: t["short_name"] for t in bs["teams"]}

    alle_clubs = sorted({c for d in EUROPA.values() for c in d["clubs"]})
    onbekend = [c for c in alle_clubs if c not in sn.values()]
    if onbekend:
        print("LET OP: clubs niet in de FPL-data: %s" % ", ".join(onbekend), file=sys.stderr)

    kalender = {c: europese_data(c) for c in alle_clubs}
    per_gw = {}

    for m in fixtures:
        g = m.get("event")
        if not g or not m.get("kickoff_time"):
            continue
        kick = datetime.fromisoformat(m["kickoff_time"].replace("Z", "+00:00")).date()
        for tid in (m["team_h"], m["team_a"]):
            club = sn[tid]
            if club not in kalender:
                continue
            # dichtstbijzijnde Europees venster vóór dit competitieduel.
            # min = het duel viel op de laatste dag van het venster (minste rust),
            # max = op de eerste dag (meeste rust). We melden allebei.
            eerder = []
            for a, b, t, exact in kalender[club]:
                if not (0 < (kick - b).days <= 7):
                    continue
                eerder.append(((kick - b).days, (kick - a).days, t, exact))
            if not eerder:
                continue
            rmin, rmax, toernooi, exact = min(eerder, key=lambda x: x[0])
            # pas 'krap' als het ook in het gunstigste geval krap is; anders
            # 'mogelijk krap' zolang UEFA de speeldag niet heeft vastgesteld
            if rmax <= 3:
                zwaarte = "krap"
            elif rmin <= 3:
                zwaarte = "mogelijk krap"
            elif rmin == 4:
                zwaarte = "normaal"
            else:
                zwaarte = "ruim"
            per_gw.setdefault(str(g), {})[club] = {
                "rustdagen": rmin, "rustdagen_max": rmax,
                "toernooi": toernooi, "exact": exact,
                "zwaarte": zwaarte,
                "pl_datum": kick.isoformat(),
            }

    # ook het omgekeerde: een Europees duel kort NA de gameweek drukt de
    # opstelling evengoed, zeker bij een belangrijk toernooiduel
    for m in fixtures:
        g = m.get("event")
        if not g or not m.get("kickoff_time"):
            continue
        kick = datetime.fromisoformat(m["kickoff_time"].replace("Z", "+00:00")).date()
        for tid in (m["team_h"], m["team_a"]):
            club = sn[tid]
            if club not in kalender:
                continue
            later = [((a - kick).days, t) for a, b, t, _ in kalender[club]
                     if 0 < (a - kick).days <= 3]
            if not later:
                continue
            dagen, toernooi = min(later, key=lambda x: x[0])
            rij = per_gw.setdefault(str(g), {}).setdefault(club, {
                "rustdagen": None, "toernooi": toernooi, "exact": True,
                "zwaarte": "normaal", "pl_datum": kick.isoformat()})
            rij["europa_erna"] = dagen
            if dagen <= 2 and rij["zwaarte"] in ("ruim", "normaal"):
                rij["zwaarte"] = "mogelijk krap"

    data = {
        "_bron": BRON,
        "_opgehaald": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "_uitleg": ("Rustdagen = het aantal dagen tussen het Europese duel en het "
                    "competitieduel van die gameweek. 3 of minder is krap, 4 is normaal, "
                    "5 of meer is ruim. 'europa_erna' betekent dat er kort NA de gameweek "
                    "een Europees duel staat, wat ook op de opstelling drukt."),
        "_waarschuwing": ("Dit zegt welke clubs onder druk staan, niet wie er gerouleerd "
                          "wordt. Vaste krachten spelen vrijwel altijd; het risico zit bij "
                          "de tweede linie. Persconferenties staan in het nieuwsoverzicht."),
        "clubs": {c: [t for t, d in EUROPA.items() if c in d["clubs"]][0] for c in alle_clubs},
        "per_gw": per_gw,
    }
    with open(os.path.join(STATE, "rotatie.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    krap = sum(1 for g in per_gw.values() for r in g.values() if r["zwaarte"] == "krap")
    mog = sum(1 for g in per_gw.values() for r in g.values() if r["zwaarte"] == "mogelijk krap")
    print(f"rotatie.json: {len(alle_clubs)} clubs in Europa, {len(per_gw)} gameweeks geraakt")
    print(f"  zeker krap: {krap} · mogelijk krap: {mog}")
    for g in sorted(per_gw, key=int)[:8]:
        rijen = sorted(per_gw[g].items(), key=lambda x: x[1].get("rustdagen") or 9)
        oms = ", ".join(
            f"{c} {r['rustdagen']}-{r.get('rustdagen_max', r['rustdagen'])}d"
            + ("!" if r["zwaarte"] == "krap" else "?" if r["zwaarte"] == "mogelijk krap" else "")
            for c, r in rijen if r.get("rustdagen"))
        if oms:
            print(f"  GW{g}: {oms}")


if __name__ == "__main__":
    main()
