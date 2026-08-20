#!/usr/bin/env python3
"""Zet state/copilot_ruw.json om naar state/xp_copilot.json, gekoppeld op FPL-id.

Draait na copilot_ophalen.js. Koppelt op genormaliseerde web_name plus positie.
Waar dat niet uniek is, gebruikt hij eerst het clubfilter dat het ophaalscript
heeft meegegeven, en daarna uitsluiting: als van twee gelijknamige spelers op
dezelfde positie er een is vastgepind, is de ander de overgebleven club.

Breekt af bij een onvolledige koppeling in plaats van stil door te gaan met
half werk, want een verkeerd gekoppelde speler is erger dan geen speler.
"""
import collections
import json
import os
import re
import sys
import unicodedata
import urllib.request

HIER = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HIER, "state")
API = "https://fantasy.premierleague.com/api/bootstrap-static/"


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s)


def main():
    ruw_pad = os.path.join(STATE, "copilot_ruw.json")
    if not os.path.exists(ruw_pad):
        sys.exit("state/copilot_ruw.json ontbreekt — draai eerst: node copilot_ophalen.js")
    ruw = json.load(open(ruw_pad))

    req = urllib.request.Request(API, headers={"User-Agent": "fpl-cockpit"})
    bs = json.load(urllib.request.urlopen(req, timeout=30))
    tm = {t["id"]: t["short_name"] for t in bs["teams"]}
    POS = {x["id"]: x["singular_name_short"] for x in bs["element_types"]}

    fpl = collections.defaultdict(list)
    for e in bs["elements"]:
        fpl[(norm(e["web_name"]), POS[e["element_type"]])].append(e)

    rijen = []
    for pos, lijst in ruw["posities"].items():
        for r in lijst:
            rijen.append({"n": r["n"], "pos": pos, "gw": r["gw"], "mn": r["mn"]})

    club_van = ruw.get("clubVan", {})
    uit, gebruikt, open_rijen = {}, set(), []

    def plaats(r, e):
        gebruikt.add(e["id"])
        uit[str(e["id"])] = {"n": e["web_name"], "t": tm[e["team"]], "p": r["pos"],
                             "gw": r["gw"], "mn": r["mn"]}

    # ronde 1: naam + positie uniek, eventueel met het clubfilter erbij
    for r in rijen:
        k = (norm(r["n"]), r["pos"])
        kand = [e for e in fpl.get(k, []) if e["id"] not in gebruikt]
        if len(kand) > 1:
            gw1 = r["gw"][0]
            club = (club_van.get("%s|%s" % (r["n"], gw1))
                    or club_van.get("%s|%g" % (r["n"], gw1)))
            if club:
                kand = [e for e in kand if tm[e["team"]] == club] or kand
        if len(kand) == 1:
            plaats(r, kand[0])
        else:
            open_rijen.append(r)

    # ronde 2: uitsluiting — wat na ronde 1 overblijft is eenduidig zodra
    # alle andere gelijknamigen van dezelfde positie al vastliggen
    for ronde in range(4):
        nog = []
        for r in open_rijen:
            k = (norm(r["n"]), r["pos"])
            kand = [e for e in fpl.get(k, []) if e["id"] not in gebruikt]
            if len(kand) == 1:
                plaats(r, kand[0])
            else:
                nog.append(r)
        if len(nog) == len(open_rijen):
            break
        open_rijen = nog

    # Twee heel verschillende gevallen, en die moeten niet hetzelfde aflopen.
    #
    #   AMBIGU  — meerdere FPL-spelers passen op dezelfde naam en positie. Dan
    #             kan ik de verkeerde kiezen en zou het cijfer van speler A bij
    #             speler B belanden. Daar breken we op af; liever geen update
    #             dan een stille verwisseling.
    #
    #   ONBEKEND — nul kandidaten: Copilot noemt iemand die niet in de FPL-data
    #             staat. Dat is geen risico, alleen een speler die we overslaan.
    #             Hierop afbreken betekende dat de hele verversing stopte en de
    #             cijfers dagenlang oud bleven — precies wat je niet wilt.
    ambigu, onbekend = [], []
    for r in open_rijen:
        k = (norm(r["n"]), r["pos"])
        kand = [e for e in fpl.get(k, []) if e["id"] not in gebruikt]
        (ambigu if kand else onbekend).append((r, kand))
    for r, kand in onbekend:
        print("  overgeslagen (niet in FPL): %s (%s)" % (r["n"], r["pos"]), file=sys.stderr)
    for r, kand in ambigu:
        print("  ambigu: %s (%s) — kandidaten: %s"
              % (r["n"], r["pos"], [tm[e["team"]] for e in kand]), file=sys.stderr)
    if ambigu:
        sys.exit("AFGEBROKEN: %d rijen konden aan meerdere spelers horen. "
                 "Liever geen update dan een verwisseling." % len(ambigu))
    overgeslagen = [{"n": r["n"], "pos": r["pos"]} for r, _ in onbekend]

    zonder = [e for e in bs["elements"] if e["id"] not in gebruikt]
    data = {
        "_bron": ruw["_bron"], "_opgehaald": ruw["_opgehaald"],
        "_copilot_bijgewerkt": ruw.get("_copilot_bijgewerkt", ""),
        "_start_gw": ruw.get("_start_gw", 1), "_horizon": ruw.get("_horizon", 8),
        "_overgeslagen": overgeslagen,
        "_methode": ruw.get("_methode", "") + " Koppeling aan FPL-id door copilot_verwerk.py: "
                    "naam plus positie, daarna clubfilter, daarna uitsluiting.",
        "_telling": ruw.get("_telling", {}),
        "_dekking": "%d van %d FPL-spelers" % (len(uit), len(bs["elements"])),
        "_zonder_copilot": [{"n": e["web_name"], "t": tm[e["team"]],
                             "p": POS[e["element_type"]], "prijs": e["now_cost"] / 10}
                            for e in zonder],
        "spelers": uit,
    }
    with open(os.path.join(STATE, "xp_copilot.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("xp_copilot.json: %d van %d spelers gekoppeld" % (len(uit), len(bs["elements"])))
    if zonder:
        print("  niet bij Copilot: %s" % ", ".join("%s (%s)" % (e["web_name"], tm[e["team"]])
                                                   for e in zonder))


if __name__ == "__main__":
    main()
