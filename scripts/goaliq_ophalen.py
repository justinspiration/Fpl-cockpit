#!/usr/bin/env python3
"""Haalt GoalIQ op: derde bron voor verwachte goals en clean sheet-kansen.

Waarom deze ernaast staat. Onze verwachte goals per duel kwamen uit twee
richtingen: een eigen Poisson-model op Opta's xGF/xGA, en de bookmakersodds van
OddsPortal. Het eerste weet niets van blessures of vorm, het tweede is scherp
maar bestaat alleen voor duels die al geprijsd zijn — in de praktijk de
eerstvolgende speelronde.

GoalIQ vult precies dat gat: een Dixon-Coles-model op Understat-data, met een
horizon van meerdere gameweeks vooruit. Drie onafhankelijke schattingen van
hetzelfde getal is aanzienlijk steviger dan twee.

Bron: https://api.goaliq.app/api/fantasy
"""
import json, os, ssl, time, urllib.request

HIER = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HIER, "state")
URL = "https://api.goaliq.app/api/fantasy"

# GoalIQ schrijft clubnamen voluit; FPL gebruikt afkortingen. De korte codes in
# hun eigen bestand (`home_short`) volgen FPL al, dus die gebruiken we — deze
# tabel vangt alleen de gevallen waar ze afwijken.
AFWIJKEND = {"NFO": "NFO", "MUN": "MUN", "MCI": "MCI", "TOT": "TOT"}


def haal(pogingen=3):
    req = urllib.request.Request(URL, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Accept": "application/json"})
    fout = None
    for poging in range(1, pogingen + 1):
        try:
            with urllib.request.urlopen(req, timeout=45,
                                        context=ssl.create_default_context()) as r:
                return json.load(r)
        except Exception as ex:
            fout = ex
            if poging < pogingen:
                time.sleep(3 * poging)
    raise SystemExit("[goaliq] MISLUKT na %d pogingen: %s" % (pogingen, fout))


def main():
    d = haal()
    meta = d.get("meta") or {}
    if not meta.get("available"):
        raise SystemExit("[goaliq] MISLUKT: bron meldt zelf available=false")
    seizoen = str(meta.get("season") or "")
    if seizoen and not seizoen.startswith("2026"):
        raise SystemExit("[goaliq] MISLUKT: bron levert seizoen %r, wij verwachten 2026/27"
                         % seizoen)

    duels = d.get("fixtures") or []
    if len(duels) < 20:
        raise SystemExit("[goaliq] MISLUKT: slechts %d duels, verwacht 20+" % len(duels))

    # Omzetten naar dezelfde vorm als odds.json: per club, per gameweek.
    per_club = {}
    for f in duels:
        gw = f.get("gameweek")
        h, a = f.get("home_short"), f.get("away_short")
        if not gw or not h or not a:
            continue
        for club, xg, cs, tegen, thuis in (
                (h, f.get("xg_home"), f.get("cs_home_pct"), a, True),
                (a, f.get("xg_away"), f.get("cs_away_pct"), h, False)):
            if xg is None or cs is None:
                continue
            per_club.setdefault(club, {}).setdefault(str(gw), []).append(
                {"opp": tegen, "thuis": thuis, "xg": round(float(xg), 3),
                 "cs": round(float(cs), 1), "fdr": f.get("fdr_home" if thuis else "fdr_away")})

    if len(per_club) < 18:
        raise SystemExit("[goaliq] MISLUKT: slechts %d clubs herkend" % len(per_club))

    gws = sorted({int(g) for v in per_club.values() for g in v})
    uit = {"bron": "GoalIQ (api.goaliq.app/api/fantasy)",
           "methode": meta.get("team_strength_source") or "Dixon-Coles op Understat",
           "opgehaald": time.strftime("%Y-%m-%d %H:%M"),
           "gegenereerd": meta.get("generated_at"),
           "gameweeks": gws, "per_club": per_club}
    os.makedirs(STATE, exist_ok=True)
    pad = os.path.join(STATE, "goaliq.json")
    json.dump(uit, open(pad, "w", encoding="utf-8"), ensure_ascii=False)
    print("[goaliq] %d clubs, GW%d-%d, %d duels opgeslagen"
          % (len(per_club), gws[0], gws[-1], len(duels)))


if __name__ == "__main__":
    main()
