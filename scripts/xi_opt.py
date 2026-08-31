#!/usr/bin/env python3
"""Optimaliseert een selectie van vijftien op wat je er werkelijk mee scoort.

Het probleem dat dit oplost
---------------------------
bouwer.py maximaliseert de som van alle vijftien spelers. Dat is exact
uitgerekend en klopt voor die vraag — maar het is de verkeerde vraag. Je stelt
er elf op. Een bank van 4,5+6,0+6,0+5,0 miljoen levert nul punten; datzelfde
geld in de basis levert punten. In een test parkeerde de bouwer £22,5m van de
£100m op de bank.

Deze pas ruilt spelers zolang de ECHTE score stijgt: de som van het beste
elftal per gameweek, aanvoerder dubbel, precies zoals FPL rekent. Het resultaat
is een goedkope bank en een sterkere basis, binnen exact dezelfde regels
(2-5-5-3, maximaal drie per club, binnen budget).

Waarom een klimzoektocht en geen exacte oplossing: welke elf spelen hangt af
van wie er in de selectie zit, en dat maakt het probleem niet meer op te delen
zoals de bouwer dat doet. Een klimzoektocht vanaf een al goede startselectie
komt er in de praktijk dicht bij en is snel genoeg om voor twaalf gameweeks
twee chips door te rekenen.
"""
XI_MIN = {"DEF": 3, "MID": 2, "FWD": 1}
XI_MAX = {"DEF": 5, "MID": 5, "FWD": 3}
FORMATIE = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
# alle geldige opstellingen, één keer uitgerekend
VORMEN = [(d, m, f)
          for d in range(XI_MIN["DEF"], XI_MAX["DEF"] + 1)
          for m in range(XI_MIN["MID"], XI_MAX["MID"] + 1)
          for f in range(XI_MIN["FWD"], XI_MAX["FWD"] + 1)
          if 1 + d + m + f == 11]


def xi_score(sq, gws):
    """Wat deze vijftien werkelijk opleveren: beste elf per week, aanvoerder dubbel."""
    totaal = 0.0
    for g in gws:
        per = {"GKP": [], "DEF": [], "MID": [], "FWD": []}
        for p in sq:
            per[p["p"]].append(p["gw"].get(g, 0))
        for k in per:
            per[k].sort(reverse=True)
        if not per["GKP"]:
            continue
        beste = 0.0
        for d, m, f in VORMEN:
            if len(per["DEF"]) < d or len(per["MID"]) < m or len(per["FWD"]) < f:
                continue
            waarden = (per["GKP"][:1] + per["DEF"][:d] + per["MID"][:m] + per["FWD"][:f])
            s = sum(waarden) + max(waarden)      # aanvoerder telt dubbel
            if s > beste:
                beste = s
        totaal += beste
    return totaal


def _legaal(sq, budget_cent):
    if sum(FORMATIE.values()) != len(sq):
        return False
    kosten, per, clubs = 0, {}, {}
    for p in sq:
        kosten += int(round(p["c"] * 10))
        per[p["p"]] = per.get(p["p"], 0) + 1
        clubs[p["t"]] = clubs.get(p["t"], 0) + 1
        if clubs[p["t"]] > 3:
            return False
    return kosten <= budget_cent and per == FORMATIE


def _bank(sq, gws):
    """Wie er over dit venster het minst bijdragen — daar zit het verspilde geld."""
    return sorted(range(len(sq)),
                  key=lambda i: sum(sq[i]["gw"].get(g, 0) for g in gws))[:5]


def verbeter(db, ids, budget_cent, gws, per_positie=24, goedkoop=12, rondes=5, minuten=45):
    """Ruil tot geen zet de echte score nog verhoogt.

    Twee soorten zetten, en de tweede is de belangrijke:

    1. Losse ruil — één speler eruit, één erin. Werkt alleen als er geld over
       is. Bij een selectie die het budget precies opmaakt kan dit vrijwel
       niets, en dat is nou juist de selectie die de bouwer aflevert.

    2. Gekoppelde ruil — maak de bank goedkoper EN zet dat geld tegelijk in de
       basis. Dit is de zet die telt: de bouwer parkeerde in een test £22,5m op
       een bank die nul punten oplevert. Alleen samen zijn de twee ruilen
       toegestaan, want los overschrijdt de eerste het budget.

    Geeft (nieuwe ids, score voor, score na) terug.
    """
    dbmap = {p["id"]: p for p in db}
    sq = [dbmap[i] for i in ids if i in dbmap]
    if len(sq) != 15:
        return list(ids), 0.0, 0.0
    start = xi_score(sq, gws)

    bruikbaar = [p for p in db
                 if p.get("status", "a") not in ("i", "s", "u")
                 # LET OP: hier stond "or 90". Een speler zonder minuten-
                 # verwachting werd daarmee behandeld als iemand die negentig
                 # minuten speelt — onbekend werd stilzwijgend gelezen als
                 # gegarandeerde basisspeler. Zo kwam een keeper zonder enige
                 # projectie in de ideale selectie: hij kost weinig en levert
                 # per definitie nul, dus hij "verspilt" geen budget.
                 and (p.get("cpmin") or 0) >= minuten
                 # en zonder projectie valt er niets te kiezen
                 and any(v for v in (p.get("gw") or {}).values())]
    sterk, spot = {}, {}
    for P in FORMATIE:
        kand = [p for p in bruikbaar if p["p"] == P]
        sterk[P] = sorted(kand, key=lambda p: -sum(p["gw"].get(g, 0) for g in gws))[:per_positie]
        # goedkope bankvullers: laagste prijs, bij gelijke prijs de beste
        spot[P] = sorted(kand, key=lambda p: (p["c"], -sum(p["gw"].get(g, 0) for g in gws)))[:goedkoop]

    huidig = start
    for _ in range(rondes):
        beste, beste_score = None, huidig
        bezet = {p["id"] for p in sq}

        # 1. losse ruil
        for i, uit in enumerate(sq):
            for inn in sterk[uit["p"]]:
                if inn["id"] in bezet:
                    continue
                kandidaat = sq[:i] + [inn] + sq[i + 1:]
                if not _legaal(kandidaat, budget_cent):
                    continue
                s = xi_score(kandidaat, gws)
                if s > beste_score + 1e-9:
                    beste_score, beste = s, kandidaat

        # 2. gekoppelde ruil: bank goedkoper, basis sterker
        for i in _bank(sq, gws):
            uit = sq[i]
            for filler in spot[uit["p"]]:
                if filler["id"] in bezet or filler["c"] >= uit["c"]:
                    continue
                tussen = sq[:i] + [filler] + sq[i + 1:]
                vrij = int(round((uit["c"] - filler["c"]) * 10))
                for j, uit2 in enumerate(tussen):
                    if j == i:
                        continue
                    for inn2 in sterk[uit2["p"]]:
                        if inn2["id"] in bezet or inn2["id"] == filler["id"]:
                            continue
                        if int(round((inn2["c"] - uit2["c"]) * 10)) > vrij:
                            continue
                        kandidaat = tussen[:j] + [inn2] + tussen[j + 1:]
                        if not _legaal(kandidaat, budget_cent):
                            continue
                        s = xi_score(kandidaat, gws)
                        if s > beste_score + 1e-9:
                            beste_score, beste = s, kandidaat
        if not beste:
            break
        sq, huidig = beste, beste_score
    return [p["id"] for p in sq], round(start, 2), round(huidig, 2)
