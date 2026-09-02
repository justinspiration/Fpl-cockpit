#!/usr/bin/env python3
"""Bewaart per speler wat hij in elke afgelopen gameweek daadwerkelijk deed.

Waarom dit apart wordt bewaard
------------------------------
De FPL API kent geen "geef mij het hele seizoen"-endpoint per speler zonder
626 losse verzoeken. Wel is er /event/{gw}/live/: dat geeft in EEN verzoek de
volledige statistiek van alle spelers voor die gameweek. Wij halen dat per
afgeronde gameweek een keer op en schrijven het bij. Wat er eenmaal in staat,
blijft staan — ook nadat FPL de velden overschrijft bij de seizoenswissel.

Dat laatste is niet theoretisch. De rauwe velden in /bootstrap-static/ dragen na
gameweek 1 alleen nog het lopende seizoen; wie daarop bouwt, kijkt naar cijfers
die stilletjes van betekenis zijn veranderd.

Per speler per gameweek gaat mee: minuten, punten, goals, assists, clean sheet,
tegendoelpunten, reddingen, bonus, BPS, DefCon, kaarten, penalty's, eigen doelpunt,
xG en xA, plus de tegenstander en of het thuis was. En de puntenopbouw zelf uit
FPL's `explain`, zodat je kunt zien waar elk punt vandaan kwam.
"""
import json, os, ssl, time, urllib.request

HIER = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HIER, "state")
BASE = "https://fantasy.premierleague.com/api"
PAD = os.path.join(STATE, "historie.json")

# Alleen wat we echt tonen. Nulwaarden slaan we niet op: over 38 gameweeks
# scheelt dat het leeuwendeel van het bestand.
VELDEN = [("minutes", "min"), ("total_points", "pt"), ("goals_scored", "g"),
          ("assists", "a"), ("clean_sheets", "cs"), ("goals_conceded", "gc"),
          ("saves", "sv"), ("bonus", "bo"), ("bps", "bps"),
          ("defensive_contribution", "dc"), ("yellow_cards", "geel"),
          ("red_cards", "rood"), ("own_goals", "og"),
          ("penalties_saved", "ps"), ("penalties_missed", "pm"),
          ("starts", "st")]
KOMMA = [("expected_goals", "xg"), ("expected_assists", "xa")]


def get(url, pogingen=3):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    fout = None
    for p in range(1, pogingen + 1):
        try:
            with urllib.request.urlopen(req, timeout=45,
                                        context=ssl.create_default_context()) as r:
                return json.load(r)
        except Exception as ex:
            fout = ex
            if p < pogingen:
                time.sleep(2 * p)
    raise SystemExit("[historie] MISLUKT bij %s: %s" % (url, fout))


def main():
    bs = get(BASE + "/bootstrap-static/")
    kort = {t["id"]: t["short_name"] for t in bs["teams"]}
    afgerond = [e["id"] for e in bs["events"] if e.get("finished")]
    # De lopende gameweek telt ook mee, maar anders. FPL zet `finished` pas om
    # 09:00 UK de dag na de laatste wedstrijd; tot dat moment staan de bonuspunten
    # nog niet vast. We halen hem daarom elke ronde opnieuw op en labelen hem als
    # voorlopig, in plaats van hem te verzwijgen tot hij officieel dicht is.
    lopend = next((e["id"] for e in bs["events"]
                   if e.get("is_current") and not e.get("finished")), None)
    if not afgerond and not lopend:
        print("[historie] nog geen gespeelde gameweek — niets te doen")
        return

    oud = {"spelers": {}, "gws": []}
    if os.path.exists(PAD):
        try:
            oud = json.load(open(PAD, encoding="utf-8"))
        except Exception:
            print("[historie] bestaand bestand onleesbaar, opnieuw opbouwen")
            oud = {"spelers": {}, "gws": []}
    hebben = set(int(g) for g in (oud.get("gws") or []))
    nodig = [g for g in afgerond if g not in hebben]
    if lopend:
        nodig.append(lopend)          # altijd verversen zolang hij loopt
        hebben.discard(lopend)
    if not nodig:
        print("[historie] al bij: GW%s" % ", GW".join(str(g) for g in sorted(hebben)))
        return

    # tegenstander per duel, zodat elke regel leesbaar is zonder extra opzoekwerk
    fixtures = get(BASE + "/fixtures/")
    duel = {f["id"]: (f["team_h"], f["team_a"]) for f in fixtures}
    van = {e["id"]: e["team"] for e in bs["elements"]}

    spelers = oud.get("spelers") or {}
    for g in nodig:
        live = get("%s/event/%d/live/" % (BASE, g))
        n = 0
        for e in live.get("elements", []):
            st = e.get("stats") or {}
            if not st.get("minutes") and not st.get("total_points"):
                continue                      # niet gespeeld en niets verdiend
            rij = {}
            for lang, kortnaam in VELDEN:
                v = st.get(lang) or 0
                if v:
                    rij[kortnaam] = v
            for lang, kortnaam in KOMMA:
                try:
                    v = round(float(st.get(lang) or 0), 2)
                except (TypeError, ValueError):
                    v = 0.0
                if v:
                    rij[kortnaam] = v
            # tegenstander en thuis/uit
            mijn = van.get(e["id"])
            for blok in (e.get("explain") or []):
                paar = duel.get(blok.get("fixture"))
                if paar and mijn in paar:
                    thuis = paar[0] == mijn
                    rij["opp"] = kort.get(paar[1] if thuis else paar[0])
                    rij["thuis"] = 1 if thuis else 0
                    break
            # waar kwamen de punten vandaan? Dit is FPL's eigen uitsplitsing.
            opbouw = {}
            for blok in (e.get("explain") or []):
                for s in blok.get("stats") or []:
                    p = (s.get("points") or 0) + (s.get("points_modification") or 0)
                    if p:
                        opbouw[s["identifier"]] = opbouw.get(s["identifier"], 0) + p
            if opbouw:
                rij["uit"] = opbouw
            spelers.setdefault(str(e["id"]), {})[str(g)] = rij
            n += 1
        hebben.add(g)
        print("[historie] GW%d: %d spelers met speeltijd%s"
              % (g, n, "  (voorlopig — bonuspunten nog niet vast)"
                 if g == lopend else ""))

    uit = {"bron": "FPL API /event/{gw}/live/",
           "opgehaald": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
           "gws": sorted(hebben), "lopend": lopend, "spelers": spelers}
    os.makedirs(STATE, exist_ok=True)
    json.dump(uit, open(PAD, "w", encoding="utf-8"), ensure_ascii=False)
    kb = os.path.getsize(PAD) / 1024
    print("[historie] GW%s bewaard, %d spelers, %.0f KB"
          % (", GW".join(str(g) for g in sorted(hebben)), len(spelers), kb))


if __name__ == "__main__":
    main()
