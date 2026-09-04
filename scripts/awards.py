#!/usr/bin/env python3
"""Awards per gameweek, bewaard voor het hele seizoen.

Waarom dit apart staat
----------------------
De awards werden alleen voor de vorige gameweek berekend en daarna weggegooid.
Aan het eind van het seizoen had je dus niets: geen wie-won-wat, geen records,
geen ruzie over wie het vaakst laatste werd. Dit script rekent elke afgeronde
gameweek door en schrijft de uitslag bij in state/awards_historie.json. Wat er
eenmaal in staat blijft staan.

Per categorie leggen we ALTIJD twee kanten vast: wie hem won en wie hem verloor.
Dat tweede is meestal het leukste, en zonder die kant kun je aan het eind van het
seizoen geen tabel maken van wie het vaakst onderaan bungelde.

Alles komt uit state/league_archief.json, dat per manager per gameweek de
volledige vijftien bewaart met punten en minuten. Niets wordt geschat.
"""
import json, os, sys, time
from collections import defaultdict

HIER = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HIER, "state")
ARCHIEF = os.path.join(STATE, "league_archief.json")
PAD = os.path.join(STATE, "awards_historie.json")

CHIPNAAM = {"bboost": "Bench Boost", "3xc": "Triple Captain",
            "wildcard": "Wildcard", "freehit": "Free Hit",
            "manager": "Assistant Manager"}

# Elke categorie: (sleutel, titel, uitleg, hoger_is_beter)
# `hoger_is_beter` bepaalt welke kant "winnaar" heet. Bij Bankdrama is een hoge
# score juist slecht, dus daar staat False — anders zou je een prijs krijgen
# voor punten die je hebt laten liggen.
CATEGORIEEN = [
    ("score",      "Weekscore",       "de meeste punten van de week", True),
    ("aanvoerder", "Aanvoerder",      "wat je aanvoerder opleverde (dubbel geteld)", True),
    ("bank",       "Bankdrama",       "punten die op je bank bleven liggen", False),
    ("gok",        "Beste gok",       "je best scorende speler die bijna niemand had", True),
    ("meeloper",   "Meeloper",        "hoeveel je selectie op de rest lijkt", False),
    ("club",       "Clubverslaving",  "spelers uit één club in je basiself", False),
    ("nullen",     "Nulliters",       "basisspelers die 2 punten of minder haalden", False),
    ("transfer",   "Transfermarkt",   "wat je transfers die week opleverden", True),
    ("waarde",     "Teamwaarde",      "hoeveel je selectie waard werd", True),
]


def _lees(pad, standaard):
    if not os.path.exists(pad):
        return standaard
    try:
        return json.load(open(pad, encoding="utf-8"))
    except Exception:
        return standaard


def meting(gw, managers):
    """Rekent alle categorieën door voor één gameweek.

    Geeft {sleutel: {"waarden": {entry: getal}, "detail": {entry: tekst}}}.
    """
    n = len(managers)
    # hoe vaak komt elke speler voor in deze league? Nodig voor 'gok' en 'meeloper'.
    tel = defaultdict(int)
    for m in managers:
        for p in m["picks"]:
            tel[p["id"]] += 1

    uit = {k: {"waarden": {}, "detail": {}} for k, _, _, _ in CATEGORIEEN}
    for m in managers:
        e = str(m["entry"])
        xi = [p for p in m["picks"] if p["basis"]]
        bank = [p for p in m["picks"] if not p["basis"]]
        chip = m.get("chip")

        # 1. weekscore, na aftrek van strafpunten
        uit["score"]["waarden"][e] = (m.get("punten") or 0)
        uit["score"]["detail"][e] = ("%d punten%s" %
            ((m.get("punten") or 0),
             (" met %s" % CHIPNAAM.get(chip, chip)) if chip else ""))

        # 2. aanvoerder: wat hij dubbel geteld opleverde
        cap = next((p for p in m["picks"] if p.get("cap")), None)
        capp = (cap["pt"] if cap else 0)
        # in het archief staat pt al zonder vermenigvuldiging
        uit["aanvoerder"]["waarden"][e] = capp * (3 if chip == "3xc" else 2)
        uit["aanvoerder"]["detail"][e] = ("%s, %d punten%s" %
            (cap["n"] if cap else "geen", capp,
             " x3 door Triple Captain" if chip == "3xc" else " x2"))

        # 3. bankdrama — bij een Bench Boost telde de bank gewoon mee, dus dan
        #    is er niets blijven liggen en hoort dit op nul
        bankp = sum(p["pt"] for p in bank)
        uit["bank"]["waarden"][e] = 0 if chip == "bboost" else bankp
        beste_bank = max(bank, key=lambda p: p["pt"], default=None)
        uit["bank"]["detail"][e] = ("Bench Boost, alles telde mee" if chip == "bboost"
            else "%d punten, waarvan %s %d" % (bankp, beste_bank["n"], beste_bank["pt"])
                 if beste_bank else "%d punten" % bankp)

        # 4. beste gok: laagst bezeten basisspeler die toch scoorde
        kans = [(tel[p["id"]], p) for p in xi if p["pt"] >= 6]
        if kans:
            kans.sort(key=lambda k: (k[0], -k[1]["pt"]))
            bez, p = kans[0]
            uit["gok"]["waarden"][e] = p["pt"] * (n - bez + 1)
            uit["gok"]["detail"][e] = ("%s, %d punten, en %d van de %d had hem"
                                       % (p["n"], p["pt"], bez, n))
        else:
            uit["gok"]["waarden"][e] = 0
            uit["gok"]["detail"][e] = "geen speler boven 5 punten"

        # 5. meeloper: hoeveel van je vijftien had de rest ook
        gedeeld = sum(tel[p["id"]] - 1 for p in m["picks"])
        pct = round(gedeeld / max(1, (n - 1) * 15) * 100)
        uit["meeloper"]["waarden"][e] = pct
        uit["meeloper"]["detail"][e] = "%d%% van je selectie is gedeeld" % pct

        # 6. clubverslaving
        clubs = defaultdict(int)
        for p in xi:
            clubs[p["t"]] += 1
        if clubs:
            club, aantal = max(clubs.items(), key=lambda kv: kv[1])
            uit["club"]["waarden"][e] = aantal
            uit["club"]["detail"][e] = "%d uit %s" % (aantal, club)
        else:
            uit["club"]["waarden"][e] = 0
            uit["club"]["detail"][e] = "—"

        # 7. nulliters: basisspelers met 2 punten of minder
        nul = [p for p in xi if p["pt"] <= 2]
        uit["nullen"]["waarden"][e] = len(nul)
        uit["nullen"]["detail"][e] = ("%d spelers: %s" % (len(nul),
            ", ".join(p["n"] for p in nul[:4])) if nul else "geen")

        # 8. transfermarkt — alleen zinvol als er transfers waren
        tr = m.get("transfers") or 0
        straf = m.get("straf") or 0
        uit["transfer"]["waarden"][e] = -straf if tr else 0
        uit["transfer"]["detail"][e] = ("%d transfer%s%s" % (tr, "" if tr == 1 else "s",
            ", %d strafpunten" % straf if straf else ", gratis") if tr else "geen transfers")

        # 9. teamwaarde
        uit["waarde"]["waarden"][e] = m.get("waarde") or 0
        uit["waarde"]["detail"][e] = "£%.1fm" % (m.get("waarde") or 0)
    return uit


def uitslag(gw, managers):
    """Winnaar en verliezer per categorie, met gedeelde plaatsen netjes afgehandeld."""
    naam = {str(m["entry"]): m["team"] for m in managers}
    wie = {str(m["entry"]): m["manager"] for m in managers}
    m3 = meting(gw, managers)
    rijen = []
    for sleutel, titel, uitlegtekst, hoog_is_goed in CATEGORIEEN:
        w = m3[sleutel]["waarden"]
        d = m3[sleutel]["detail"]
        if not w:
            continue
        hoogste, laagste = max(w.values()), min(w.values())
        top = hoogste if hoog_is_goed else laagste
        bodem = laagste if hoog_is_goed else hoogste
        winnaars = [e for e, v in w.items() if v == top]
        verliezers = [e for e, v in w.items() if v == bodem]
        # Iedereen gelijk betekent dat er niets te winnen viel. Dan geen prijs,
        # in plaats van willekeurig iemand kronen.
        gelijk = hoogste == laagste
        rijen.append({
            "sleutel": sleutel, "titel": titel, "uitleg": uitlegtekst,
            "hoog_is_goed": hoog_is_goed, "gelijk": gelijk,
            "winnaar": None if gelijk else {
                "entries": winnaars, "teams": [naam[e] for e in winnaars],
                "managers": [wie[e] for e in winnaars],
                "waarde": top, "detail": d.get(winnaars[0], "")},
            "verliezer": None if gelijk else {
                "entries": verliezers, "teams": [naam[e] for e in verliezers],
                "managers": [wie[e] for e in verliezers],
                "waarde": bodem, "detail": d.get(verliezers[0], "")},
            "alles": {e: {"waarde": w[e], "detail": d.get(e, "")} for e in w},
        })
    return rijen


def seizoenstand(historie):
    """Telt over alle gameweeks wie wat won en verloor, plus de records."""
    gewonnen = defaultdict(lambda: defaultdict(int))
    verloren = defaultdict(lambda: defaultdict(int))
    namen = {}
    records = {}
    for gw, blok in sorted(historie.items(), key=lambda kv: int(kv[0])):
        for r in blok["categorieen"]:
            if r["gelijk"]:
                continue
            for e, t in zip(r["winnaar"]["entries"], r["winnaar"]["teams"]):
                gewonnen[e][r["sleutel"]] += 1
                namen[e] = t
            for e, t in zip(r["verliezer"]["entries"], r["verliezer"]["teams"]):
                verloren[e][r["sleutel"]] += 1
                namen[e] = t
            # Records over het hele seizoen.
            # Let op de kant: bij Bankdrama is een LAGE waarde juist goed. Ze
            # "hoogste" en "laagste" noemen leest dan verkeerd om — het gaat om
            # de beste en de slechtste prestatie, en welk getal daarbij hoort
            # verschilt per categorie.
            for kant in ("winnaar", "verliezer"):
                v = r[kant]["waarde"]
                sleutel = "beste" if kant == "winnaar" else "slechtste"
                bestaand = records.get((r["sleutel"], sleutel))
                if bestaand is None:
                    beter = True
                elif r["hoog_is_goed"]:
                    beter = v > bestaand["waarde"] if kant == "winnaar" else v < bestaand["waarde"]
                else:
                    beter = v < bestaand["waarde"] if kant == "winnaar" else v > bestaand["waarde"]
                if beter:
                    records[(r["sleutel"], sleutel)] = {
                        "gw": int(gw), "waarde": v, "teams": r[kant]["teams"],
                        "detail": r[kant]["detail"], "titel": r["titel"]}
    tabel = []
    for e in set(list(gewonnen) + list(verloren)):
        w, v = gewonnen[e], verloren[e]
        tabel.append({"entry": e, "team": namen.get(e, "?"),
                      "gewonnen": dict(w), "verloren": dict(v),
                      "gewonnen_totaal": sum(w.values()),
                      "verloren_totaal": sum(v.values()),
                      "saldo": sum(w.values()) - sum(v.values())})
    tabel.sort(key=lambda r: (-r["saldo"], -r["gewonnen_totaal"]))
    return {"tabel": tabel,
            "records": [{"categorie": k[0], "kant": k[1], **v} for k, v in records.items()]}


def seizoensfeiten(gws):
    """Losse feiten waar je aan het eind van het seizoen ruzie over wilt maken.

    Chips krijgen hier hun eigen behandeling, want die vallen buiten de gewone
    categorieën: een chip is één beslissing per seizoen en het is achteraf
    pijnlijk precies te zien of hij goed viel. We vergelijken wat de chip
    opleverde met wat die manager gemiddeld scoort.
    """
    per_manager = defaultdict(list)
    chips, namen = [], {}
    for gw, blok in sorted(gws.items(), key=lambda kv: int(kv[0])):
        for m in blok.get("managers") or []:
            e = str(m["entry"])
            namen[e] = m["team"]
            per_manager[e].append({"gw": int(gw), "punten": m.get("punten") or 0,
                                   "chip": m.get("chip")})
    for e, rijen in per_manager.items():
        gemiddeld = sum(r["punten"] for r in rijen) / max(1, len(rijen))
        for r in rijen:
            if r["chip"]:
                chips.append({"entry": e, "team": namen[e], "gw": r["gw"],
                              "chip": CHIPNAAM.get(r["chip"], r["chip"]),
                              "punten": r["punten"],
                              "winst": round(r["punten"] - gemiddeld, 1)})
    chips.sort(key=lambda c: -c["winst"])
    weken = [(r["gw"], r["punten"], namen[e]) for e, rijen in per_manager.items() for r in rijen]
    feiten = {
        "chips_beste": chips[:5],
        "chips_slechtste": chips[-3:][::-1] if len(chips) > 5 else [],
        "hoogste_week": max(weken, key=lambda w: w[1]) if weken else None,
        "laagste_week": min(weken, key=lambda w: w[1]) if weken else None,
        "gemiddelde_per_manager": sorted(
            [{"team": namen[e], "gemiddeld": round(sum(r["punten"] for r in rj) / max(1, len(rj)), 1),
              "beste": max(r["punten"] for r in rj), "slechtste": min(r["punten"] for r in rj),
              "weken": len(rj)}
             for e, rj in per_manager.items()], key=lambda r: -r["gemiddeld"]),
    }
    return feiten


def main():
    arch = _lees(ARCHIEF, {})
    gws = arch.get("gameweeks") or {}
    if not gws:
        sys.exit("[awards] geen league_archief.json — draai eerst archief.py")
    oud = _lees(PAD, {"gameweeks": {}})
    historie = oud.get("gameweeks") or {}
    nieuw = 0
    for gw, blok in sorted(gws.items(), key=lambda kv: int(kv[0])):
        managers = blok.get("managers") or []
        if len(managers) < 2:
            continue
        if gw in historie and historie[gw].get("managers") == len(managers):
            continue                       # al gedaan en niets veranderd
        historie[gw] = {"gw": int(gw), "managers": len(managers),
                        "categorieen": uitslag(int(gw), managers)}
        nieuw += 1
    uit = {"bron": "state/league_archief.json (FPL API picks + live punten)",
           "opgehaald": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
           "gameweeks": historie, "seizoen": seizoenstand(historie),
           "feiten": seizoensfeiten(gws)}
    json.dump(uit, open(PAD, "w", encoding="utf-8"), ensure_ascii=False)
    print("[awards] %d gameweeks in het archief (%d nieuw berekend), %d categorieën per week"
          % (len(historie), nieuw, len(CATEGORIEEN)))
    st = uit["seizoen"]["tabel"]
    if st:
        print("[awards] koploper op saldo: %s (%d gewonnen, %d verloren)"
              % (st[0]["team"], st[0]["gewonnen_totaal"], st[0]["verloren_totaal"]))


if __name__ == "__main__":
    main()
