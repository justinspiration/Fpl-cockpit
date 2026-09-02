#!/usr/bin/env python3
"""Haalt de puntenvoorspellingen van FPL Form op als tweede bron naast FPL Copilot.

Waarom deze bron ernaast staat: Copilot geeft één getal per gameweek waarin
speelkans en puntenverwachting al vermenigvuldigd zijn. FPL Form levert die twee
apart — punten-als-hij-speelt en kans-dat-hij-speelt. Juist dat onderscheid is
waar de twee modellen uiteenlopen, en waar wij een oordeel over willen vellen.

Bron: https://fplform.com/export-fpl-form-data (Nicholas Hope).
Hun voorwaarden staan publicatie van de ruwe cijfers NIET toe; afgeleide analyse
mag wel, mits 'FPL Form' met link vermeld wordt. Daarom slaan we de ruwe waarden
alleen lokaal op en publiceert het dashboard uitsluitend het gemengde cijfer.
"""
import json, os, ssl, sys, time, urllib.error, urllib.parse, urllib.request

HIER = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HIER, "state")
URL = "https://fplform.com/export-fpl-form-data.php"
FPL = "https://fantasy.premierleague.com/api/bootstrap-static/"


def volgende_gw():
    """Eerste gameweek die nog niet gespeeld is, uit de FPL API zelf."""
    with urllib.request.urlopen(FPL, timeout=30) as r:
        bs = json.load(r)
    for e in bs["events"]:
        if e.get("is_next"):
            return e["id"]
    for e in bs["events"]:
        if not e.get("finished"):
            return e["id"]
    return 38


def haal(eerste, laatste, pogingen=3):
    data = urllib.parse.urlencode({"all": 1, "firstgw": eerste,
                                   "lastgw": laatste}).encode()
    req = urllib.request.Request(URL, data=data, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://fplform.com/export-fpl-form-data",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
    laatste_fout = None
    for poging in range(1, pogingen + 1):
        try:
            with urllib.request.urlopen(req, timeout=90,
                                        context=ssl.create_default_context()) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as ex:                      # netwerk, 5xx, time-out
            laatste_fout = ex
            if poging < pogingen:
                time.sleep(3 * poging)
    raise SystemExit("[fplform] MISLUKT na %d pogingen: %s" % (pogingen, laatste_fout))


def main():
    gw = volgende_gw()
    eind = min(38, gw + 9)
    print("[fplform] ophalen GW%d t/m GW%d" % (gw, eind))
    tekst = haal(gw, eind)
    regels = [r for r in tekst.splitlines() if r.strip()]
    if len(regels) < 50 or not regels[0].startswith("ID,Name,Team,Pos,Price"):
        raise SystemExit("[fplform] MISLUKT: onverwacht bestand (%d regels, kop %r)"
                         % (len(regels), regels[0][:80] if regels else ""))
    import csv, io
    rijen = list(csv.DictReader(io.StringIO(tekst)))

    spelers, gewogen = {}, 0
    for r in rijen:
        try:
            pid = int(r["ID"])
        except (TypeError, ValueError):
            continue
        pg, kans = {}, {}
        for g in range(gw, eind + 1):
            k1, k2 = "%d_with_prob" % g, "%d_prob" % g
            if k1 in r and r[k1] not in (None, ""):
                pg[str(g)] = round(float(r[k1]), 3)
            if k2 in r and r[k2] not in (None, ""):
                kans[str(g)] = round(float(r[k2]), 3)
        if pg:
            spelers[str(pid)] = {"n": r["Name"], "t": r["Team"], "p": r["Pos"],
                                 "xp": pg, "kans": kans}
            gewogen += 1

    if gewogen < 300:
        raise SystemExit("[fplform] MISLUKT: slechts %d spelers gelezen, "
                         "verwacht 500+. Niets weggeschreven." % gewogen)

    uit = {"bron": "FPL Form (fplform.com)", "opgehaald": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
           "vanaf_gw": gw, "tot_gw": eind, "spelers": spelers}
    os.makedirs(STATE, exist_ok=True)
    pad = os.path.join(STATE, "xp_fplform.json")
    json.dump(uit, open(pad, "w", encoding="utf-8"), ensure_ascii=False)
    print("[fplform] %d spelers opgeslagen in %s" % (gewogen, os.path.basename(pad)))


if __name__ == "__main__":
    main()
