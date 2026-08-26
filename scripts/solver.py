#!/usr/bin/env python3
"""
Multi-gameweek transfer-solver.

WAT DIT OPLOST: een advies per gameweek is bijziend. Soms is de beste zet deze week
géén transfer doen, zodat je er volgende week twee gratis hebt. Deze solver kijkt
meerdere gameweeks vooruit en zoekt het pad met de hoogste totale opbrengst.

REGELS DIE HIJ RESPECTEERT (bron: fantasy.premierleague.com/help/rules)
  - 1 gratis transfer per gameweek, maximaal 5 opsparen
  - elke extra transfer kost 4 punten
  - maximaal 3 spelers per club, 2/5/5/3 per positie, budget 100.0
  - opstelling: 1 keeper, minimaal 3 verdedigers, minimaal 1 aanvaller
  - alleen de basiself scoort; de aanvoerder telt dubbel

METHODE: beam search. Per gameweek worden de kansrijkste zetten uitgeprobeerd
(niets doen, 1 transfer, 2 transfers met hit) en alleen de beste paden blijven over.
Geen willekeur, geen schatting - alles rekent met de xP die al in het dashboard staat.

Gebruik:
  python3 solver.py --gws 6 --beam 60
"""
import json, os, sys, argparse
from itertools import islice

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
sys.path.insert(0, HERE)

XI_MIN = {"GKP": 1, "DEF": 3, "MID": 2, "FWD": 1}
XI_MAX = {"GKP": 1, "DEF": 5, "MID": 5, "FWD": 3}


def inzet(p, gw, nu=None):
    """Wat hij oplevert als je hem OPSTELT, niet zijn ruwe projectie.

    Voor de eerstvolgende gameweek telt beschikbaarheid mee: wie geblesseerd
    of geschorst is scoort nul, hoe hoopvol een externe bron ook is. Verderop
    in het venster laten we die bron met rust, want dan kan hij terug zijn.
    """
    v = p["gw"].get(gw, 0)
    if nu is None or gw != nu:
        return v
    if p.get("status") in ("i", "s", "u", "n"):
        return 0.0
    if p.get("status") == "d":
        k = p.get("speelkans")
        k = 50 if k is None else k
        return v * max(0.0, min(1.0, k / 100.0))
    return v


def beste_xi(squad, gw, nu=None):
    """Hoogste elftal binnen de formatieregels; aanvoerder telt dubbel."""
    per = {}
    for p in squad:
        per.setdefault(p["p"], []).append(p)
    for k in per:
        per[k].sort(key=lambda x: -inzet(x, gw, nu))
    beste, xi_beste = -1, None
    for d in range(XI_MIN["DEF"], XI_MAX["DEF"] + 1):
        for m in range(XI_MIN["MID"], XI_MAX["MID"] + 1):
            for f in range(XI_MIN["FWD"], XI_MAX["FWD"] + 1):
                if 1 + d + m + f != 11:
                    continue
                if (len(per.get("GKP", [])) < 1 or len(per.get("DEF", [])) < d
                        or len(per.get("MID", [])) < m or len(per.get("FWD", [])) < f):
                    continue
                xi = (per["GKP"][:1] + per["DEF"][:d] + per["MID"][:m] + per["FWD"][:f])
                s = sum(inzet(x, gw, nu) for x in xi)
                s += max(inzet(x, gw, nu) for x in xi)        # aanvoerder dubbel
                if s > beste:
                    beste, xi_beste = s, xi
    return beste, xi_beste


def legaal(squad, budget_over):
    if budget_over < -1e-9:
        return False
    cnt, clubs = {}, {}
    for p in squad:
        cnt[p["p"]] = cnt.get(p["p"], 0) + 1
        clubs[p["t"]] = clubs.get(p["t"], 0) + 1
        if clubs[p["t"]] > 3:
            return False
    return cnt == {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}


def kandidaten(db, squad, uit, budget, horizon, gws, top=14):
    """Realistische vervangers voor één speler, gerangschikt op winst over de horizon."""
    have = {p["id"] for p in squad}
    clubs = {}
    for p in squad:
        if p["id"] != uit["id"]:
            clubs[p["t"]] = clubs.get(p["t"], 0) + 1
    ruimte = budget + uit["c"]
    uit_som = sum(uit["gw"].get(g, 0) for g in gws[:horizon])
    opties = []
    for c in db:
        if (c["p"] != uit["p"] or c["id"] in have or c["c"] > ruimte + 1e-9
                or c["status"] in ("i", "s", "u")):
            continue
        if c["st"] < 15 and c["min"] < 900 and not c.get("p90geschat"):
            continue
        if clubs.get(c["t"], 0) >= 3:
            continue
        winst = sum(c["gw"].get(g, 0) for g in gws[:horizon]) - uit_som
        # Zelfde drempel als de JS-solver en het transferadvies: onder de 2 punten
        # over het venster is een transfer geschuif zonder opbrengst.
        if winst > 2.0:
            opties.append((winst, c))
    opties.sort(key=lambda x: -x[0])
    return [c for _, c in opties[:top]]


def solve(db, squad0, budget0, gws, beam=60, max_hits=1):
    """Beam search over de gameweeks. Geeft de beste transferpaden terug."""
    start = {"squad": squad0, "budget": budget0, "vrij": 1,
             "punten": 0.0, "pad": [], "kosten": 0}
    paden = [start]
    for i, g in enumerate(gws):
        nieuw = []
        for st in paden:
            # optie 1: niets doen (spaart een transfer op, maximaal 5)
            sc, _ = beste_xi(st["squad"], g)
            nieuw.append({**st, "punten": st["punten"] + sc,
                          "vrij": min(5, st["vrij"] + 1),
                          "pad": st["pad"] + [{"gw": g, "acties": [], "hit": 0, "sc": round(sc, 2)}]})
            # optie 2 en 3: één of twee transfers
            for aantal in (1, 2):
                if aantal == 2 and max_hits < 1:
                    continue
                hit = max(0, aantal - st["vrij"]) * 4
                if hit > max_hits * 4:
                    continue
                # kies de zwakste spelers als kandidaat om te wisselen
                zwak = sorted(st["squad"], key=lambda p: sum(p["gw"].get(x, 0) for x in gws[i:i + 3]))[:6]
                for uit in zwak[:4 if aantal == 1 else 3]:
                    for inn in kandidaten(db, st["squad"], uit, st["budget"], min(4, len(gws) - i), gws[i:], top=6):
                        sq = [x for x in st["squad"] if x["id"] != uit["id"]] + [inn]
                        bud = st["budget"] + uit["c"] - inn["c"]
                        if not legaal(sq, bud):
                            continue
                        if aantal == 1:
                            sc2, _ = beste_xi(sq, g)
                            nieuw.append({"squad": sq, "budget": bud,
                                          "vrij": min(5, max(0, st["vrij"] - 1) + 1),
                                          "punten": st["punten"] + sc2 - hit,
                                          "kosten": st["kosten"] + hit,
                                          "pad": st["pad"] + [{"gw": g, "sc": round(sc2, 2),
                                              "acties": [(uit["n"], inn["n"])], "hit": hit}]})
                        else:
                            # tweede transfer bovenop de eerste
                            zwak2 = sorted(sq, key=lambda p: sum(p["gw"].get(x, 0) for x in gws[i:i + 3]))[:3]
                            for uit2 in zwak2:
                                if uit2["id"] == inn["id"]:
                                    continue
                                for inn2 in kandidaten(db, sq, uit2, bud, min(4, len(gws) - i), gws[i:], top=3):
                                    sq2 = [x for x in sq if x["id"] != uit2["id"]] + [inn2]
                                    bud2 = bud + uit2["c"] - inn2["c"]
                                    if not legaal(sq2, bud2):
                                        continue
                                    sc3, _ = beste_xi(sq2, g)
                                    nieuw.append({"squad": sq2, "budget": bud2,
                                                  "vrij": min(5, max(0, st["vrij"] - 2) + 1),
                                                  "punten": st["punten"] + sc3 - hit,
                                                  "kosten": st["kosten"] + hit,
                                                  "pad": st["pad"] + [{"gw": g, "sc": round(sc3, 2),
                                                      "acties": [(uit["n"], inn["n"]), (uit2["n"], inn2["n"])],
                                                      "hit": hit}]})
        nieuw.sort(key=lambda x: -x["punten"])
        # ontdubbel op squad-samenstelling zodat de beam divers blijft
        gezien, gefilterd = set(), []
        for st in nieuw:
            k = tuple(sorted(p["id"] for p in st["squad"]))
            if k in gezien:
                continue
            gezien.add(k)
            gefilterd.append(st)
            if len(gefilterd) >= beam:
                break
        paden = gefilterd
    return paden


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gws", type=int, default=6)
    ap.add_argument("--beam", type=int, default=60)
    ap.add_argument("--hits", type=int, default=1)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    import dashboard
    d = dashboard.build()
    db = d["db"]
    gws = d["alle_gws"][:a.gws]
    squad = [next(x for x in db if x["id"] == p["id"]) for p in d["squad"]]
    budget = (d["entry"] or {}).get("bank", 0.0)

    paden = solve(db, squad, budget, gws, beam=a.beam, max_hits=a.hits)
    beste = paden[0]
    # referentie: nooit iets doen
    niets = solve(db, squad, budget, gws, beam=1, max_hits=0)
    basis = max((p["punten"] for p in niets if all(not s["acties"] for s in p["pad"])), default=None)
    if basis is None:
        st = {"squad": squad, "punten": 0.0}
        basis = sum(beste_xi(squad, g)[0] for g in gws)

    if a.json:
        print(json.dumps({"gws": gws, "basis": round(basis, 1),
                          "beste": round(beste["punten"], 1),
                          "winst": round(beste["punten"] - basis, 1),
                          "hits": beste["kosten"],
                          "pad": beste["pad"],
                          "alternatieven": [{"punten": round(p["punten"], 1),
                                             "pad": p["pad"]} for p in paden[1:4]]},
                         ensure_ascii=False))
        return

    print("MULTI-GAMEWEEK SOLVER  GW%d t/m GW%d" % (gws[0], gws[-1]))
    print("=" * 62)
    print("Niets doen          : %.1f punten" % basis)
    print("Beste transferpad   : %.1f punten  (%+.1f)" % (beste["punten"], beste["punten"] - basis))
    print("Strafpunten betaald : %d" % beste["kosten"])
    print("\nPAD:")
    for st in beste["pad"]:
        if not st["acties"]:
            print("  GW%-3d bewaar je transfer" % st["gw"])
        else:
            for u, i in st["acties"]:
                print("  GW%-3d %-18s -> %-18s%s" % (st["gw"], u, i, "  (-4)" if st["hit"] else ""))
    print("\nALTERNATIEVEN:")
    for p in paden[1:4]:
        zetten = [("GW%d %s→%s" % (s["gw"], a2, b2)) for s in p["pad"] for a2, b2 in s["acties"]]
        print("  %.1f ptn : %s" % (p["punten"], ", ".join(zetten) or "niets doen"))
    print("\nBron: xP per speler per gameweek uit het dashboard (FPL Copilot waar beschikbaar).")
    print("Transferregels: fantasy.premierleague.com/help/rules")


if __name__ == "__main__":
    main()
