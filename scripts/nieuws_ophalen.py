#!/usr/bin/env python3
"""Verzamelt Premier League- en FPL-nieuws uit publieke RSS-feeds.

Waarom RSS en niet scrapen: feeds zijn gestructureerd, hebben een tijdstempel,
breken niet als een site zijn opmaak wijzigt, en er zit geen blokkade op. Voor
het soort bericht dat er hier toe doet — "Haaland traint apart", "Arteta over
Saliba" — is dat precies goed.

Elk bericht krijgt een relevantiescore. Berichten die een speler uit Justins
selectie noemen wegen het zwaarst, daarna spelers die hij volgt of overweegt,
daarna algemeen blessure- en opstellingsnieuws. Zo staat bovenaan wat hem
werkelijk raakt in plaats van wat toevallig het laatst gepubliceerd is.

Gebruik:  python3 nieuws_ophalen.py
"""
import base64
import html
import io
import json
import os
import re
import sys
import unicodedata
import urllib.request
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

HIER = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HIER, "state")

FEEDS = [
    ("Fantasy Football Scout", "https://www.fantasyfootballscout.co.uk/feed", "fpl"),
    ("BBC Sport", "https://feeds.bbci.co.uk/sport/football/premier-league/rss.xml", "algemeen"),
    ("Sky Sports", "https://www.skysports.com/rss/12040", "algemeen"),
    ("The Guardian", "https://www.theguardian.com/football/premierleague/rss", "algemeen"),
    ("talkSPORT", "https://talksport.com/football/premier-league/feed/", "geruchten"),
    # Voetbal International schrijft over al het voetbal; alleen de Premier
    # League-berichten mogen erdoor. Dat filter staat in ALLEEN_PL hieronder.
    ("Voetbal International", "https://www.vi.nl/feed/news.xml", "nl"),
]

# FotMob heeft geen openbare nieuwsfeed meer: de nieuws-tab is uit hun API
# verdwenen (leagues?id=47&tab=news geeft de tab niet meer terug) en de site is
# een JavaScript-app zonder RSS. Wat FotMob toont is bovendien doorgeplaatst
# werk van BBC, Sky en Guardian — bronnen die hier al in staan. Zodra er wel
# een feed komt, is dit een regel erbij.

# Clubs en begrippen waaraan we een Premier League-bericht herkennen. Nodig voor
# feeds die niet alleen over Engeland gaan.
PL_WOORDEN = {
    "arsenal", "aston villa", "bournemouth", "brentford", "brighton", "burnley",
    "chelsea", "coventry", "crystal palace", "everton", "fulham", "hull",
    "ipswich", "leeds", "liverpool", "manchester city", "man city",
    "manchester united", "man utd", "man united", "newcastle",
    "nottingham forest", "sunderland", "tottenham", "spurs", "west ham",
    "wolves", "wolverhampton", "premier league", "old trafford", "anfield",
    "etihad", "stamford bridge", "emirates",
}
ALLEEN_PL = {"nl"}          # soorten waarvoor het PL-filter verplicht is

# Woorden die een bericht relevant maken voor het opstellen van een team.
SIGNAAL = {
    "blessure": 5, "injury": 5, "injured": 5, "out for": 5, "ruled out": 6,
    "sidelined": 5, "surgery": 5, "operatie": 5, "hamstring": 4, "knee": 3,
    "ankle": 3, "doubt": 4, "doubtful": 4, "fitness": 3, "return": 3,
    "comeback": 3, "available": 2, "suspended": 5, "suspension": 5, "ban": 4,
    "red card": 4, "team news": 6, "line-up": 4, "lineup": 4, "starting xi": 5,
    "press conference": 4, "rotation": 4, "rested": 4, "rest": 2,
    "transfer": 3, "signing": 3, "deal": 2, "medical": 3, "loan": 2,
    "penalty": 3, "set-piece": 3, "captain": 2, "manager": 2, "sack": 3,
}
RUIS = ("women", "wsl", "u21", "under-21", "academy", "podcast", "quiz",
        # rollende liveblogs: die noemen alle competities door elkaar en zijn
        # een uur later achterhaald
        "vi live", "live blog", "liveblog", "as it happened", "minute by minute")


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]", " ", s)


class _Redirect308(urllib.request.HTTPRedirectHandler):
    """urllib volgt 301 en 302 vanzelf, maar laat 308 vallen. VI gebruikt 308."""
    def http_error_308(self, req, fp, code, msg, headers):
        return self.http_error_301(req, fp, 301, msg, headers)


_OPENER = urllib.request.build_opener(_Redirect308)
_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) fpl-cockpit"}


def haal(url, timeout=20):
    req = urllib.request.Request(url, headers=_UA)
    return _OPENER.open(req, timeout=timeout).read().decode("utf-8", "replace")


def beeld_inbakken(url, breedte, kwaliteit=82, timeout=15, minimum=0):
    """Download een afbeelding, verklein hem en geef hem terug als data-URI.

    Waarom inbakken en niet gewoon linken: een gepubliceerd artifact draait
    onder een strikte CSP die elke externe host blokkeert. Een <img> naar
    cdn.fantasyfootballscout.co.uk laadt daar dus nooit — precies wat Justin
    zag: een leeg vlak met alleen de beginletter van de bron. Ingebakken als
    data-URI hoort het plaatje bij de pagina zelf en werkt het overal.

    Verkleinen is nodig omdat het anders te zwaar wordt: een artikelfoto is
    60-100 KB, en zestien daarvan in volle grootte maakt het bestand log.
    Op de breedte waarop ze getoond worden is dat 8-25 KB per stuk.
    """
    try:
        from PIL import Image
    except ImportError:
        return ""
    try:
        rauw = _OPENER.open(urllib.request.Request(url, headers=_UA), timeout=timeout).read()
        im = Image.open(io.BytesIO(rauw))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        bron_breedte = im.width
        # Een bron die kleiner is dan gevraagd wordt NIET opgeblazen: dat levert
        # alleen een vage foto op. De aanroeper krijgt de echte breedte terug en
        # kan daarmee besluiten hem niet als banner te gebruiken.
        if minimum and bron_breedte < minimum:
            return "", bron_breedte
        if im.width > breedte:
            im = im.resize((breedte, max(1, round(im.height * breedte / im.width))),
                           Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=kwaliteit, optimize=True, progressive=True)
        return "data:image/jpeg;base64." .replace(".", ",") + base64.b64encode(buf.getvalue()).decode(), bron_breedte
    except Exception:
        return "", 0


def parse(xml):
    """Kleine RSS-lezer. Geen externe pakketten nodig voor dit formaat."""
    uit = []
    for blok in re.findall(r"<item[ >].*?</item>", xml, re.S) or re.findall(r"<item>.*?</item>", xml, re.S):
        def veld(naam):
            m = re.search(r"<%s[^>]*>(.*?)</%s>" % (naam, naam), blok, re.S)
            if not m:
                return ""
            t = m.group(1)
            t = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", t, flags=re.S)
            t = re.sub(r"<[^>]+>", "", t)
            return html.unescape(t).strip()
        titel = veld("title")
        link = veld("link")
        if not titel or not link:
            continue
        # afbeelding: media:content, media:thumbnail, enclosure of een <img> in de tekst
        beeld = ""
        for patroon in (r'<media:content[^>]+url="([^"]+)"',
                        r'<media:thumbnail[^>]+url="([^"]+)"',
                        r'<enclosure[^>]+url="([^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"',
                        r'<img[^>]+src="([^"]+)"',
                        r'src=&quot;([^&]+\.(?:jpg|jpeg|png|webp)[^&]*)&quot;'):
            m2 = re.search(patroon, blok, re.I)
            if m2:
                beeld = html.unescape(m2.group(1))
                break
        uit.append({"titel": titel, "link": link, "beeld": beeld,
                    "samenvatting": veld("description")[:400],
                    "datum": veld("pubDate")})
    return uit


def og_beeld(url, timeout=12):
    """Haal het deelplaatje (og:image) van een artikel op.

    Nodig omdat de belangrijkste bron — Fantasy Football Scout — helemaal geen
    afbeelding in zijn feed zet. Elke site die op sociale media gedeeld wil
    worden heeft wel een og:image in de <head>, dus die pakken we daar. We
    lezen alleen het begin van de pagina; de <head> staat vooraan en de rest
    downloaden kost onnodig tijd.
    """
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) fpl-cockpit"})
        kop = urllib.request.urlopen(req, timeout=timeout).read(90000).decode("utf-8", "replace")
    except Exception:
        return ""
    for patroon in (r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
                    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
                    r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)'):
        m = re.search(patroon, kop, re.I)
        if m:
            u = html.unescape(m.group(1)).strip()
            if u.startswith("//"):
                u = "https:" + u
            if u.startswith("http"):
                return u
    return ""


def tijd(s):
    try:
        d = parsedate_to_datetime(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def koppel_spelers(tekst, spelers, volledig, mijn):
    """Welke spelers worden in deze tekst genoemd?

    Twee valkuilen die hier worden afgevangen:

    1. Deelwoorden. "Rice" zit in "prices", "Sane" in "insane". Daarom matchen
       we alleen op hele woorden.

    2. Namen die in een andere naam zitten. Arsenal heeft een verdediger die
       in FPL simpelweg "Gabriel" heet, en een aanvaller "Martinelli" wiens
       volledige naam Gabriel Martinelli is. Een bericht over Martinelli
       kreeg daardoor ook Gabriel als tag. Nu kijken we of de gevonden naam
       deel uitmaakt van de volledige naam van een ANDERE speler die eveneens
       in de tekst staat; zo ja, dan valt de korte match af.
    """
    tekst = " " + re.sub(r"\s+", " ", tekst) + " "
    ruw = []
    for sn, p in spelers.items():
        if len(sn) < 4:
            continue
        if re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(sn), tekst):
            ruw.append((sn, p))

    # Zoek naar stukken van een volledige naam die in de tekst staan. De hele
    # naam staat er zelden ("Gabriel Martinelli Silva"); wel een deel ervan
    # ("Gabriel Martinelli"). Daarom kijken we naar elke aaneengesloten reeks
    # van twee of meer woorden uit een volledige naam.
    stukken = []                      # (stuk uit de tekst, web_name van die speler)
    for vol, web in volledig:
        woorden = vol.split()
        if len(woorden) < 2:
            continue
        for i in range(len(woorden)):
            for j in range(i + 2, len(woorden) + 1):
                stuk = " ".join(woorden[i:j])
                if len(stuk) < 8:
                    continue
                if re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(stuk), tekst):
                    stukken.append((stuk, web))

    uit, gezien = [], set()
    for sn, p in ruw:
        # Staat deze korte naam binnen een langer naamstuk dat hier genoemd
        # wordt en bij een ANDERE speler hoort? Dan gaat het bericht niet over hem.
        # Dit is de Gabriel-Martinelli-val.
        vals = False
        for stuk, web in stukken:
            if web == sn:
                continue
            if re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(sn), " " + stuk + " "):
                vals = True
                break
        if vals or sn in gezien:
            continue
        gezien.add(sn)
        uit.append({"n": p["n"], "t": p["t"], "p": p["p"], "mijn": sn in mijn})
    return uit


def main():
    # Spelers uit het dashboard, plus hun volledige naam uit de FPL API.
    # Die volledige naam is nodig om een echte fout te voorkomen: "Gabriel"
    # zit letterlijk in "Gabriel Martinelli", maar dat is een andere speler.
    spelers, mijn = {}, set()
    volledig = []            # (genormaliseerde volledige naam, web_name)
    pad = os.path.join(STATE, "dashboard.html")
    if os.path.exists(pad):
        s = open(pad, encoding="utf-8").read()
        m = re.search(r"const D = (\{.*?\});\n", s, re.S)
        if m:
            D = json.loads(m.group(1))
            for p in D["db"]:
                if len(p["n"]) >= 4:
                    spelers[norm(p["n"]).strip()] = p
            mijn = {norm(p["n"]).strip() for p in D.get("squad", [])}
    try:
        bs = json.loads(haal("https://fantasy.premierleague.com/api/bootstrap-static/"))
        for e in bs["elements"]:
            vol = norm("%s %s" % (e.get("first_name", ""), e.get("second_name", ""))).strip()
            vol = re.sub(r"\s+", " ", vol)
            if vol:
                volledig.append((vol, norm(e["web_name"]).strip()))
    except Exception as e:
        print("waarschuwing: volledige namen niet opgehaald (%s)" % str(e)[:50], file=sys.stderr)

    nu = datetime.now(timezone.utc)
    berichten, mislukt = [], []
    for naam, url, soort in FEEDS:
        try:
            items = parse(haal(url))
            if not items:
                mislukt.append(f"{naam}: geen items")
                continue
            for it in items:
                t = tijd(it["datum"])
                if t and nu - t > timedelta(days=10):
                    continue
                tekst = norm(it["titel"] + " " + it["samenvatting"])
                if any(r in tekst for r in RUIS):
                    continue
                # Feeds die niet alleen over Engeland gaan moeten een Premier
                # League-haakje hebben, anders staat er zo Eredivisie tussen.
                if soort in ALLEEN_PL and not any(w in tekst for w in PL_WOORDEN):
                    continue

                score, redenen, genoemd = 0, [], []
                for w, punten in SIGNAAL.items():
                    if w in tekst:
                        score += punten
                        redenen.append(w)
                genoemd = koppel_spelers(tekst, spelers, volledig, mijn)
                for x in genoemd:
                    score += 12 if x["mijn"] else 4
                if soort == "fpl":
                    score += 4
                if t:
                    uren = (nu - t).total_seconds() / 3600
                    score += max(0, 8 - uren / 6)      # verser is belangrijker
                if score < 5:
                    continue
                berichten.append({
                    "titel": it["titel"], "link": it["link"], "beeld": it.get("beeld", ""),
                    "samenvatting": re.sub(r"\s+", " ", it["samenvatting"])[:260],
                    "bron": naam, "soort": soort,
                    "datum": t.isoformat() if t else None,
                    "score": round(score, 1),
                    "signalen": sorted(set(redenen))[:5],
                    "spelers": genoemd[:6],
                })
        except Exception as e:
            mislukt.append(f"{naam}: {str(e)[:70]}")

    # dubbele koppen weghalen (dezelfde gebeurtenis bij meerdere bronnen)
    gezien, uniek = set(), []
    for b in sorted(berichten, key=lambda x: -x["score"]):
        sleutel = norm(b["titel"])[:60]
        if sleutel in gezien:
            continue
        gezien.add(sleutel)
        uniek.append(b)

    # Voor de berichten die bovenaan komen: als de feed geen plaatje meegaf,
    # halen we het deelplaatje van het artikel zelf op. Alleen voor de top,
    # want elk artikel opvragen kost tijd en die van onderaan zie je toch niet
    # als kaart met beeld.
    zonder = [b for b in uniek[:16] if not b.get("beeld")]
    gehaald = 0
    for b in zonder:
        u = og_beeld(b["link"])
        if u:
            b["beeld"] = u
            gehaald += 1

    # De plaatjes inbakken. De eerste krijgt de hero-breedte, de vier daarna de
    # kaartbreedte, de rest een miniatuur. Wat niet lukt valt terug op de
    # beginletter van de bron, zoals eerder.
    # De banner bovenaan is groot: op een scherm met hoge pixeldichtheid staat
    # hij op ruim 1200 echte beeldpunten. De vorige versie vroeg 760 en blies
    # bronnen die kleiner waren gewoon op — de banner van Haaland kwam uit een
    # bestand van 240 pixels breed. Daarom nu: eerst kijken hoe groot de bron
    # ECHT is, en pas daarna beslissen wie de banner wordt.
    HERO_MIN = 900          # smaller dan dit is geen banner waard
    ingebakken, bytes_totaal = 0, 0
    metingen = []
    for i, b in enumerate(uniek[:16]):
        if not b.get("beeld", "").startswith("http"):
            metingen.append((i, 0))
            continue
        try:
            rauw = _OPENER.open(urllib.request.Request(b["beeld"], headers=_UA), timeout=12).read()
            from PIL import Image as _I
            metingen.append((i, _I.open(io.BytesIO(rauw)).width))
        except Exception:
            metingen.append((i, 0))
    scherp = [i for i, w in metingen if w >= HERO_MIN]
    hero_i = scherp[0] if scherp else None
    for i, b in enumerate(uniek[:16]):
        if not b.get("beeld", "").startswith("http"):
            continue
        if i == hero_i:
            breedte, kwal = 1500, 84
        elif i <= 4:
            breedte, kwal = 960, 80
        else:
            breedte, kwal = 340, 78
        ondergrens = 900 if i == hero_i else (420 if i <= 4 else 200)
        d, bron_w = beeld_inbakken(b["beeld"], breedte, kwaliteit=kwal, minimum=ondergrens)
        if d:
            b["beeld_bron"] = b["beeld"]
            b["beeld"] = d
            b["beeld_breedte"] = min(breedte, bron_w)
            b["beeld_scherp"] = bron_w >= HERO_MIN
            ingebakken += 1
            bytes_totaal += len(d)
    # De banner moet vooraan staan; als een scherpe foto verderop zit, schuift
    # dat bericht naar voren in plaats van dat er een vage banner blijft staan.
    if hero_i:
        uniek.insert(0, uniek.pop(hero_i))
    # de rest houdt geen plaatje: een externe URL werkt toch niet in een artifact
    for b in uniek:
        if b.get("beeld", "").startswith("http"):
            b["beeld_bron"] = b["beeld"]
            b["beeld"] = ""

    data = {
        "_bron": "publieke RSS-feeds",
        "_beeld": ("Uit de feed waar die het meegeeft, aangevuld met de og:image van "
                   "het artikel zelf. De afbeeldingen zijn verkleind en in de pagina "
                   "ingebakken als data-URI, omdat een gepubliceerd artifact geen "
                   "externe hosts mag laden. "
                   f"Deze keer {gehaald} extra gevonden, {ingebakken} ingebakken "
                   f"({bytes_totaal // 1024} KB)."),
        "_feeds": [{"naam": n, "url": u, "soort": s} for n, u, s in FEEDS],
        "_opgehaald": nu.isoformat(timespec="seconds"),
        "_methode": ("Berichten uit de laatste 10 dagen, gescoord op blessure- en "
                     "opstellingswoorden, op vermelde spelers (zwaarder als ze in de "
                     "selectie zitten) en op versheid. Onder de 5 punten valt af."),
        "_mislukt": mislukt,
        "_aantal": len(uniek),
        "berichten": uniek[:60],
    }
    with open(os.path.join(STATE, "nieuws.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"nieuws.json: {len(uniek)} berichten uit {len(FEEDS) - len(mislukt)} feeds")
    if mislukt:
        print("  mislukt:", "; ".join(mislukt))
    for b in uniek[:6]:
        sp = ", ".join(x["n"] for x in b["spelers"][:3])
        print(f"  [{b['score']:5.1f}] {b['bron']:22s} {b['titel'][:62]}"
              + (f"  ← {sp}" if sp else ""))


if __name__ == "__main__":
    main()
