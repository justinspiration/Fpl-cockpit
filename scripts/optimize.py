#!/usr/bin/env python3
"""
Expected-points model + squad-optimizer voor FPL 26/27.

TRANSPARANTIE OVER DE METHODE (Justin eist bronvermelding):
Er bestaat op dit moment geen betrouwbare publieke player-level expected-points
bron voor 26/27 (zie skill sectie 3). Dit model is dus MIJN EIGEN berekening,
opgebouwd uit officiele FPL API-data. Elke stap staat hieronder expliciet.

  projectie_per_gw = basis x fixture x beschikbaarheid x wk_factor

  basis          = points_per_game 25/26, gekrompen richting een positie-baseline
                   naarmate een speler minder minuten maakte (weinig minuten =
                   onbetrouwbare PPG). reliability = min(1, minuten/1500).
  fixture        = FDR-multiplier per gameweek, uit /fixtures/ van de FPL API.
                   FDR2 -> 1.18 | FDR3 -> 1.00 | FDR4 -> 0.85 | FDR5 -> 0.72
  beschikbaarheid= status 'a' -> 1.0 | 'd' -> chance_of_playing/100 | i/s/u -> 0
  wk_factor      = 0.85 in GW1-3 voor spelers met zware WK-belasting 2026
                   (bron: Fantasy Football Fix / SI, zie skill).

BEKENDE BEPERKINGEN - altijd melden:
  - PPG komt van vorig seizoen, deels bij een ANDERE club. Transfers (Semenyo
    BOU->MCI, Isak ->LIV) zijn dus onzeker: ander team, andere rol.
  - Promovendi en nieuwe spelers hebben geen PL-historie -> krijgen baseline.
  - Geen enkel model kent de opstellingen. Persconferenties gaan voor.

Gebruik:
  python3 optimize.py model --gws 5 [--pos MID] [--n 30]
  python3 optimize.py squad --gws 5 [--iters 400]
  python3 optimize.py rate  --gws 5 --team <bestand.json>
"""
import json, sys, random, argparse, urllib.request
from datetime import datetime, timezone

BASE = "https://fantasy.premierleague.com/api"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

FDR_MULT = {1: 1.30, 2: 1.18, 3: 1.00, 4: 0.85, 5: 0.72}
POS_BASE = {"GKP": 3.0, "DEF": 2.6, "MID": 2.4, "FWD": 2.6}
SQUAD_REQ = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
XI_MIN = {"GKP": 1, "DEF": 3, "MID": 2, "FWD": 1}
XI_MAX = {"GKP": 1, "DEF": 5, "MID": 5, "FWD": 3}

# Zware WK-2026 belasting (halve finale of verder / 450+ min).
# Bron: Fantasy Football Fix "World Cup Players to Avoid" + SI club-ranking.
WK_ZWAAR = {"Mac Allister", "Konsa", "Rodri", "Anderson", "L.Martínez", "Martinez",
            "Guéhi", "Enzo", "O'Reilly", "Romero", "Saliba", "Rice", "Madueke",
            "Saka", "Gabriel", "Eze", "Raya"}


def get(url):
    return json.load(urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30))


def load(gws):
    bs = get(BASE + "/bootstrap-static/")
    fx = get(BASE + "/fixtures/")
    teams = {t["id"]: t["short_name"] for t in bs["teams"]}
    pos = {p["id"]: p["singular_name_short"] for p in bs["element_types"]}
    nxt = next((e for e in bs["events"] if e.get("is_next")), None)
    start = nxt["id"] if nxt else 1
    win = list(range(start, start + gws))

    fdr = {t["id"]: {g: [] for g in win} for t in bs["teams"]}
    for m in fx:
        g = m.get("event")
        if g in win:
            fdr[m["team_h"]][g].append(m["team_h_difficulty"])
            fdr[m["team_a"]][g].append(m["team_a_difficulty"])

    players = []
    for e in bs["elements"]:
        P = pos[e["element_type"]]
        mins = e["minutes"]
        ppg = float(e["points_per_game"] or 0)
        rel = min(1.0, mins / 1500.0)
        basis = rel * ppg + (1 - rel) * POS_BASE[P]

        st = e.get("status", "a")
        if st in ("i", "s", "u"):
            avail = 0.0
        elif st == "d":
            avail = float(e.get("chance_of_playing_next_round") or 50) / 100.0
        else:
            avail = 1.0

        wk = e["web_name"] in WK_ZWAAR
        per_gw, tot = [], 0.0
        for i, g in enumerate(win):
            ms = fdr[e["team"]][g]
            if not ms:
                per_gw.append(0.0)
                continue
            fmul = sum(FDR_MULT.get(d, 1.0) for d in ms)  # DGW telt dubbel
            w = 0.85 if (wk and i < 3) else 1.0
            v = basis * fmul * avail * w
            per_gw.append(v)
            tot += v

        players.append({
            "id": e["id"], "n": e["web_name"], "t": teams[e["team"]], "tid": e["team"],
            "p": P, "cost": e["now_cost"], "proj": tot, "gw": per_gw,
            "ppg": ppg, "mins": mins, "own": float(e["selected_by_percent"]),
            "tp": e["total_points"], "st": st, "avail": avail, "wk": wk,
            "ep_next": float(e.get("ep_next") or 0), "news": (e.get("news") or "")[:40],
        })
    return players, win, start


def best_xi(squad):
    """Beste 11 uit 15, over alle geldige formaties."""
    by = {P: sorted([p for p in squad if p["p"] == P], key=lambda x: -x["proj"])
          for P in SQUAD_REQ}
    best, bxi = -1, None
    for d in range(XI_MIN["DEF"], XI_MAX["DEF"] + 1):
        for m in range(XI_MIN["MID"], XI_MAX["MID"] + 1):
            for f in range(XI_MIN["FWD"], XI_MAX["FWD"] + 1):
                if 1 + d + m + f != 11:
                    continue
                if d > len(by["DEF"]) or m > len(by["MID"]) or f > len(by["FWD"]):
                    continue
                xi = by["GKP"][:1] + by["DEF"][:d] + by["MID"][:m] + by["FWD"][:f]
                # Aanvoerder telt dubbel - fundamenteel in FPL, dus in de doelfunctie.
                s = sum(x["proj"] for x in xi) + max(x["proj"] for x in xi)
                if s > best:
                    best, bxi = s, xi
    return best, bxi


def legal(squad, budget=1000):
    if len(squad) != 15:
        return False
    cnt = {}
    for p in squad:
        cnt[p["p"]] = cnt.get(p["p"], 0) + 1
    if cnt != SQUAD_REQ:
        return False
    tc = {}
    for p in squad:
        tc[p["tid"]] = tc.get(p["tid"], 0) + 1
        if tc[p["tid"]] > 3:
            return False
    return sum(p["cost"] for p in squad) <= budget


def optimize(players, iters=400, budget=1000, seed=7, lock=None):
    """lock = lijst van (naam, club) die verplicht in de squad moeten."""
    rnd = random.Random(seed)
    locked = []
    for nm, tm in (lock or []):
        hit = [p for p in players if nm.lower() in p["n"].lower() and p["t"] == tm]
        if hit:
            locked.append(sorted(hit, key=lambda x: -x["tp"])[0])
    lock_ids = {p["id"] for p in locked}
    pool = [p for p in players if p["avail"] > 0 or p["cost"] <= 45]
    by = {P: sorted([p for p in pool if p["p"] == P], key=lambda x: -x["proj"])
          for P in SQUAD_REQ}
    # kandidaten begrenzen: top op projectie + top op waarde-per-miljoen
    cand = {}
    for P in SQUAD_REQ:
        a = by[P][:55]
        b = sorted(by[P], key=lambda x: -(x["proj"] / max(x["cost"], 1)))[:55]
        seen, lst = set(), []
        for p in a + b:
            if p["id"] not in seen:
                seen.add(p["id"]); lst.append(p)
        cand[P] = lst

    best_sq, best_sc = None, -1
    for it in range(iters):
        sq = list(locked)
        for P, k in SQUAD_REQ.items():
            need = k - sum(1 for p in locked if p["p"] == P)
            if need <= 0:
                continue
            opts = [c for c in cand[P] if c["id"] not in lock_ids]
            sq += rnd.sample(opts, min(need, len(opts)))
        # repareer naar legaal
        tries = 0
        while not legal(sq, budget) and tries < 800:
            tries += 1
            over = sum(p["cost"] for p in sq) - budget
            tc = {}
            for p in sq:
                tc[p["tid"]] = tc.get(p["tid"], 0) + 1
            bad = [p for p in sq if tc[p["tid"]] > 3 and p["id"] not in lock_ids]
            if bad:
                out = rnd.choice(bad)
            elif over > 0:
                xi_ids = {x["id"] for x in best_xi(sq)[1]} if len(sq) == 15 else set()
                pool2 = [p for p in sq if p["id"] not in xi_ids and p["id"] not in lock_ids] or [p for p in sq if p["id"] not in lock_ids]
                out = max(pool2, key=lambda p: p["cost"])
            else:
                break
            ids = {p["id"] for p in sq}
            opts = [c for c in cand[out["p"]]
                    if c["id"] not in ids and c["cost"] <= out["cost"]]
            if not opts:
                break
            sq[sq.index(out)] = rnd.choice(opts[:30])
        if not legal(sq, budget):
            continue

        sc = best_xi(sq)[0]
        improved = True
        while improved:
            improved = False
            for i in range(15):
                cur = sq[i]
                if cur["id"] in lock_ids:
                    continue
                ids = {p["id"] for p in sq}
                room = budget - sum(p["cost"] for p in sq) + cur["cost"]
                for c in cand[cur["p"]]:
                    if c["id"] in ids or c["cost"] > room:
                        continue
                    trial = sq[:]; trial[i] = c
                    if not legal(trial, budget):
                        continue
                    s2 = best_xi(trial)[0]
                    if s2 > sc + 1e-9:
                        sq, sc, improved = trial, s2, True
                        break
                if improved:
                    break
        if sc > best_sc:
            best_sc, best_sq = sc, sq[:]
    return best_sq, best_sc


def show(squad, win, label):
    sc, xi = best_xi(squad)
    xid = {p["id"] for p in xi}
    bench = [p for p in squad if p["id"] not in xid]
    bench.sort(key=lambda p: (p["p"] == "GKP", -p["proj"]))
    cost = sum(p["cost"] for p in squad)
    print("=" * 96)
    print("%s   |  XI-projectie GW%d-%d: %.1f ptn  |  kosten %.1f  |  bank %.1f" %
          (label, win[0], win[-1], sc, cost / 10.0, (1000 - cost) / 10.0))
    print("=" * 96)
    hdr = "%-16s %-4s %-4s %6s %8s %7s %6s %6s  %s"
    print(hdr % ("SPELER", "CLUB", "POS", "PRIJS", "PROJ%d" % len(win), "PROJ/GW", "PPG", "EIG%", "LET OP"))
    order = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}
    for p in sorted(xi, key=lambda x: (order[x["p"]], -x["proj"])):
        flag = []
        if p["wk"]: flag.append("WK-belasting")
        if p["st"] != "a": flag.append(p["news"] or p["st"])
        if p["mins"] < 900: flag.append("weinig min 25/26")
        print(hdr % (p["n"], p["t"], p["p"], "%.1f" % (p["cost"] / 10.0),
                     "%.1f" % p["proj"], "%.2f" % (p["proj"] / len(win)),
                     "%.1f" % p["ppg"], "%.1f%%" % p["own"], ", ".join(flag)))
    print("  --- bank ---")
    for p in bench:
        print(hdr % (p["n"], p["t"], p["p"], "%.1f" % (p["cost"] / 10.0),
                     "%.1f" % p["proj"], "%.2f" % (p["proj"] / len(win)),
                     "%.1f" % p["ppg"], "%.1f%%" % p["own"], p["news"]))
    cap = max(xi, key=lambda p: p["proj"])
    print("\n  Aanvoerder volgens model: %s (+%.1f ptn over de periode)" % (cap["n"], cap["proj"]))
    return sc


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    m = sub.add_parser("model"); m.add_argument("--gws", type=int, default=5)
    m.add_argument("--pos"); m.add_argument("--n", type=int, default=30)
    m.add_argument("--max", type=float)
    s = sub.add_parser("squad"); s.add_argument("--gws", type=int, default=5)
    s.add_argument("--iters", type=int, default=400); s.add_argument("--lock", default="")
    r = sub.add_parser("rate"); r.add_argument("--gws", type=int, default=5)
    r.add_argument("--team", required=True)
    a = ap.parse_args()

    if a.cmd == "model":
        pl, win, st = load(a.gws)
        rows = [p for p in pl if (not a.pos or p["p"] == a.pos.upper())
                and (not a.max or p["cost"] / 10.0 <= a.max)]
        rows.sort(key=lambda p: -p["proj"])
        print("PROJECTIE GW%d-%d  (eigen model, zie kop van optimize.py)" % (win[0], win[-1]))
        print("%-16s %-4s %-4s %6s %8s %7s %6s %7s %7s  %s" %
              ("SPELER", "CLUB", "POS", "PRIJS", "PROJ", "PROJ/GW", "PPG", "EIG%", "P/PRIJS", "LET OP"))
        for p in rows[:a.n]:
            fl = []
            if p["wk"]: fl.append("WK")
            if p["st"] != "a": fl.append(p["st"].upper())
            if p["mins"] < 900: fl.append("lage min")
            print("%-16s %-4s %-4s %6.1f %8.1f %7.2f %6.1f %6.1f%% %7.2f  %s" %
                  (p["n"], p["t"], p["p"], p["cost"] / 10.0, p["proj"], p["proj"] / len(win),
                   p["ppg"], p["own"], p["proj"] / (p["cost"] / 10.0), ",".join(fl)))
        print("\nBron: officiele FPL API /bootstrap-static/ + /fixtures/ | opgehaald %s"
              % datetime.now(timezone.utc).isoformat(timespec="seconds"))

    elif a.cmd == "squad":
        pl, win, st = load(a.gws)
        lk = [tuple(x.split(":")) for x in a.lock.split(",") if ":" in x]
        sq, sc = optimize(pl, iters=a.iters, lock=lk)
        show(sq, win, "OPTIMAAL TEAM volgens model")
        print("\nBron: officiele FPL API | %s" % datetime.now(timezone.utc).isoformat(timespec="seconds"))

    elif a.cmd == "rate":
        pl, win, st = load(a.gws)
        want = json.load(open(a.team))
        idx = {}
        for p in pl:
            idx.setdefault((p["n"].lower(), p["t"]), p)
        sq = []
        for nm, tm in want["squad"]:
            hit = [p for p in pl if nm.lower() in p["n"].lower() and p["t"] == tm]
            if not hit:
                print("!! niet gevonden:", nm, tm); continue
            sq.append(sorted(hit, key=lambda x: -x["tp"])[0])
        show(sq, win, want.get("label", "TEAM"))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
