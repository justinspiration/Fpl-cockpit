#!/usr/bin/env python3
"""
FPL data-engine voor Justin. Haalt ALTIJD live data uit de officiele FPL API.
Geen enkele waarde in de output komt uit het geheugen van het model.

Gebruik:
  python3 fpl.py deadline                 # volgende deadline + countdown (NL-tijd)
  python3 fpl.py snapshot                 # sla prijs/ownership-momentopname op
  python3 fpl.py prices                   # prijswijzigingen sinds vorige snapshot
  python3 fpl.py players [--pos MID] [--max 8.0] [--min-mins 0] [--sort form] [--n 25]
  python3 fpl.py fixtures [--next 5]      # FDR-ticker per club
  python3 fpl.py team <entry_id> [gw]     # squad van een manager
  python3 fpl.py league <league_id>       # mini-league stand + effectieve ownership
  python3 fpl.py status                   # is de game live of in onderhoud?

Alle tijden worden getoond in Europe/Amsterdam.
"""
import sys, os, json, time, argparse, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

BASE = "https://fantasy.premierleague.com/api"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36"
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")

# Europe/Amsterdam zonder externe deps: CEST (+2) laatste zondag maart -> laatste zondag oktober.
def _last_sunday(year, month):
    d = datetime(year, month, 31) if month != 3 else datetime(year, 3, 31)
    while d.weekday() != 6:
        d -= timedelta(days=1)
    return d

def to_nl(dt_utc):
    y = dt_utc.year
    start = _last_sunday(y, 3).replace(hour=1, tzinfo=timezone.utc)
    end = _last_sunday(y, 10).replace(hour=1, tzinfo=timezone.utc)
    off = 2 if start <= dt_utc < end else 1
    return dt_utc.astimezone(timezone(timedelta(hours=off))), ("CEST" if off == 2 else "CET")

def fetch(path, tries=3):
    url = path if path.startswith("http") else BASE + path
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 503:
                raise SystemExit("FPL API geeft 503 (Game Updating). De game is nu in onderhoud "
                                 "of nog niet live. Probeer later opnieuw.")
            time.sleep(1.5 * (i + 1))
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise SystemExit("FPL API niet bereikbaar: %s" % last)

def boot():
    return fetch("/bootstrap-static/")

def idx(bs):
    teams = {t["id"]: t for t in bs["teams"]}
    pos = {p["id"]: p["singular_name_short"] for p in bs["element_types"]}
    return teams, pos

def f(v, d=1):
    try:
        return round(float(v), d)
    except (TypeError, ValueError):
        return 0.0

def next_event(bs):
    for e in bs["events"]:
        if e.get("is_next"):
            return e
    for e in bs["events"]:
        if not e.get("finished"):
            return e
    return None

# ---------------------------------------------------------------- commands
def cmd_status(_):
    bs = boot()
    ev = next_event(bs)
    cur = [e for e in bs["events"] if e.get("is_current")]
    print("Game is LIVE. Spelers in database: %d" % len(bs["elements"]))
    print("Huidige GW: %s" % (cur[0]["name"] if cur else "geen (pre-season)"))
    print("Volgende GW: %s" % (ev["name"] if ev else "onbekend"))
    print("Opgehaald: %s" % datetime.now(timezone.utc).isoformat(timespec="seconds"))

def cmd_deadline(_):
    bs = boot()
    ev = next_event(bs)
    if not ev:
        print("Geen komende gameweek gevonden.")
        return
    dl = datetime.fromisoformat(ev["deadline_time"].replace("Z", "+00:00"))
    nl, tz = to_nl(dl)
    now = datetime.now(timezone.utc)
    left = dl - now
    h = left.total_seconds() / 3600.0
    print("VOLGENDE DEADLINE")
    print("  %s" % ev["name"])
    print("  UK/UTC : %s" % dl.strftime("%a %d %b %Y %H:%M UTC"))
    print("  NL     : %s %s" % (nl.strftime("%a %d %b %Y %H:%M"), tz))
    if h < 0:
        print("  status : VERSTREKEN")
    else:
        print("  resteert: %dd %dh %dm" % (left.days, left.seconds // 3600, (left.seconds % 3600) // 60))
    print("  T-2u    : %s %s  <-- briefingmoment" % ((nl - timedelta(hours=2)).strftime("%a %d %b %H:%M"), tz))
    if ev.get("most_captained"):
        el = {e["id"]: e for e in bs["elements"]}
        mc = el.get(ev["most_captained"])
        if mc:
            print("  meest aangevoerd: %s" % mc["web_name"])

def cmd_snapshot(_):
    bs = boot()
    ev = next_event(bs)
    snap = {
        "taken_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": ev["name"] if ev else None,
        "players": {str(e["id"]): {"n": e["web_name"], "c": e["now_cost"],
                                   "o": e["selected_by_percent"]} for e in bs["elements"]},
    }
    if not os.path.isdir(STATE):
        os.makedirs(STATE)
    p = os.path.join(STATE, "snapshot.json")
    if os.path.exists(p):
        os.replace(p, os.path.join(STATE, "snapshot_prev.json"))
    with open(p, "w") as fh:
        json.dump(snap, fh)
    print("Snapshot opgeslagen: %d spelers, %s" % (len(snap["players"]), snap["taken_utc"]))

def cmd_prices(_):
    p = os.path.join(STATE, "snapshot.json")
    if not os.path.exists(p):
        raise SystemExit("Geen eerdere snapshot. Draai eerst: python3 fpl.py snapshot")
    with open(p) as fh:
        old = json.load(fh)
    bs = boot()
    teams, pos = idx(bs)
    ups, downs = [], []
    for e in bs["elements"]:
        o = old["players"].get(str(e["id"]))
        if not o:
            continue
        d = e["now_cost"] - o["c"]
        if d:
            row = (e["web_name"], teams[e["team"]]["short_name"], pos[e["element_type"]],
                   o["c"] / 10.0, e["now_cost"] / 10.0, f(e["selected_by_percent"]))
            (ups if d > 0 else downs).append(row)
    print("PRIJSWIJZIGINGEN sinds %s" % old["taken_utc"])
    for label, rows in (("STIJGERS", ups), ("DALERS", downs)):
        print("\n%s (%d)" % (label, len(rows)))
        for r in sorted(rows, key=lambda x: -x[5]):
            print("  %-16s %-4s %-4s %.1f -> %.1f   eig. %.1f%%" % r)
    if not ups and not downs:
        print("  geen wijzigingen")

def cmd_pricewatch(a):
    """Voorspelt prijsstijgingen/-dalingen uit netto transfers deze gameweek.

    FPL's algoritme: netto transfers IN boven een drempel -> +0,1; netto OUT
    onder een drempel (als percentage van eigendom) -> -0,1. De exacte drempel
    is niet openbaar, dus dit is een INSCHATTING op basis van netto transfers
    afgezet tegen het aantal eigenaren. Wildcard/Free Hit tellen niet mee en
    zijn hier niet uit te filteren -> altijd als schatting presenteren.
    """
    bs = boot()
    teams, pos = idx(bs)
    spelers = bs["total_players"] or 1
    rows = []
    for e in bs["elements"]:
        tin = e.get("transfers_in_event", 0) or 0
        tout = e.get("transfers_out_event", 0) or 0
        net = tin - tout
        eig = f(e["selected_by_percent"]) / 100.0 * spelers  # aantal eigenaren
        if eig < 1:
            eig = 1
        druk = net / eig * 100.0   # netto transfers als % van de eigenaren
        rows.append((druk, net, tin, tout, e, eig))
    rows.sort(key=lambda x: -x[0])
    if all(r[1] == 0 for r in rows):
        print("Nog geen transferdata: de gameweek is niet begonnen. Prijzen bewegen pas\nzodra GW1 live is. Gebruik tot die tijd `fpl.py snapshot` + `fpl.py prices`\nom wijzigingen achteraf te detecteren.")
        return
    print("PRIJSDRUK — inschatting op basis van netto transfers deze gameweek")
    print("Totaal actieve FPL-spelers: %s\n" % "{:,}".format(spelers))
    hdr = "%-16s %-4s %-4s %6s %10s %10s %9s  %s"
    print(hdr % ("SPELER", "CLUB", "POS", "PRIJS", "NETTO", "IN", "UIT", "DRUK%"))
    print("--- GROOTSTE STIJGINGSKANS ---")
    for druk, net, tin, tout, e, eig in rows[:a.n]:
        print(hdr % (e["web_name"], teams[e["team"]]["short_name"], pos[e["element_type"]],
                     "%.1f" % (e["now_cost"] / 10.0), "{:+,}".format(net),
                     "{:,}".format(tin), "{:,}".format(tout), "%+.1f" % druk))
    print("\n--- GROOTSTE DAALKANS ---")
    for druk, net, tin, tout, e, eig in rows[-a.n:][::-1]:
        print(hdr % (e["web_name"], teams[e["team"]]["short_name"], pos[e["element_type"]],
                     "%.1f" % (e["now_cost"] / 10.0), "{:+,}".format(net),
                     "{:,}".format(tin), "{:,}".format(tout), "%+.1f" % druk))
    print("\nLET OP: schatting, geen zekerheid. Wildcard-/Free Hit-transfers tellen niet")
    print("mee voor prijswijzigingen en zijn hier niet uit te filteren.")
    print("Bron: officiele FPL API /bootstrap-static/, velden transfers_in_event /")
    print("transfers_out_event / selected_by_percent | %s"
          % datetime.now(timezone.utc).isoformat(timespec="seconds"))


def cmd_players(a):
    bs = boot()
    teams, pos = idx(bs)
    rows = []
    for e in bs["elements"]:
        pname = pos[e["element_type"]]
        if a.pos and pname.upper() != a.pos.upper():
            continue
        if a.team and teams[e["team"]]["short_name"].upper() != a.team.upper():
            continue
        if a.max and e["now_cost"] / 10.0 > a.max:
            continue
        if e["minutes"] < a.min_mins:
            continue
        rows.append({
            "n": e["web_name"], "t": teams[e["team"]]["short_name"], "p": pname,
            "cost": e["now_cost"] / 10.0, "tp": e["total_points"], "form": f(e["form"]),
            "ppg": f(e["points_per_game"]), "own": f(e["selected_by_percent"]),
            "mins": e["minutes"], "xgi": f(e.get("expected_goal_involvements", 0), 2),
            "ep": f(e.get("ep_next", 0)), "tin": e.get("transfers_in_event", 0),
            "st": e.get("status", "a"), "news": (e.get("news") or "")[:42],
        })
    key = {"form": "form", "points": "tp", "ppg": "ppg", "own": "own",
           "cost": "cost", "xgi": "xgi", "ep": "ep", "tin": "tin"}.get(a.sort, "form")
    rows.sort(key=lambda r: -r[key])
    print("%-16s %-4s %-4s %6s %5s %5s %5s %6s %6s %6s  %s" %
          ("SPELER", "CLUB", "POS", "PRIJS", "PTN", "VORM", "PPG", "EIG%", "xGI", "EP", "STATUS"))
    for r in rows[:a.n]:
        flag = "" if r["st"] == "a" else ("[%s] %s" % (r["st"].upper(), r["news"]))
        print("%-16s %-4s %-4s %6.1f %5d %5.1f %5.1f %5.1f%% %6.2f %6.1f  %s" %
              (r["n"], r["t"], r["p"], r["cost"], r["tp"], r["form"], r["ppg"],
               r["own"], r["xgi"], r["ep"], flag))
    print("\nBron: officiele FPL API /bootstrap-static/ | %s" %
          datetime.now(timezone.utc).isoformat(timespec="seconds"))

def cmd_fixtures(a):
    bs = boot()
    teams, _ = idx(bs)
    fx = fetch("/fixtures/")
    ev = next_event(bs)
    start = ev["id"] if ev else 1
    gws = list(range(start, start + a.next))
    grid = {t["id"]: {g: [] for g in gws} for t in bs["teams"]}
    for m in fx:
        g = m.get("event")
        if g not in gws:
            continue
        grid[m["team_h"]][g].append("%s (T) %d" % (teams[m["team_a"]]["short_name"], m["team_h_difficulty"]))
        grid[m["team_a"]][g].append("%s (U) %d" % (teams[m["team_h"]]["short_name"], m["team_a_difficulty"]))
    scored = []
    for tid, row in grid.items():
        tot, n = 0, 0
        for g in gws:
            for c in row[g]:
                tot += int(c.split()[-1]); n += 1
        scored.append((tot / n if n else 9.9, teams[tid]["short_name"], row, n))
    scored.sort()
    print("FDR-TICKER  GW%d-%d   (lager = makkelijker; DGW = 2 duels, BGW = leeg)" % (gws[0], gws[-1]))
    print("%-5s %5s  %s" % ("CLUB", "GEM", "  ".join("GW%d" % g for g in gws)))
    for avg, sn, row, n in scored:
        cells = []
        for g in gws:
            cells.append(" + ".join(row[g]) if row[g] else "-- BGW")
        tag = "  <<DGW/BGW>>" if n != len(gws) else ""
        print("%-5s %5.2f  %s%s" % (sn, avg, " | ".join(cells), tag))
    print("\nBron: officiele FPL API /fixtures/ | %s" %
          datetime.now(timezone.utc).isoformat(timespec="seconds"))

def cmd_team(a):
    bs = boot()
    teams, pos = idx(bs)
    el = {e["id"]: e for e in bs["elements"]}
    ent = fetch("/entry/%s/" % a.entry_id)
    gw = a.gw or (next_event(bs) or {}).get("id", 1)
    print("MANAGER: %s %s  (%s)" % (ent.get("player_first_name", ""), ent.get("player_last_name", ""),
                                    ent.get("name", "")))
    print("  Totaal: %s ptn | OR: %s | Teamwaarde: %.1f | Bank: %.1f" % (
        ent.get("summary_overall_points"), ent.get("summary_overall_rank"),
        (ent.get("last_deadline_value") or 0) / 10.0, (ent.get("last_deadline_bank") or 0) / 10.0))
    try:
        picks = fetch("/entry/%s/event/%d/picks/" % (a.entry_id, gw))
    except SystemExit:
        print("  (nog geen picks voor GW%d)" % gw)
        return
    print("\nSQUAD GW%d" % gw)
    for p in picks["picks"]:
        e = el[p["element"]]
        mark = " (C)" if p["multiplier"] >= 2 else (" (VC)" if p.get("is_vice_captain") else "")
        bench = "  BANK" if p["position"] > 11 else ""
        print("  %-16s %-4s %-4s %5.1f  vorm %4.1f  eig %5.1f%%%s%s" % (
            e["web_name"], teams[e["team"]]["short_name"], pos[e["element_type"]],
            e["now_cost"] / 10.0, f(e["form"]), f(e["selected_by_percent"]), mark, bench))
    ac = picks.get("active_chip")
    print("\n  Actieve chip: %s" % (ac if ac else "geen"))

def cmd_league(a):
    bs = boot()
    teams, pos = idx(bs)
    el = {e["id"]: e for e in bs["elements"]}
    st = fetch("/leagues-classic/%s/standings/" % a.league_id)
    res = st["standings"]["results"]
    print("MINI-LEAGUE: %s  (%d managers)" % (st["league"]["name"], len(res)))
    print("%-4s %-24s %-18s %8s %8s" % ("#", "TEAM", "MANAGER", "TOTAAL", "GW"))
    for r in res:
        print("%-4d %-24s %-18s %8d %8d" % (r["rank"], r["entry_name"][:24],
                                            r["player_name"][:18], r["total"], r["event_total"]))
    gw = (next_event(bs) or {}).get("id", 1) - 1
    if gw < 1:
        print("\n(Nog geen afgeronde gameweek: effectieve ownership volgt na GW1.)")
        return
    print("\nEFFECTIEVE OWNERSHIP binnen deze league (GW%d) -- jouw echte risicomaat" % gw)
    own, cap, n = {}, {}, 0
    for r in res:
        try:
            pk = fetch("/entry/%s/event/%d/picks/" % (r["entry"], gw))
        except SystemExit:
            continue
        n += 1
        for p in pk["picks"]:
            if p["position"] <= 11:
                own[p["element"]] = own.get(p["element"], 0) + 1
            if p["multiplier"] >= 2:
                cap[p["element"]] = cap.get(p["element"], 0) + 1
    rows = []
    for pid, c in own.items():
        e = el[pid]
        eo = 100.0 * (c + cap.get(pid, 0)) / n
        rows.append((eo, 100.0 * c / n, 100.0 * cap.get(pid, 0) / n, e["web_name"],
                     teams[e["team"]]["short_name"], pos[e["element_type"]],
                     f(e["selected_by_percent"])))
    rows.sort(key=lambda x: -x[0])
    print("%-16s %-4s %-4s %8s %8s %8s %10s" %
          ("SPELER", "CLUB", "POS", "EO-LEAGUE", "BEZIT%", "CAPT%", "EO-WERELD"))
    for eo, o, c, nm, tm, ps, glob in rows[:30]:
        print("%-16s %-4s %-4s %7.0f%% %7.0f%% %7.0f%% %9.1f%%" % (nm, tm, ps, eo, o, c, glob))
    print("\n  n=%d teams uitgelezen. Hoge EO-league = veilig bezit / duur om te missen." % n)
    print("  Lage EO-league + hoge kwaliteit = jouw differentialkans binnen de league.")

def main():
    ap = argparse.ArgumentParser(add_help=True)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status"); sub.add_parser("deadline")
    sub.add_parser("snapshot"); sub.add_parser("prices")
    p = sub.add_parser("pricewatch"); p.add_argument("--n", type=int, default=12)
    p = sub.add_parser("players")
    p.add_argument("--pos"); p.add_argument("--max", type=float); p.add_argument("--team")
    p.add_argument("--min-mins", type=int, default=0, dest="min_mins")
    p.add_argument("--sort", default="form"); p.add_argument("--n", type=int, default=25)
    p = sub.add_parser("fixtures"); p.add_argument("--next", type=int, default=5)
    p = sub.add_parser("team"); p.add_argument("entry_id"); p.add_argument("gw", nargs="?", type=int)
    p = sub.add_parser("league"); p.add_argument("league_id")
    a = ap.parse_args()
    fn = {"status": cmd_status, "deadline": cmd_deadline, "snapshot": cmd_snapshot,
          "prices": cmd_prices, "players": cmd_players, "fixtures": cmd_fixtures,
          "pricewatch": cmd_pricewatch,
          "team": cmd_team, "league": cmd_league}.get(a.cmd)
    if not fn:
        ap.print_help(); return
    fn(a)

if __name__ == "__main__":
    main()
