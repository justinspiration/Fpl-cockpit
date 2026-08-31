#!/usr/bin/env python3
"""Meerdere bronnen per cijfer, gemiddeld, met herkomst per getal.

Waarom dit bestaat
------------------
Eén bron is een mening. Twee bronnen die het eens zijn is bevestiging. Twee die
het oneens zijn is informatie — vaak de nuttigste die er is, want daar zit het
echte meningsverschil (meestal over speelminuten, zelden over kwaliteit).

Het gevaar van middelen is stille vergiftiging. Op 31-08-2026 bleek FPL Prophet
een compleet seizoen achter te lopen: hun `gw` stond op 38, Haaland kostte er
£14,7m en hun speler-id's waren die van 25/26. Zonder controle was dat gewoon
meegemiddeld en had niemand het gezien. Daarom mag hier GEEN bron meedoen die
niet eerst een keuring doorstaat.

Drie regels
-----------
1. Elke bron levert een `keur()` die tegen de FPL API zelf ijkt. Zakt hij, dan
   doet hij niet mee en staat de reden in het dashboard.
2. Elke bron wordt op schaal gebracht voordat hij meetelt. Modellen liggen
   structureel hoger of lager; ongecorrigeerd middelen vergelijkt appels met peren.
3. Elk gemengd getal draagt zijn opbouw mee. Geen cijfer zonder herkomst.
"""
import json, os, time

HIER = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HIER, "state")

# Hoe oud mag een bron zijn voordat we hem wantrouwen? Prijzen en blessures
# verschuiven dagelijks; een projectie van vorige week is geen projectie meer.
MAX_LEEFTIJD_UUR = 36.0


class Keuring:
    """Uitkomst van de controle op één bron."""

    def __init__(self, naam, ok, reden="", leeftijd_uur=None, dekking=0):
        self.naam = naam
        self.ok = bool(ok)
        self.reden = reden
        self.leeftijd_uur = leeftijd_uur
        self.dekking = dekking

    def dict(self):
        return {"bron": self.naam, "ok": self.ok, "reden": self.reden,
                "leeftijd_uur": (round(self.leeftijd_uur, 1)
                                 if self.leeftijd_uur is not None else None),
                "dekking": self.dekking}

    def __repr__(self):
        return "<%s %s %s>" % (self.naam, "OK" if self.ok else "GEWEIGERD", self.reden)


def _leeftijd_uur(stempel):
    """Uren sinds een tijdstempel. None als het onleesbaar is."""
    if not stempel:
        return None
    for vorm in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return (time.time() - time.mktime(time.strptime(str(stempel)[:19], vorm))) / 3600.0
        except (ValueError, OverflowError):
            continue
    return None


def keur_spelerbron(naam, data, bootstrap, id_veld="spelers", prijs_van=None,
                    min_dekking=300, max_leeftijd=MAX_LEEFTIJD_UUR):
    """Controleert een spelersbron tegen de LEVENDE FPL API.

    Twee dingen die echt fout kunnen gaan, en die je alleen zo vangt:
      * de bron loopt een seizoen achter (verkeerde id's, oude prijzen)
      * de bron is dagen oud en weet niets van deze week

    `prijs_van(rij)` geeft de prijs die de bron zelf noemt, in tienden van een
    miljoen, of None als de bron geen prijs meelevert.
    """
    if not data or not data.get(id_veld):
        return Keuring(naam, False, "geen data")

    rijen = data[id_veld]
    leeftijd = _leeftijd_uur(data.get("opgehaald") or data.get("generated_at"))
    if leeftijd is not None and leeftijd > max_leeftijd:
        return Keuring(naam, False, "%.0f uur oud (grens %.0f)" % (leeftijd, max_leeftijd),
                       leeftijd, len(rijen))

    fpl = {e["id"]: e for e in bootstrap["elements"]}
    treffers = naam_ok = prijs_ok = prijs_getest = 0
    for sleutel, rij in (rijen.items() if isinstance(rijen, dict)
                         else enumerate(rijen)):
        try:
            pid = int(sleutel)
        except (TypeError, ValueError):
            continue
        e = fpl.get(pid)
        if not e:
            continue
        treffers += 1
        bron_naam = (rij.get("n") or rij.get("web_name") or "") if isinstance(rij, dict) else ""
        if bron_naam and _lijkt(bron_naam, e["web_name"]):
            naam_ok += 1
        if prijs_van:
            p = prijs_van(rij)
            if p:
                prijs_getest += 1
                if abs(int(p) - e["now_cost"]) <= 2:      # £0,2m speling
                    prijs_ok += 1

    if treffers < min_dekking:
        return Keuring(naam, False, "slechts %d spelers herkend (minimaal %d)"
                       % (treffers, min_dekking), leeftijd, treffers)
    # De Prophet-test: kloppen de namen bij de id's? Zo niet, dan is de bron van
    # een ander seizoen en betekent elk getal iets anders dan wij denken.
    if treffers >= 20 and naam_ok / max(1, treffers) < 0.80:
        return Keuring(naam, False,
                       "naam hoort bij %d%% van de id's — bron gebruikt andere id's "
                       "(vermoedelijk een ander seizoen)" % round(100 * naam_ok / treffers),
                       leeftijd, treffers)
    if prijs_getest >= 20 and prijs_ok / prijs_getest < 0.70:
        return Keuring(naam, False,
                       "prijzen wijken af bij %d%% van de spelers — verouderde momentopname"
                       % round(100 * (1 - prijs_ok / prijs_getest)), leeftijd, treffers)
    return Keuring(naam, True, "", leeftijd, treffers)


def _lijkt(a, b):
    """Ruwe naamvergelijking: accenten en puntjes mogen verschillen."""
    import unicodedata

    def plat(s):
        s = unicodedata.normalize("NFKD", str(s))
        return "".join(c for c in s if c.isalnum()).lower()
    a, b = plat(a), plat(b)
    return bool(a) and bool(b) and (a == b or a in b or b in a)


def schaal(paren, grens=(0.5, 2.0), minimum_n=10):
    """Verhouding tussen twee bronnen, gemeten op wat ze allebei kennen.

    `paren` is een lijst (referentie, andere). Geeft de factor waarmee `andere`
    vermenigvuldigd moet worden om op het niveau van `referentie` te komen.
    """
    a = sum(x for x, _ in paren)
    b = sum(y for _, y in paren)
    if len(paren) < minimum_n or b <= 0:
        return 1.0
    return max(grens[0], min(grens[1], a / b))


def meng(waarden, gewichten=None, afronding=2):
    """Middelt bronnen en geeft de opbouw terug.

    `waarden` is {bronnaam: getal}, al op schaal gebracht. Het resultaat draagt
    altijd zijn eigen herkomst mee, zodat het dashboard kan tonen waar een
    cijfer vandaan komt zonder ernaar te hoeven raden.
    """
    schoon = {k: float(v) for k, v in (waarden or {}).items() if v is not None}
    if not schoon:
        return {"waarde": None, "bronnen": {}, "n": 0, "spreiding": None}
    g = {k: float((gewichten or {}).get(k, 1.0)) for k in schoon}
    som = sum(g.values()) or 1.0
    uit = sum(schoon[k] * g[k] for k in schoon) / som
    lo, hi = min(schoon.values()), max(schoon.values())
    spreiding = (hi - lo) / hi if hi > 0 else 0.0
    return {"waarde": round(uit, afronding),
            "bronnen": {k: round(v, afronding) for k, v in schoon.items()},
            "n": len(schoon),
            "spreiding": round(spreiding, 3)}


def strijd(gemengd, drempel=0.30):
    """Eén regel uitleg als de bronnen het echt oneens zijn, anders None."""
    if not gemengd or gemengd["n"] < 2 or (gemengd["spreiding"] or 0) < drempel:
        return None
    b = gemengd["bronnen"]
    lo = min(b, key=b.get)
    hi = max(b, key=b.get)
    return "%d%% uit elkaar (%s %.1f tegen %s %.1f)" % (
        round(100 * gemengd["spreiding"]), lo, b[lo], hi, b[hi])
