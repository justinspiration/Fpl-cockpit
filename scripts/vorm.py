#!/usr/bin/env python3
"""Haalt de gespeelde wedstrijden per Premier League-club op, inclusief de
voorbereiding.

Waarom Wikipedia en niet een sportAPI
-------------------------------------
De FPL-API kent alleen competitieduels; oefenwedstrijden zitten er niet in.
Drie alternatieven zijn getest en afgevallen:

  * TheSportsDB — gratis laag geeft een wedstrijd per club, hun PL-clublijst
    is verouderd, twaalf van de twintig clubs waren te koppelen en na een
    handvol verzoeken volgt een blokkade.
  * BBC Sport — publiceert geen oefenwedstrijden per club.
  * Google — blokkeert geautomatiseerd ophalen en bouwt de pagina met
    JavaScript op.

Wikipedia heeft per club een seizoensartikel waarin elke wedstrijd in een vast
sjabloon staat ({{football box collapsible}}) met datum, beide ploegen, de
eindstand en het toernooi. Dat is gestructureerd, gratis, zonder limiet, en het
dekt de voorbereiding én de competitie.

Wat je erover moet weten
------------------------
Wikipedia wordt door mensen bijgehouden. Een uitslag kan een dag later pas
ingevuld staan, en in theorie kan er een fout in staan. Daarom:
  * elke wedstrijd houdt zijn bron en het artikel waar hij vandaan komt;
  * de competitiestand blijft uit de FPL-API komen — die blijft leidend;
  * dit bestand is alleen voor de VORM, dus de laatste vijf wedstrijden.
Zo bouwt geen enkel puntenmodel hierop, maar zie je wel wie in vorm is.

Gebruik:  python3 vorm.py
"""
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HIER = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HIER, "state")
WIKI = "https://en.wikipedia.org/w/api.php"
UA = {"User-Agent": "fpl-cockpit/1.0 (persoonlijk FPL-dashboard)"}

# FPL kort de clubnamen af ("Man City", "Spurs", "Nott'm Forest") en Wikipedia
# gebruikt de volledige naam met het juiste voor- of achtervoegsel: Bournemouth
# is A.F.C. Bournemouth, Hull City is Hull City A.F.C. Dit is geen verzonnen
# data maar een naamkoppeling; de uitslagen zelf komen onveranderd uit het
# artikel dat erbij hoort.
WIKINAAM = {
    "ARS": "Arsenal F.C.", "AVL": "Aston Villa F.C.", "BOU": "AFC Bournemouth",
    "BRE": "Brentford F.C.", "BHA": "Brighton & Hove Albion F.C.",
    "BUR": "Burnley F.C.", "CHE": "Chelsea F.C.", "COV": "Coventry City F.C.",
    "CRY": "Crystal Palace F.C.", "EVE": "Everton F.C.", "FUL": "Fulham F.C.",
    "HUL": "Hull City A.F.C.", "IPS": "Ipswich Town F.C.", "LEE": "Leeds United F.C.",
    "LIV": "Liverpool F.C.", "MCI": "Manchester City F.C.", "MUN": "Manchester United F.C.",
    "NEW": "Newcastle United F.C.", "NFO": "Nottingham Forest F.C.",
    "SUN": "Sunderland A.F.C.", "TOT": "Tottenham Hotspur F.C.",
    "WHU": "West Ham United F.C.", "WOL": "Wolverhampton Wanderers F.C.",
}

MAANDEN = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june",
     "july", "august", "september", "october", "november", "december"])}


def api(params):
    u = WIKI + "?" + urllib.parse.urlencode({**params, "format": "json"})
    return json.load(urllib.request.urlopen(
        urllib.request.Request(u, headers=UA), timeout=30))


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]", " ", s).strip()


def haal_artikelen(titels):
    """Haal meerdere artikelen in een verzoek op.

    Wikipedia staat vijftig titels per aanvraag toe. Per club apart vragen gaf
    na vier clubs een 429; zo blijft het bij twee verzoeken voor alle twintig.
    """
    uit = {}
    for i in range(0, len(titels), 20):
        groep = titels[i:i + 20]
        d = api({"action": "query", "prop": "revisions", "rvprop": "content",
                 "rvslots": "main", "titles": "|".join(groep), "redirects": 1})
        q = d.get("query", {})
        # normalisaties en doorverwijzingen terugvertalen naar wat we vroegen
        heen = {}
        for n in q.get("normalized", []):
            heen[n["to"]] = n["from"]
        for r in q.get("redirects", []):
            heen[r["to"]] = heen.get(r["from"], r["from"])
        for pg in q.get("pages", {}).values():
            if "missing" in pg or not pg.get("revisions"):
                continue
            gevraagd = heen.get(pg["title"], pg["title"])
            uit[gevraagd] = (pg["title"], pg["revisions"][0]["slots"]["main"]["*"])
        time.sleep(1.2)
    return uit


def velden(blok):
    """Splits de sjabloonvelden.

    Niet zomaar op | splitsen: [[Real Betis|Betis]] en {{fbaicon|ESP}} bevatten
    zelf een |. Daarom tellen we de haakjes mee en knippen we alleen op een |
    die buiten alle haakjes staat.
    """
    uit, sleutel, waarde = {}, None, []
    d_vier, d_krul = 0, 0
    i = 0
    while i < len(blok):
        twee = blok[i:i + 2]
        if twee == "[[":
            d_vier += 1; waarde.append(twee); i += 2; continue
        if twee == "]]":
            d_vier -= 1; waarde.append(twee); i += 2; continue
        if twee == "{{":
            d_krul += 1; waarde.append(twee); i += 2; continue
        if twee == "}}":
            d_krul -= 1; waarde.append(twee); i += 2; continue
        c = blok[i]
        if c == "|" and d_vier <= 0 and d_krul <= 0:
            if sleutel:
                uit[sleutel] = "".join(waarde).strip()
            sleutel, waarde = None, []
            # de sleutel is alles tot de eerste = op dit niveau
            m = re.match(r"\s*([a-z0-9_]+)\s*=", blok[i + 1:], re.I)
            if m:
                sleutel = m.group(1).lower()
                i += 1 + m.end()
                continue
            i += 1
            continue
        waarde.append(c); i += 1
    if sleutel:
        uit[sleutel] = "".join(waarde).strip()
    return uit


def schoon(v):
    """Wikitekst naar leesbare naam: [[Inter Milan]] -> Inter Milan."""
    v = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", v)
    v = re.sub(r"\[\[([^\]]+)\]\]", r"\1", v)
    # externe link met bijschrift: [https://... 5–0] -> 5–0
    v = re.sub(r"\[https?://\S+\s+([^\]]*)\]", r"\1", v)
    v = re.sub(r"\[https?://\S+\]", "", v)
    v = re.sub(r"\{\{[^}]*\}\}", "", v)
    v = re.sub(r"<[^>]+>", "", v)
    return re.sub(r"\s+", " ", v).strip()


def datum(v):
    # Chelsea en anderen gebruiken {{Start date|2026|7|28|df=y}} in plaats van
    # "28 July 2026". Allebei moeten werken.
    m2 = re.search(r"\{\{\s*[Ss]tart date\s*\|\s*(\d{4})\s*\|\s*(\d{1,2})\s*\|\s*(\d{1,2})", v)
    if m2:
        try:
            return datetime(int(m2.group(1)), int(m2.group(2)), int(m2.group(3)),
                            tzinfo=timezone.utc)
        except ValueError:
            return None
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", v)
    if not m:
        return None
    mnd = MAANDEN.get(m.group(2).lower())
    if not mnd:
        return None
    try:
        return datetime(int(m.group(3)), mnd, int(m.group(1)), tzinfo=timezone.utc)
    except ValueError:
        return None


def duels(tekst, club_vol):
    """Alle wedstrijden uit de sjablonen, met uitslag."""
    uit = []
    for m in re.finditer(r"\{\{\s*football box(?: collapsible)?(.*?)\n\}\}", tekst, re.S | re.I):
        blok = m.group(1)
        veld = velden(blok)
        t1, t2 = schoon(veld.get("team1", "")), schoon(veld.get("team2", ""))
        sc = veld.get("score", "")
        d = datum(veld.get("date", ""))
        ms = re.match(r"\s*(\d+)\s*[–\-—]\s*(\d+)", sc)
        if not (t1 and t2 and ms and d):
            continue
        thuis = norm(club_vol) in norm(t1) or norm(t1) in norm(club_vol)
        eigen, ander = (int(ms.group(1)), int(ms.group(2))) if thuis else (int(ms.group(2)), int(ms.group(1)))
        toernooi = schoon(veld.get("round", "")) or "wedstrijd"
        uit.append({
            "datum": d.date().isoformat(),
            "opp": t2 if thuis else t1,
            "thuis": thuis,
            "voor": eigen, "tegen": ander,
            "uit": "W" if eigen > ander else ("G" if eigen == ander else "V"),
            "soort": "oefen" if re.search(r"friendl|trophy|exhibition|cup pre|tour",
                                          toernooi, re.I) else "officieel",
            "toernooi": toernooi[:60],
        })
    if not uit:
        uit = uit_tabel(tekst, club_vol)
    uit.sort(key=lambda x: x["datum"])
    return uit


def uit_tabel(tekst, club_vol):
    """Terugval voor clubs die geen sjablonen gebruiken maar een tabel.

    Manchester United zet zijn wedstrijden in een wikitable met de kolommen
    Date | Opponents | H/A | Result F-A. Dezelfde gegevens, andere vorm.
    """
    uit = []
    for m_tab in re.finditer(r"\{\|[^\n]*wikitable.*?\n\|\}", tekst, re.S):
        tab = m_tab.group(0)
        if "Opponents" not in tab or "Result" not in tab:
            continue
        # onder welk kopje staat deze tabel? Dat bepaalt of het oefenduels zijn.
        koppen = re.findall(r"\n=+\s*([^=\n]+?)\s*=+", tekst[:m_tab.start()])
        kopje = koppen[-1] if koppen else ""
        oefen = bool(re.search(r"pre-?season|friendl", kopje, re.I))
        for rij in re.split(r"\n\|-", tab)[1:]:
            # de rij begint vaak met een opmaakregel (style="background:...")
            # voordat de eerste cel komt; die hoort er niet bij
            rij = re.sub(r"^[^\n]*", "", rij, count=1)
            cellen = [c.strip() for c in re.split(r"\n\|(?!\|)", rij) if c.strip()]
            if len(cellen) < 4:
                continue
            d = datum(schoon(cellen[0]))
            opp = schoon(cellen[1])
            ha = schoon(cellen[2]).upper()[:1]
            ms = re.search(r"(\d+)\s*[\u2013\-\u2014]\s*(\d+)", schoon(cellen[3]))
            if not (d and opp and ms):
                continue
            eigen, ander = int(ms.group(1)), int(ms.group(2))   # kolom is altijd F-A
            # H = thuis, A = uit, N = neutraal terrein
            uit.append({
                "datum": d.date().isoformat(), "opp": opp, "thuis": ha == "H",
                "voor": eigen, "tegen": ander,
                "uit": "W" if eigen > ander else ("G" if eigen == ander else "V"),
                "soort": "oefen" if oefen else "officieel",
                "toernooi": (kopje or ("Pre-season" if oefen else "wedstrijd"))[:60],
                "neutraal": ha == "N",
            })
    return uit


def main():
    bs = json.load(urllib.request.urlopen(urllib.request.Request(
        "https://fantasy.premierleague.com/api/bootstrap-static/", headers=UA), timeout=30))
    clubs = [(t["short_name"], t["name"]) for t in bs["teams"]]

    nu = datetime.now(timezone.utc).date().isoformat()
    data, mislukt = {}, []
    # eerst alle artikelen in twee verzoeken, daarna pas verwerken
    vraag = {}
    for kort, vol in clubs:
        wnaam = WIKINAAM.get(kort)
        if not wnaam:
            print("  LET OP: geen Wikipedia-naam voor %s (%s)" % (kort, vol), file=sys.stderr)
            continue
        vraag[f"2026\u201327 {wnaam} season"] = (kort, wnaam)
    gevonden = haal_artikelen(list(vraag))
    for gevraagd, (kort, vol) in vraag.items():
        try:
            paar = gevonden.get(gevraagd)
            if not paar:
                mislukt.append(kort)
                continue
            titel, tekst = paar
            alle = [d for d in duels(tekst, vol) if d["datum"] <= nu]
            if not alle:
                mislukt.append(kort + " (geen uitslagen)")
                continue
            laatste = alle[-5:]
            data[kort] = {
                "artikel": titel,
                "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(titel.replace(" ", "_")),
                "gespeeld": len(alle),
                "laatste5": laatste,
                "vorm": [d["uit"] for d in laatste],
                "vormpunten": sum({"W": 3, "G": 1, "V": 0}[d["uit"]] for d in laatste),
                "vormmax": len(laatste) * 3,
                "oefen": sum(1 for d in laatste if d["soort"] == "oefen"),
            }
        except Exception as ex:
            mislukt.append(f"{kort}: {str(ex)[:40]}")

    uit = {
        "_bron": "Wikipedia, seizoensartikel per club, sjabloon {{football box collapsible}}",
        "_methode": ("Per club het artikel '2026–27 <club> F.C. season' opgehaald en elke "
                     "wedstrijd met een ingevulde eindstand eruit gehaald. De laatste vijf "
                     "gespeelde duels vormen de vorm, oefenwedstrijden meegeteld en apart "
                     "gemarkeerd."),
        "_waarschuwing": ("Wikipedia wordt door mensen bijgehouden: een uitslag kan later "
                          "ingevuld staan of een fout bevatten. Daarom wordt dit alleen voor "
                          "de VORM gebruikt. De competitiestand komt uit de FPL-API en blijft "
                          "leidend, en geen enkele puntenprojectie rekent hiermee."),
        "_opgehaald": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "_mislukt": mislukt,
        "clubs": data,
    }
    with open(os.path.join(STATE, "vorm.json"), "w", encoding="utf-8") as f:
        json.dump(uit, f, ensure_ascii=False, indent=1)

    print(f"vorm.json: {len(data)} van de {len(clubs)} clubs"
          + (f", niet gelukt: {', '.join(mislukt)}" if mislukt else ""))
    for k, v in list(data.items())[:6]:
        rij = " ".join(f"{d['opp'][:12]} {d['voor']}-{d['tegen']}" for d in v["laatste5"])
        print(f"  {k}: {''.join(v['vorm'])} ({v['vormpunten']}/{v['vormmax']}) "
              f"· {v['oefen']} oefen · {rij[:70]}")


if __name__ == "__main__":
    main()
