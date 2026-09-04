#!/usr/bin/env python3
"""Koppelt de xPts van FPL Estimator aan FPL-spelers-id's.

Estimator toont per rij "Haaland Haaland Man City": de weergavenaam twee keer
plus de clubnaam voluit. Er is geen id in de pagina, dus koppelen gaat op naam
plus club. Dat is gevoelig voor naamgenoten, en daarom breken we bij twijfel af
op die ene speler in plaats van gokken — dezelfde regel als bij Copilot.

Het cijfer is een TOTAAL over het getoonde venster (standaard vijf gameweeks),
niet per week. Dat wordt zo opgeslagen, en het dashboard vergelijkt het met de
som van de andere bronnen over hetzelfde venster. Anders vergelijk je een
weekcijfer met een vijfweekscijfer en klopt er niets van.
"""
import json, os, re, sys, time, unicodedata, urllib.request

HIER = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HIER, "state")


def plat(s):
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in s if c.isalnum()).lower()


def main():
    pad = os.path.join(STATE, "estimator_ruw.json")
    if not os.path.exists(pad):
        sys.exit("[estimator] geen estimator_ruw.json — draai eerst estimator_ophalen.js")
    ruw = json.load(open(pad, encoding="utf-8"))
    with urllib.request.urlopen(urllib.request.Request(
            "https://fantasy.premierleague.com/api/bootstrap-static/",
            headers={"User-Agent": "Mozilla/5.0"}), timeout=45) as r:
        bs = json.load(r)
    clubs = {t["id"]: t for t in bs["teams"]}
    POS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

    # clubnaam voluit -> FPL-id. Estimator schrijft "Man City", FPL "Man City".
    per_club = {}
    for t in bs["teams"]:
        for naam in (t["name"], t["short_name"]):
            per_club[plat(naam)] = t["id"]

    spelers, ambigu, niet = {}, [], []
    for rij in ruw.get("rijen", []):
        tekst = rij.get("tekst", "")
        # de club staat achteraan; probeer de langste clubnaam die past
        club_id = None
        for naam, tid in sorted(per_club.items(), key=lambda kv: -len(kv[0])):
            if plat(tekst).endswith(naam):
                club_id = tid
                break
        kern = tekst
        if club_id:
            vol = clubs[club_id]["name"]
            kern = re.sub(re.escape(vol) + r"\s*$", "", tekst).strip()
        # Estimator plakt statuslabels tussen de naam en de club: "Doku Doku
        # INJURED Man City" of "Hinshelwood Hinshelwood 50% Brighton". Die eerst
        # weg, anders herkent geen enkele naam zich meer.
        # Let op: geen \b achter het procentteken. Dat is geen woordteken, dus
        # "50%" werd nooit herkend en die spelers vielen alsnog af.
        kern = re.sub(r"\b(?:INJURED|SUSPENDED|DOUBTFUL|UNAVAILABLE)\b|\b\d{1,3}\s*%",
                      " ", kern, flags=re.I).strip()
        kern = re.sub(r"\s+", " ", kern)
        # De weergavenaam staat er twee keer achter elkaar. Bij "Haaland Haaland"
        # is dat één woord, bij "João Pedro João Pedro" zijn het er twee. Deel de
        # woordenlijst dus doormidden in plaats van alleen het eerste woord te
        # nemen — anders vallen alle spelers met een naam van twee woorden af.
        w = kern.split()
        if len(w) >= 2 and len(w) % 2 == 0 and w[:len(w) // 2] == w[len(w) // 2:]:
            kern = " ".join(w[:len(w) // 2])
        elif len(w) >= 2 and w[0] == w[-1]:
            kern = w[0]
        kandidaten = [e for e in bs["elements"]
                      if plat(e["web_name"]) == plat(kern)
                      and POS[e["element_type"]] == rij.get("pos")
                      and (club_id is None or e["team"] == club_id)]
        if not kandidaten:
            niet.append(tekst)
            continue
        if len(kandidaten) > 1:
            ambigu.append(tekst)
            continue
        e = kandidaten[0]
        spelers[str(e["id"])] = {"n": e["web_name"], "xpts": rij["xpts"],
                                 "rang": rij["rang"], "prijs": rij["prijs"]}

    if len(spelers) < 150:
        sys.exit("[estimator] MISLUKT: slechts %d spelers gekoppeld van %d rijen"
                 % (len(spelers), len(ruw.get("rijen", []))))
    # prijzen toetsen: dit vangt een verouderde momentopname
    per_id = {e["id"]: e for e in bs["elements"]}
    ok = sum(1 for k, v in spelers.items()
             if abs(v["prijs"] * 10 - per_id[int(k)]["now_cost"]) <= 2)
    if ok / len(spelers) < 0.70:
        sys.exit("[estimator] MISLUKT: prijzen wijken af bij %d%% — bron verouderd"
                 % round(100 * (1 - ok / len(spelers))))

    venster = ruw.get("_venster")
    uit = {"bron": ruw.get("_bron"),
           "opgehaald": ruw.get("_opgehaald") or time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
           "venster": venster,
           "eenheid": "totaal over het getoonde venster, niet per gameweek",
           "spelers": spelers}
    json.dump(uit, open(os.path.join(STATE, "xp_estimator.json"), "w",
                        encoding="utf-8"), ensure_ascii=False)
    print("[estimator] %d spelers gekoppeld, venster GW%s, prijzen kloppen bij %d%%"
          % (len(spelers), venster or "?", round(100 * ok / len(spelers))))
    if ambigu:
        print("[estimator] overgeslagen wegens naamgenoten: %s" % ", ".join(ambigu[:5]))
    if niet:
        print("[estimator] niet herkend: %s" % ", ".join(niet[:5]))


if __name__ == "__main__":
    main()
