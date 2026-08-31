#!/usr/bin/env python3
"""
Opta-laag: zet de opgehaalde Opta-data om naar bruikbare projecties.

HERKOMST VAN ELKE WAARDE (Justins eis: nul verzonnen data)
  state/opta_raw.tsv    spelers, Opta via theanalyst.com/competition/premier-league/stats
                        tabblad PLAYERS > Attacking. Kolommen:
                        naam, apps, min, goals, xG, goals-vs-xG, shots, SOT, conv%, xG/shot
  state/opta_teams.tsv  clubs, Opta via theanalyst.com/competition/premier-league/table
                        tabblad EXPECTED. Kolommen: naam, gespeeld, xGF, xGA, xGD, xPTS
  PROMOVENDI            Coventry/Hull/Ipswich hebben GEEN PL-data (speelden Championship).
                        Bron eindstand 25/26: Wikipedia 2025-26 EFL Championship.
                        Omrekening naar PL-niveau met gepubliceerde promotie-effecten:
                          aanval  -33,5% xG per duel
                          verdediging  +60% tegendoelpunten per duel
                        Bron: databetweenthelines.substack.com "The Promotion Effect".
                        Deze drie clubs worden in de output gemarkeerd met bron="championship"
                        zodat het dashboard kan tonen dat het een omrekening is.
  THUIS/UIT             FPL's eigen strength_overall_home / _away uit /bootstrap-static/.
                        Geen zelfbedachte thuisvoordeel-factor.

BEREKENING PER FIXTURE (transparant, geen black box)
  lambda_voor  = (xGF_club / 38) * (xGA_tegenstander / competitiegemiddelde xGA) * sterkteverhouding
  clean sheet% = Poisson(0 tegendoelpunten) = exp(-lambda_tegen)
Dit is een BEREKENING op basis van Opta-data, geen Opta-projectie. Het dashboard
labelt het als zodanig.
"""
import os, json, math, unicodedata, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
BASE = "https://fantasy.premierleague.com/api"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

# Opta-clubnaam -> FPL-afkorting
CLUBMAP = {
    "Arsenal": "ARS", "Man City": "MCI", "Man Utd": "MUN", "Liverpool": "LIV",
    "Chelsea": "CHE", "Brighton": "BHA", "Bournemouth": "BOU", "Brentford": "BRE",
    "Newcastle": "NEW", "Crystal Palace": "CRY", "Palace": "CRY", "Leeds": "LEE",
    "Aston Villa": "AVL", "Villa": "AVL", "Fulham": "FUL", "Everton": "EVE",
    "Spurs": "TOT", "Nottm Forest": "NFO", "Forest": "NFO", "Sunderland": "SUN",
}

# Championship 25/26 eindstand (46 duels) — Wikipedia 2025-26 EFL Championship
# xG uit dezelfde bronnenreeks als de promotieanalyse.
PROMOVENDI = {
    "COV": {"naam": "Coventry City", "gespeeld": 46, "gf": 97, "ga": 45, "xg": 87.3, "positie": "kampioen"},
    "IPS": {"naam": "Ipswich Town",  "gespeeld": 46, "gf": 80, "ga": 47, "xg": 79.2, "positie": "2e"},
    "HUL": {"naam": "Hull City",     "gespeeld": 46, "gf": 70, "ga": 66, "xg": 59.7, "positie": "play-offs"},
}
AANVAL_DROP = 0.665      # -33,5% xG per duel bij promotie
VERDEDIGING_STIJGING = 1.60  # +60% tegendoelpunten per duel


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def get(u):
    return json.load(urllib.request.urlopen(
        urllib.request.Request(u, headers={"User-Agent": UA}), timeout=30))


def laad_teams():
    """xGF/xGA per club, omgerekend naar 38 duels. Promovendi via Championship."""
    p = os.path.join(STATE, "opta_teams.tsv")
    uit = {}
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            d = line.rstrip("\n").split("\t")
            if len(d) < 4:
                continue
            sn = CLUBMAP.get(d[0])
            if not sn:
                continue
            uit[sn] = {"xgf": float(d[2]), "xga": float(d[3]),
                       "bron": "opta-pl", "club": d[0]}
    for sn, c in PROMOVENDI.items():
        xgf38 = c["xg"] / c["gespeeld"] * AANVAL_DROP * 38
        xga38 = c["ga"] / c["gespeeld"] * VERDEDIGING_STIJGING * 38
        uit[sn] = {"xgf": round(xgf38, 1), "xga": round(xga38, 1),
                   "bron": "championship", "club": c["naam"],
                   "toelichting": "Championship 25/26 (%d duels, %d-%d, xG %.1f), omgerekend: "
                                  "aanval -33,5%%, verdediging +60%%"
                                  % (c["gespeeld"], c["gf"], c["ga"], c["xg"])}
    return uit


def laad_spelers():
    """Opta-spelersregels op genormaliseerde naam."""
    p = os.path.join(STATE, "opta_raw.tsv")
    uit = {}
    if not os.path.exists(p):
        return uit
    for line in open(p, encoding="utf-8"):
        d = line.rstrip("\n").split("\t")
        if len(d) < 10:
            continue
        try:
            uit[norm(d[0])] = {
                "opta_naam": d[0], "apps": int(d[1]), "min": int(d[2]),
                "goals": int(d[3]), "xg": float(d[4]), "g_min_xg": float(d[5]),
                "shots": int(d[6]), "sot": int(d[7]),
                "conv": float(d[8].rstrip("%")), "xg_per_shot": float(d[9]),
            }
        except ValueError:
            continue
    return uit


def koppel(bs, spelers_opta):
    """Koppel Opta-spelers aan FPL-ids via token-overlap.

    De eerdere versie matchte op het laatste naamwoord en koppelde daardoor
    Joao Pedro (voluit: Joao Pedro Junqueira de JESUS) aan Igor JESUS. Braziliaanse
    en Spaanse namen breken elke achternaam-heuristiek. Daarom nu: vergelijk
    woordverzamelingen, eis een unieke beste match, en negeer naamdeeltjes.
    """
    teams = {t["id"]: t["short_name"] for t in bs["teams"]}
    STOP = {"de", "da", "dos", "das", "do", "van", "der", "den", "el", "al", "di", "le"}

    def toks(s):
        return {w for w in norm(s).replace(".", " ").split() if w and w not in STOP}

    opta_lijst = [(k, v, toks(v["opta_naam"])) for k, v in spelers_opta.items()]

    # Alle kandidaatparen scoren, daarna hoogste-eerst toewijzen. Wederzijds
    # exclusief: zo pakt Igor Jesus (FPL) zijn eigen Opta-regel voordat G.Jesus
    # hem kan claimen, want die combinatie scoort hoger.
    paren = []
    for e in bs["elements"]:
        web = toks(e["web_name"])
        vol = toks("%s %s" % (e["first_name"], e["second_name"]))
        if not (web or vol):
            continue
        for k, v, ot in opta_lijst:
            if not ot:
                continue
            gedeeld_web, gedeeld_vol = web & ot, vol & ot
            if not (gedeeld_web or gedeeld_vol):
                continue
            s = 3 * len(gedeeld_web) + len(gedeeld_vol)
            if web == ot:
                s += 20                      # getoonde naam exact gelijk
            if web and web <= ot:
                s += 6                       # getoonde naam zit volledig in de Opta-naam
            if vol and ot <= vol:
                s += 4                       # Opta-naam zit volledig in de volledige naam
            paren.append((s, e["id"], k, e["minutes"]))
    paren.sort(key=lambda x: (-x[0], -x[3]))

    uit, bezet = {}, set()
    for s, eid, k, _ in paren:
        if s < 4 or eid in uit or k in bezet:
            continue
        uit[eid] = spelers_opta[k]
        bezet.add(k)

    gemist = ["%s (%s)" % (e["web_name"], teams[e["team"]])
              for e in bs["elements"] if e["minutes"] > 900 and e["id"] not in uit]
    return uit, gemist


def _laad_goaliq():
    """Derde bron voor verwachte goals en clean sheets: Dixon-Coles op Understat.

    Vult het gat tussen de andere twee. Ons eigen model kent alleen vorig
    seizoen; de bookmakersodds bestaan alleen voor duels die al geprijsd zijn,
    in de praktijk de eerstvolgende speelronde. GoalIQ kijkt meerdere weken
    vooruit met actuele clubsterktes.
    """
    pad = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "goaliq.json")
    if not os.path.exists(pad):
        return {}
    try:
        return json.load(open(pad, encoding="utf-8")).get("per_club", {})
    except Exception:
        return {}


def _laad_odds():
    """Marktafgeleide doelpuntverwachtingen, als odds_verwerk.py gedraaid heeft."""
    pad = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "odds.json")
    if not os.path.exists(pad):
        return {}
    try:
        return json.load(open(pad)).get("per_club", {})
    except Exception:
        return {}


def fixture_projecties(bs, fixtures, teamdata, gws):
    """Verwachte goals en clean-sheetkans per club per gameweek."""
    teams = {t["id"]: t for t in bs["teams"]}
    sn = {t["id"]: t["short_name"] for t in bs["teams"]}
    xga_lijst = [v["xga"] for v in teamdata.values()]
    gem_xga = sum(xga_lijst) / len(xga_lijst) if xga_lijst else 50.0

    # FPL's strength_overall_home/_away is NIET bruikbaar: Arsenal staat er sterker
    # uit dan thuis. Daarom een expliciete, gedocumenteerde thuisvoordeel-factor.
    # Premier League-breed scoort de thuisploeg circa 10% meer dan de uitploeg.
    # Dit is de ENIGE constante in de berekening die niet uit onze datasets komt;
    # het dashboard vermeldt dat bij de bronvermelding van de ticker.
    THUIS, UIT = 1.10, 0.90

    GEWICHT_FX = {"bookmakers": 1.0, "GoalIQ": 1.0, "eigen model": 0.5}
    uit = {sn[t["id"]]: {} for t in bs["teams"]}
    odds = _laad_odds()
    goaliq = _laad_goaliq()

    for m in fixtures:
        g = m.get("event")
        if g not in gws:
            continue
        for tid, opp_id, thuis in ((m["team_h"], m["team_a"], True),
                                   (m["team_a"], m["team_h"], False)):
            a, b = teamdata.get(sn[tid]), teamdata.get(sn[opp_id])
            if not a or not b:
                continue
            hf = THUIS if thuis else UIT
            lam_voor = (a["xgf"] / 38.0) * (b["xga"] / gem_xga if gem_xga else 1.0) * hf
            lam_tegen = (b["xgf"] / 38.0) * (a["xga"] / gem_xga if gem_xga else 1.0) * (UIT if thuis else THUIS)
            cs = math.exp(-lam_tegen) * 100.0
            eigen_xg, eigen_cs = round(lam_voor, 2), round(cs)

            # DRIE BRONNEN, GEMIDDELD MET HERKOMST.
            # Vroeger overschreven de bookmakers dit duel volledig. Dat gooide
            # informatie weg: waar twee onafhankelijke modellen hetzelfde zeggen
            # is het cijfer steviger dan waar ze uiteenlopen, en dat verschil zag
            # je nergens meer terug.
            #
            # Gewichten. Bookmakers krijgen vol gewicht: daar zit echt geld
            # achter en zij verwerken blessures en opstellingsnieuws van vandaag.
            # GoalIQ ook vol: actuele clubsterktes, meerdere weken vooruit.
            # Ons eigen model half — dat kent alleen vorig seizoen.
            bxg = {"eigen model": round(lam_voor, 3)}
            bcs = {"eigen model": round(cs, 1)}
            bxga = {"eigen model": round(lam_tegen, 3)}
            for r in odds.get(sn[tid], []):
                anders = r["uit"] if r["thuisduel"] else r["thuis"]
                if anders == sn[opp_id] and r["thuisduel"] == thuis:
                    kant = "thuis" if thuis else "uit"
                    ander = "uit" if thuis else "thuis"
                    bxg["bookmakers"] = round(r["xg"][kant], 3)
                    bxga["bookmakers"] = round(r["xg"][ander], 3)
                    bcs["bookmakers"] = round(r["cs"][kant], 1)
                    break
            for r in goaliq.get(sn[tid], {}).get(str(g), []):
                if r.get("opp") == sn[opp_id] and bool(r.get("thuis")) == thuis:
                    bxg["GoalIQ"] = round(float(r["xg"]), 3)
                    bcs["GoalIQ"] = round(float(r["cs"]), 1)
                    break

            def _gem(d):
                w = sum(GEWICHT_FX.get(k, 1.0) for k in d)
                return sum(v * GEWICHT_FX.get(k, 1.0) for k, v in d.items()) / w if w else 0.0

            lam_voor, lam_tegen, cs = _gem(bxg), _gem(bxga), _gem(bcs)
            bron = "+".join(sorted(bxg)) if len(bxg) > 1 else "model"

            uit[sn[tid]].setdefault(g, []).append({
                "opp": sn[opp_id], "thuis": thuis,
                "xg": round(lam_voor, 2), "xga": round(lam_tegen, 2),
                "cs": round(cs), "bron": bron,
                "bronxg": bxg if len(bxg) > 1 else None,
                "broncs": bcs if len(bcs) > 1 else None,
                "eigen_xg": eigen_xg, "eigen_cs": eigen_cs,
                "fdr": m["team_h_difficulty"] if thuis else m["team_a_difficulty"],
                # Aftrap in UTC, precies zoals FPL hem geeft. De pagina rekent
                # hem om naar de tijd van de kijker; hier niets omrekenen,
                # want dan weet niemand meer welke zone er bedoeld is.
                "kick": m.get("kickoff_time"),
            })
    return uit


def bouw(gws=None):
    bs = get(BASE + "/bootstrap-static/")
    fixtures = get(BASE + "/fixtures/")
    if gws is None:
        gws = list(range(1, 39))
    teamdata = laad_teams()
    sp = laad_spelers()
    koppeling, gemist = koppel(bs, sp)
    proj = fixture_projecties(bs, fixtures, teamdata, gws)
    return {"teams": teamdata, "spelers": koppeling, "gemist": gemist,
            "fixture_proj": proj, "aantal_opta_spelers": len(sp)}


if __name__ == "__main__":
    d = bouw()
    print("Opta-clubs        : %d (%d met PL-data, %d omgerekend uit Championship)"
          % (len(d["teams"]),
             sum(1 for v in d["teams"].values() if v["bron"] == "opta-pl"),
             sum(1 for v in d["teams"].values() if v["bron"] == "championship")))
    print("Opta-spelers      : %d ingelezen, %d gekoppeld aan FPL"
          % (d["aantal_opta_spelers"], len(d["spelers"])))
    if d["gemist"]:
        print("Niet gekoppeld (>900 min): %d" % len(d["gemist"]))
        print("   " + ", ".join(d["gemist"][:14]))
    print("\nZwakste verdedigingen (hoogste xGA over 38 duels):")
    for sn, v in sorted(d["teams"].items(), key=lambda x: -x[1]["xga"])[:6]:
        print("   %-4s xGA %5.1f  xGF %5.1f  [%s]" % (sn, v["xga"], v["xgf"], v["bron"]))
