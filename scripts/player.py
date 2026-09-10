#!/usr/bin/env python3
"""
Spelersdossier — verplichte diepteanalyse vóór elk advies over een speler.

Aanleiding (12-08-2026): ik noemde Semenyo een regressierisico op basis van
één seizoen goals-vs-xG. Dat was te dun. Meerjarige data liet zien dat hij
ELK seizoen boven zijn xG scoort — een vaardigheidspatroon, geen meevaller.
Dit script dwingt af dat alle assen worden bekeken vóór er iets geroepen wordt.

Wat dit script WEL kan (officiele FPL API, altijd live):
  - prijs, status, blessurenieuws, ownership, transfers deze GW
  - MEERJARIGE historie: punten, minuten, starts, goals, assists, xG, xA per seizoen
  - overperformance (G-xG) PER SEIZOEN -> patroon of uitschieter?
  - minuten-zekerheid: starts/appearances-ratio en minuten per start
  - komende fixtures met FDR + gemiddelde
  - SET PIECES: penalty-, corner- en vrijetrapvolgorde (structurele puntenbron)
  - punten per miljoen

Wat dit script NIET kan — handmatig aanvullen, staat in de output als checklist:
  - pre-season vorm (WebSearch: "<speler> pre-season <jaar>")
  - slotvorm vorig seizoen per gameweek (API wist dit bij de seizoensreset)
  - trainerswissel / rolwijziging / systeem
  - concurrentie in de selectie, transfergeruchten
  - Opta-detail: xG per shot, big chances, touches in de box
    -> theanalyst.com/competition/premier-league/stats

Gebruik:
  python3 player.py Semenyo
  python3 player.py "B.Fernandes" --gws 6
"""
import json, sys, argparse, urllib.request
from datetime import datetime, timezone

BASE = "https://fantasy.premierleague.com/api"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"


def get(u):
    return json.load(urllib.request.urlopen(
        urllib.request.Request(u, headers={"User-Agent": UA}), timeout=30))


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def dossier(naam, gws=5):
    bs = get(BASE + "/bootstrap-static/")
    teams = {t["id"]: t["short_name"] for t in bs["teams"]}
    pos = {p["id"]: p["singular_name_short"] for p in bs["element_types"]}
    hits = [e for e in bs["elements"] if naam.lower() in e["web_name"].lower()
            or naam.lower() in ("%s %s" % (e["first_name"], e["second_name"])).lower()]
    if not hits:
        print("Geen speler gevonden voor %r" % naam); return
    e = sorted(hits, key=lambda x: -x["total_points"])[0]
    s = get(BASE + "/element-summary/%d/" % e["id"])

    print("=" * 82)
    print("SPELERSDOSSIER  %s  (%s, %s)  £%.1fm" %
          (e["web_name"], teams[e["team"]], pos[e["element_type"]], e["now_cost"] / 10.0))
    print("=" * 82)

    st = e.get("status", "a")
    stat = {"a": "fit", "d": "twijfel", "i": "geblesseerd",
            "s": "geschorst", "u": "niet beschikbaar"}.get(st, st)
    print("\n1. BESCHIKBAARHEID")
    print("   status      : %s" % stat)
    print("   nieuws      : %s" % (e.get("news") or "geen"))
    if e.get("chance_of_playing_next_round") is not None:
        print("   speelkans   : %s%%" % e["chance_of_playing_next_round"])
    print("   eigendom    : %.1f%%   transfers in deze GW: %s" %
          (f(e["selected_by_percent"]), e.get("transfers_in_event", 0)))

    print("\n2. MEERJARIGE HISTORIE  <- kijk naar het PATROON, niet naar 1 seizoen")
    print("   %-9s %5s %6s %6s %4s %4s %7s %7s %8s %8s" %
          ("SEIZOEN", "PNT", "MIN", "STARTS", "G", "A", "xG", "xA", "G-xG", "PNT/90"))
    for h in s["history_past"]:
        g, xg = h["goals_scored"], f(h.get("expected_goals"))
        mins = h["minutes"]
        p90 = (h["total_points"] / mins * 90) if mins else 0
        print("   %-9s %5d %6d %6s %4d %4d %7.2f %7.2f %+8.2f %8.2f" %
              (h["season_name"], h["total_points"], mins, h.get("starts", "?"),
               g, h["assists"], xg, f(h.get("expected_assists")), g - xg, p90))
    if len(s["history_past"]) >= 2:
        d = [h["goals_scored"] - f(h.get("expected_goals")) for h in s["history_past"]
             if h["minutes"] > 600]
        if d:
            pos_n = sum(1 for x in d if x > 0)
            print("   -> boven xG in %d van %d seizoenen met >600 min." % (pos_n, len(d)))
            if pos_n == len(d) and len(d) >= 3:
                print("      CONSISTENT patroon: dit oogt als finishing-vaardigheid, GEEN toeval.")
            elif pos_n <= 1:
                print("      Structureel ONDER xG: mogelijk zwakke afronding of koopkans.")
            else:
                print("      Wisselend beeld: kijk naar rol, systeem en kansenkwaliteit.")

    print("\n3. MINUTEN-ZEKERHEID")
    for h in s["history_past"][-2:]:
        starts, mins = h.get("starts") or 0, h["minutes"]
        mps = (mins / starts) if starts else 0
        print("   %-9s %s starts, %d min  (%.0f min per start)" %
              (h["season_name"], starts, mins, mps))
    laatste = s["history_past"][-1] if s["history_past"] else None
    if laatste and (laatste.get("starts") or 0) >= 30:
        print("   -> IJZEREN basisspeler vorig seizoen. Laag rotatierisico.")
    elif laatste and (laatste.get("starts") or 0) >= 20:
        print("   -> Meestal basis, maar met rotatie. Bewaken.")
    elif laatste:
        print("   -> LET OP: geen vaste basisplaats vorig seizoen.")

    print("\n4. KOMENDE %d FIXTURES" % gws)
    tot, n = 0, 0
    for fx in s["fixtures"][:gws]:
        opp = teams[fx["team_a"]] if fx["is_home"] else teams[fx["team_h"]]
        print("   GW%-3s %-4s %-3s  FDR %d" %
              (fx.get("event"), opp, "(T)" if fx["is_home"] else "(U)", fx["difficulty"]))
        tot += fx["difficulty"]; n += 1
    if n:
        gem = tot / n
        oordeel = ("gunstig" if gem <= 2.6 else
                   "neutraal" if gem <= 3.2 else "zwaar")
        print("   -> gemiddelde FDR %.2f (%s)" % (gem, oordeel))

    print("\n5. SET PIECES  <- structurele puntenbron, weegt zwaar")
    po, co, do = (e.get("penalties_order"), e.get("corners_and_indirect_freekicks_order"),
                  e.get("direct_freekicks_order"))
    print("   penalty's        : %s" % ("nemer #%s" % po if po else "geen"))
    print("   corners/ind. vrij: %s" % ("nemer #%s" % co if co else "geen"))
    print("   directe vrije trap: %s" % ("nemer #%s" % do if do else "geen"))
    if po == 1:
        print("   -> EERSTE PENALTYNEMER. Structureel hogere xG; dit verhoogt zijn waarde fors.")
    elif po == 2:
        print("   -> Tweede penaltynemer: neemt over bij afwezigheid van de eerste.")
    if co == 1:
        print("   -> Eerste cornernemer: extra assist-potentie.")
    for k in ("penalties_text", "corners_and_indirect_freekicks_text", "direct_freekicks_text"):
        if e.get(k):
            print("   toelichting: %s" % e[k])
            break

    print("\n6. WAARDE")
    print("   punten 25/26 : %d  |  per miljoen: %.1f  |  PPG: %s  |  vorm: %s" %
          (e["total_points"], e["total_points"] / (e["now_cost"] / 10.0),
           e["points_per_game"], e["form"]))

    print("\n7. HANDMATIG AANVULLEN — dit script kan het niet, doe het WEL:")
    print("   [ ] pre-season vorm      -> WebSearch \"%s pre-season 2026\"" % e["web_name"])
    print("   [ ] slotvorm vorig seizoen (API wist per-GW data bij de reset)")
    print("   [ ] trainer / systeem / rolwijziging bij %s" % teams[e["team"]])
    print("   [ ] concurrentie + transfergeruchten")
    print("   [ ] Opta-detail -> theanalyst.com/competition/premier-league/stats")
    print("   [ ] team news   -> fantasyfootballhub.co.uk/premier-league-predicted-lineups")
    print("\nBron: officiele FPL API /bootstrap-static/ + /element-summary/%d/ | %s"
          % (e["id"], datetime.now(timezone.utc).isoformat(timespec="seconds")))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("naam"); ap.add_argument("--gws", type=int, default=5)
    a = ap.parse_args()
    dossier(a.naam, a.gws)
