#!/usr/bin/env python3
"""Bookmakersodds via the-odds-api.com, in plaats van scrapen.

Waarom niet meer scrapen
------------------------
Odds zijn commercieel waardevol, dus verdedigen odds-sites zich actief. Gemeten
op 10-09-2026:

  Oddschecker  HTTP 403 met een bot-controle — dicht, ook met een echte browser
  OddsPortal   HTTP 200, maar serveert alleen advertenties. Zelfs met een echte
               Chrome en de cookiemuur netjes afgehandeld (Reject All) komt er
               geen enkele wedstrijdregel meer uit. Hun pagina is veranderd.

Dat gevecht win je niet duurzaam: elke herbouw van hun pagina breekt de scraper
opnieuw, en dan sta je weer dagen met oude cijfers. Een gedocumenteerde API
verandert alleen met aankondiging.

Wat dit kost
------------
the-odds-api rekent per markt per regio. Wij vragen één markt (h2h) in één regio
(uk) = 1 credit per aanroep. De gratis laag geeft 500 per maand, dus twee keer
per dag ophalen kost 60 en laat ruim tachtig procent ongebruikt. Vaker heeft
weinig zin: odds bewegen vooral op teamnieuws, niet per uur.

De sleutel
----------
Zet hem als omgevingsvariabele ODDS_API_KEY. Nooit in een bestand, nooit in de
repository — die is publiek. Op GitHub hoort hij in Settings > Secrets.
"""
import json, os, ssl, sys, time, urllib.request, urllib.error

HIER = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HIER, "state")
BASIS = "https://api.the-odds-api.com/v4/sports/soccer_epl/odds/"
FPL = "https://fantasy.premierleague.com/api/bootstrap-static/"

# De API schrijft clubnamen voluit; FPL gebruikt afkortingen. Alles wat we niet
# automatisch kunnen koppelen komt hier terecht — en wordt overgeslagen in
# plaats van gegokt, want een verkeerd gekoppelde club levert stil verkeerde
# doelpuntverwachtingen op voor twee ploegen tegelijk.
HANDMATIG = {
    "manchester city": "MCI", "manchester united": "MUN",
    "tottenham hotspur": "TOT", "newcastle united": "NEW",
    "west ham united": "WHU", "wolverhampton wanderers": "WOL",
    "brighton and hove albion": "BHA", "brighton & hove albion": "BHA",
    "nottingham forest": "NFO", "leeds united": "LEE",
    "sheffield united": "SHU", "luton town": "LUT",
    "ipswich town": "IPS", "leicester city": "LEI",
    "coventry city": "COV", "hull city": "HUL",
    "afc bournemouth": "BOU", "crystal palace": "CRY",
    "aston villa": "AVL", "sunderland": "SUN",
}


def haal(url, pogingen=3, koppen=None):
    req = urllib.request.Request(url, headers=koppen or {"User-Agent": "fpl-cockpit"})
    fout = None
    for p in range(1, pogingen + 1):
        try:
            with urllib.request.urlopen(req, timeout=45,
                                        context=ssl.create_default_context()) as r:
                return json.load(r), dict(r.headers)
        except urllib.error.HTTPError as ex:
            lijf = ex.read().decode("utf-8", "replace")[:200]
            if ex.code in (401, 422):
                sys.exit("[odds-api] MISLUKT: %s — %s" % (ex.code, lijf))
            fout = "%s %s" % (ex.code, lijf)
        except Exception as ex:
            fout = str(ex)
        if p < pogingen:
            time.sleep(3 * p)
    sys.exit("[odds-api] MISLUKT na %d pogingen: %s" % (pogingen, fout))


def kort_map():
    bs, _ = haal(FPL)
    m = {}
    for t in bs["teams"]:
        m[t["name"].lower()] = t["short_name"]
        m[t["short_name"].lower()] = t["short_name"]
    m.update(HANDMATIG)
    return m


def naar_kort(naam, m):
    n = (naam or "").lower().strip()
    if n in m:
        return m[n]
    # "Manchester City FC" -> "manchester city"
    for achtervoegsel in (" fc", " afc"):
        if n.endswith(achtervoegsel) and n[:-len(achtervoegsel)] in m:
            return m[n[: -len(achtervoegsel)]]
    # laatste poging: unieke gedeeltelijke match
    kand = {v for k, v in m.items() if len(k) > 4 and (k in n or n in k)}
    return kand.pop() if len(kand) == 1 else None


def verwerk(data, m):
    duels, onbekend = [], set()
    for w in data:
        h = naar_kort(w.get("home_team"), m)
        u = naar_kort(w.get("away_team"), m)
        if not h or not u:
            onbekend.add("%s / %s" % (w.get("home_team"), w.get("away_team")))
            continue
        # Alle bookmakers middelen. Eén bookmaker is één mening; het gemiddelde
        # van tien is de marktprijs, en dat is wat we willen.
        per = {"thuis": [], "gelijk": [], "uit": []}
        for b in w.get("bookmakers", []):
            for markt in b.get("markets", []):
                if markt.get("key") != "h2h":
                    continue
                for uitk in markt.get("outcomes", []):
                    naam, prijs = uitk.get("name"), uitk.get("price")
                    if not prijs:
                        continue
                    if naam == w.get("home_team"):
                        per["thuis"].append(prijs)
                    elif naam == w.get("away_team"):
                        per["uit"].append(prijs)
                    elif (naam or "").lower() == "draw":
                        per["gelijk"].append(prijs)
        if not all(per[k] for k in per):
            onbekend.add("%s-%s (onvolledige odds)" % (h, u))
            continue
        gem = {k: round(sum(v) / len(v), 3) for k, v in per.items()}
        tijd = (w.get("commence_time") or "")[11:16]
        duels.append({"thuis": h, "uit": u, "tijd": tijd, "odds": gem,
                      "bookmakers": len(w.get("bookmakers", []))})
    return duels, onbekend


def main():
    sleutel = os.environ.get("ODDS_API_KEY", "").strip()
    if not sleutel:
        print("[odds-api] Geen ODDS_API_KEY ingesteld — overgeslagen.")
        print("[odds-api] Maak een gratis sleutel op https://the-odds-api.com en zet hem als")
        print("[odds-api] omgevingsvariabele. Zonder odds valt het dashboard terug op GoalIQ")
        print("[odds-api] en het eigen model; dat werkt, maar mist de marktkennis.")
        return 0

    url = ("%s?apiKey=%s&regions=uk&markets=h2h&oddsFormat=decimal"
           % (BASIS, urllib.parse.quote(sleutel)))
    data, koppen = haal(url)
    if not isinstance(data, list):
        sys.exit("[odds-api] MISLUKT: onverwacht antwoord")
    m = kort_map()
    duels, onbekend = verwerk(data, m)
    if not duels:
        sys.exit("[odds-api] MISLUKT: geen enkel duel gekoppeld (%d ontvangen)" % len(data))

    over = koppen.get("x-requests-remaining")
    gebruikt = koppen.get("x-requests-used")
    uit = {"_bron": "the-odds-api.com — soccer_epl, markt h2h, regio uk",
           "_markt": "h2h (1X2), gemiddelde over alle bookmakers",
           "_opgehaald": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
           "_methode": ("Decimale odds per bookmaker, gemiddeld per uitkomst. "
                        "Een enkele bookmaker is een mening; het gemiddelde van "
                        "meerdere benadert de marktprijs."),
           "_credits_over": over, "_credits_gebruikt": gebruikt,
           "_duels": len(duels), "_onbekend": sorted(onbekend),
           "duels": duels}
    os.makedirs(STATE, exist_ok=True)
    json.dump(uit, open(os.path.join(STATE, "odds_ruw.json"), "w",
                        encoding="utf-8"), ensure_ascii=False, indent=1)
    gem_bm = sum(d["bookmakers"] for d in duels) / len(duels)
    print("[odds-api] %d duels, gemiddeld %.0f bookmakers per duel%s"
          % (len(duels), gem_bm,
             ", nog %s van de %s credits over"
             % (over, int(over or 0) + int(gebruikt or 0)) if over else ""))
    if onbekend:
        print("[odds-api] overgeslagen: %s" % ", ".join(sorted(onbekend)[:5]))
    return 0


if __name__ == "__main__":
    import urllib.parse
    sys.exit(main())
