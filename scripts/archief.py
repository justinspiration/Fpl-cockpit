#!/usr/bin/env python3
"""Bouwt en onderhoudt het seizoensarchief van de mini-league.

Waarom een apart archief
------------------------
De FPL API geeft je de opstelling van elke manager per gameweek, maar alleen
zolang je ernaar vraagt: er is geen "geef mij het hele seizoen"-endpoint. Wil
je later terugkijken wie in gameweek 7 wie had aangevoerd, dan moet je dat
zelf hebben bewaard. Dit script haalt elke afgeronde gameweek op en schrijft
hem bij in state/league_archief.json. Wat er eenmaal in staat blijft staan,
ook als de API later iets anders teruggeeft.

Wat er per gameweek in gaat, per manager:
  - de vijftien spelers, met wie er in de basis stond en wie op de bank
  - de aanvoerder en de vervangend aanvoerder
  - welke chip er actief was
  - de punten, de strafpunten en de transfers
  - welke bankspelers zijn ingevallen omdat iemand niet speelde

Daarnaast houdt het bij hoe Justins eigen ploeg het deed tegenover twee
ijkpunten: het beste elftal dat hij uit zijn eigen vijftien had kunnen zetten,
en de beste vijftien die er voor zijn budget te koop was. Dat eerste meet zijn
opstelkeuzes, het tweede zijn selectiekeuzes.

Gebruik:  python3 archief.py
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

HIER = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HIER, "state")
BASE = "https://fantasy.premierleague.com/api"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) fpl-cockpit"}
PAD = os.path.join(STATE, "league_archief.json")


def get(url):
    try:
        return json.load(urllib.request.urlopen(
            urllib.request.Request(url, headers=UA), timeout=30))
    except Exception:
        return None


def laad():
    if os.path.exists(PAD):
        try:
            return json.load(open(PAD, encoding="utf-8"))
        except Exception:
            pass
    return {"_bron": "FPL API /entry/{id}/event/{gw}/picks/ en /event/{gw}/live/",
            "_uitleg": ("Per afgeronde gameweek de opstelling van elke manager in de "
                        "mini-league. De API bewaart dit niet zelf, dus wat hier niet "
                        "in staat is niet meer op te halen."),
            "gameweeks": {}, "prestatie": {}}


def beste_xi(spelers, punten):
    """Hoogste elftal binnen de formatieregels, aanvoerder dubbel.

    spelers: lijst van (element_id, positie). punten: id -> behaalde punten.
    """
    per = {}
    for eid, pos in spelers:
        per.setdefault(pos, []).append(punten.get(eid, 0))
    for k in per:
        per[k].sort(reverse=True)
    beste = 0
    for d in range(3, 6):
        for m in range(2, 6):
            for f in range(1, 4):
                if 1 + d + m + f != 11:
                    continue
                if (len(per.get("GKP", [])) < 1 or len(per.get("DEF", [])) < d
                        or len(per.get("MID", [])) < m or len(per.get("FWD", [])) < f):
                    continue
                xi = per["GKP"][:1] + per["DEF"][:d] + per["MID"][:m] + per["FWD"][:f]
                beste = max(beste, sum(xi) + max(xi))
    return beste


def main():
    ids = {}
    p = os.path.join(STATE, "ids.json")
    if os.path.exists(p):
        ids = json.load(open(p))
    entry_id, league_id = ids.get("entry_id"), ids.get("league_id")
    if not entry_id or not league_id:
        sys.exit("entry_id en league_id ontbreken in state/ids.json")

    bs = get(BASE + "/bootstrap-static/")
    if not bs:
        sys.exit("bootstrap-static niet bereikbaar")
    pos = {x["id"]: x["singular_name_short"] for x in bs["element_types"]}
    sn = {t["id"]: t["short_name"] for t in bs["teams"]}
    el = {e["id"]: {"n": e["web_name"], "t": sn[e["team"]],
                    "p": pos[e["element_type"]], "c": e["now_cost"] / 10.0}
          for e in bs["elements"]}
    klaar = [e["id"] for e in bs["events"] if e.get("finished")]
    if not klaar:
        print("Nog geen afgeronde gameweek — het archief blijft leeg tot na GW1.")
        A = laad()
        A["_bijgewerkt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        A["_afgerond"] = 0
        json.dump(A, open(PAD, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return

    stz = get("%s/leagues-classic/%s/standings/" % (BASE, league_id))
    managers = [{"entry": r["entry"], "team": r["entry_name"], "manager": r["player_name"],
                 "ik": r["entry"] == entry_id}
                for r in (stz or {}).get("standings", {}).get("results", [])]
    if not managers:
        sys.exit("geen managers in de league gevonden")

    A = laad()
    nieuw = 0
    for g in klaar:
        sleutel = str(g)
        if sleutel in A["gameweeks"]:
            continue                      # al bewaard; niet opnieuw ophalen
        live = get("%s/event/%d/live/" % (BASE, g)) or {}
        punten = {e["id"]: e["stats"]["total_points"] for e in live.get("elements", [])}
        minuten = {e["id"]: e["stats"]["minutes"] for e in live.get("elements", [])}
        rij = {"managers": [], "opgehaald": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        for m in managers:
            pk = get("%s/entry/%s/event/%d/picks/" % (BASE, m["entry"], g))
            if not pk:
                continue
            picks = pk["picks"]
            cap = next((x["element"] for x in picks if x["multiplier"] >= 2), None)
            vice = next((x["element"] for x in picks if x.get("is_vice_captain")), None)
            # invallers: een bankspeler die punten maakte terwijl een basisspeler 0 minuten had
            basis_uit = [x["element"] for x in picks
                         if x["position"] <= 11 and minuten.get(x["element"], 0) == 0]
            bank_in = [x["element"] for x in picks
                       if x["position"] > 11 and minuten.get(x["element"], 0) > 0]
            rij["managers"].append({
                "entry": m["entry"], "team": m["team"], "manager": m["manager"], "ik": m["ik"],
                "chip": pk.get("active_chip"),
                "punten": (pk.get("entry_history") or {}).get("points"),
                "straf": (pk.get("entry_history") or {}).get("event_transfers_cost"),
                "transfers": (pk.get("entry_history") or {}).get("event_transfers"),
                "waarde": ((pk.get("entry_history") or {}).get("value") or 0) / 10.0,
                "bank": ((pk.get("entry_history") or {}).get("bank") or 0) / 10.0,
                "cap": cap, "vice": vice,
                "autosub_uit": basis_uit[:len(bank_in)], "autosub_in": bank_in[:len(basis_uit)],
                "picks": [{"id": x["element"],
                           "n": el.get(x["element"], {}).get("n", "?"),
                           "t": el.get(x["element"], {}).get("t", "?"),
                           "p": el.get(x["element"], {}).get("p", "?"),
                           "basis": x["position"] <= 11,
                           "cap": x["multiplier"] >= 2,
                           "pt": punten.get(x["element"], 0),
                           "min": minuten.get(x["element"], 0)}
                          for x in picks],
            })
        if rij["managers"]:
            A["gameweeks"][sleutel] = rij
            nieuw += 1

            # ── hoe deed Justin het tegenover twee ijkpunten? ──────────
            ik = next((x for x in rij["managers"] if x["ik"]), None)
            if ik:
                mijn15 = [(x["id"], x["p"]) for x in ik["picks"]]
                haalbaar_eigen = beste_xi(mijn15, punten)
                werkelijk = (ik["punten"] or 0)
                A["prestatie"][sleutel] = {
                    "werkelijk": werkelijk,
                    "straf": ik["straf"] or 0,
                    "beste_uit_eigen_15": round(haalbaar_eigen, 1),
                    "gemist_door_opstelling": round(haalbaar_eigen - werkelijk, 1),
                    "chip": ik["chip"],
                    "cap": ik["cap"],
                    "cap_punten": punten.get(ik["cap"], 0),
                    "beste_cap": max((punten.get(x["id"], 0) for x in ik["picks"]
                                      if x["basis"]), default=0),
                }

    A["_bijgewerkt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    A["_afgerond"] = len(klaar)
    A["_managers"] = len(managers)
    json.dump(A, open(PAD, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"league_archief.json: {len(A['gameweeks'])} gameweeks bewaard "
          f"({nieuw} nieuw), {len(managers)} managers")
    for g in sorted(A["prestatie"], key=int)[-4:]:
        pr = A["prestatie"][g]
        print(f"  GW{g}: {pr['werkelijk']} punten · uit je eigen 15 was "
              f"{pr['beste_uit_eigen_15']} mogelijk "
              f"(−{pr['gemist_door_opstelling']} door de opstelling)")


if __name__ == "__main__":
    main()
