#!/usr/bin/env python3
"""Haalt de puntenvoorspellingen van Fantasy Football Pundit op.

Waarom deze bron
----------------
Justin vertrouwt FPL Form niet als maatstaf, en terecht gevraagd: waarom zou je
middelen met een cijfer waar je zelf niet in gelooft? Pundit is aantoonbaar
rijker. Voor elke speler en elke komende gameweek levert het:

  predicted_points        verwachte punten, speelkans al meegerekend
  predicted_points_start  wat hij zou opleveren ALS hij start
  start_pct               hoe waarschijnlijk het is dat hij start
  source                  "odds" als het uit de wedstrijdmarkt komt,
                          "estimated" als het hun eigen schatting is

Die laatste twee zijn precies waar onze adviezen op stukliepen: de meeste
meningsverschillen tussen bronnen gaan over MINUTEN, niet over kwaliteit. Pundit
zet die twee uit elkaar in plaats van ze tot één getal te vermenigvuldigen.

Geen browser nodig: de pagina is Next.js en draagt de volledige dataset als JSON
mee in de HTML. We halen hem er met een haakjesteller uit — geen reguliere
expressie over JSON, want dat breekt zodra er een accolade in een spelersnaam staat.

Koppeling gaat op `player_code`, en dat is FPL's `code` — een nummer dat aan de
speler hangt en niet aan het seizoen. Stabieler dan `id`, dat per seizoen opnieuw
wordt uitgedeeld.

Bron: https://www.fantasyfootballpundit.com/fpl-points-predictor/
"""
import json, os, ssl, time, urllib.request

HIER = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HIER, "state")
URL = "https://www.fantasyfootballpundit.com/fpl-points-predictor/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def haal(pogingen=3):
    req = urllib.request.Request(URL, headers={"User-Agent": UA,
                                               "Accept": "text/html"})
    fout = None
    for p in range(1, pogingen + 1):
        try:
            with urllib.request.urlopen(req, timeout=60,
                                        context=ssl.create_default_context()) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as ex:
            fout = ex
            if p < pogingen:
                time.sleep(3 * p)
    raise SystemExit("[pundit] MISLUKT: pagina niet op te halen (%s)" % fout)


def _array_na(tekst, sleutel):
    """Leest de JSON-array die direct na `sleutel` staat.

    De payload zit dubbel ge-escaped in de HTML. We tellen zelf haakjes in
    plaats van json.loads op een gok los te laten, want de array is groot en
    een verkeerde afkapping levert stil een halve dataset op.
    """
    i = tekst.find(sleutel)
    if i < 0:
        return None
    i = tekst.find("[", i)
    if i < 0:
        return None
    diep, j, in_str, ontsnapt = 0, i, False, False
    while j < len(tekst):
        c = tekst[j]
        if in_str:
            if ontsnapt:
                ontsnapt = False
            elif c == "\\":
                ontsnapt = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "[":
                diep += 1
            elif c == "]":
                diep -= 1
                if diep == 0:
                    return tekst[i:j + 1]
        j += 1
    return None


def main():
    html = haal()
    # De HTML draagt de payload ge-escaped mee; eerst terugbrengen naar echte JSON.
    plat = html.replace('\\\\"', "\x00").replace('\\"', '"').replace("\x00", '\\"')
    ruw = _array_na(plat, '"rows":')
    if not ruw:
        raise SystemExit("[pundit] MISLUKT: geen 'rows'-blok gevonden — "
                         "de pagina-opbouw is waarschijnlijk gewijzigd")
    try:
        rijen = json.loads(ruw)
    except Exception as ex:
        raise SystemExit("[pundit] MISLUKT: rows-blok niet te lezen (%s)" % str(ex)[:90])
    if len(rijen) < 500:
        raise SystemExit("[pundit] MISLUKT: slechts %d rijen — verwacht er honderden"
                         % len(rijen))

    # FPL erbij om op code te koppelen en de prijzen te toetsen
    with urllib.request.urlopen(urllib.request.Request(
            "https://fantasy.premierleague.com/api/bootstrap-static/",
            headers={"User-Agent": UA}), timeout=45) as r:
        bs = json.load(r)
    per_code = {e["code"]: e for e in bs["elements"]}

    spelers, gws, ongekoppeld, prijs_ok, prijs_n = {}, set(), set(), 0, 0
    for r in rijen:
        code = r.get("player_code")
        e = per_code.get(code)
        if not e:
            ongekoppeld.add(r.get("web_name"))
            continue
        try:
            gw = int(r.get("gw"))
        except (TypeError, ValueError):
            continue
        gws.add(gw)
        try:
            prijs = float(r.get("price") or 0)
        except ValueError:
            prijs = 0
        if prijs:
            prijs_n += 1
            if abs(prijs * 10 - e["now_cost"]) <= 2:
                prijs_ok += 1
        d = spelers.setdefault(str(e["id"]), {"n": e["web_name"], "xp": {}, "min": {},
                                              "start": {}, "bron": {}})
        try:
            d["xp"][str(gw)] = round(float(r.get("predicted_points") or 0), 2)
            d["start"][str(gw)] = round(float(r.get("start_pct") or 0) / 100.0, 3)
            # Pundit geeft geen minuten, wel de startkans. 90 minuten bij een
            # basisplaats is de gangbare aanname; wie invalt haalt er ruwweg 25.
            k = d["start"][str(gw)]
            d["min"][str(gw)] = round(k * 90 + (1 - k) * 25)
            d["bron"][str(gw)] = r.get("source")
        except (TypeError, ValueError):
            continue

    if len(spelers) < 300:
        raise SystemExit("[pundit] MISLUKT: slechts %d spelers gekoppeld" % len(spelers))
    if prijs_n >= 50 and prijs_ok / prijs_n < 0.70:
        raise SystemExit("[pundit] MISLUKT: prijzen wijken af bij %d%% van de rijen — "
                         "de bron is waarschijnlijk verouderd"
                         % round(100 * (1 - prijs_ok / prijs_n)))

    uit = {"bron": "Fantasy Football Pundit — %s" % URL,
           "opgehaald": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
           "vanaf_gw": min(gws), "tot_gw": max(gws),
           "methode": ("predicted_points per gameweek, met start_pct als aparte "
                       "startkans. Het veld `source` zegt per duel of het cijfer uit "
                       "de wedstrijdmarkt (odds) komt of uit hun eigen schatting."),
           "spelers": spelers}
    os.makedirs(STATE, exist_ok=True)
    json.dump(uit, open(os.path.join(STATE, "xp_pundit.json"), "w",
                        encoding="utf-8"), ensure_ascii=False)
    uit_odds = sum(1 for d in spelers.values() for v in d["bron"].values() if v == "odds")
    print("[pundit] %d spelers, GW%d-%d, %d rijen uit de wedstrijdmarkt, "
          "prijzen kloppen bij %d%%"
          % (len(spelers), min(gws), max(gws), uit_odds,
             round(100 * prijs_ok / max(1, prijs_n))))
    if ongekoppeld:
        print("[pundit] niet gekoppeld: %s" % ", ".join(sorted(x for x in ongekoppeld if x)[:6]))


if __name__ == "__main__":
    main()
