#!/usr/bin/env python3
"""Leidt doelpuntverwachtingen en clean sheet-kansen af uit bookmakersodds.

Waarom dit beter is dan de vorige berekening
--------------------------------------------
Tot nu toe kwamen verwachte goals en clean sheet-kansen uit een Poisson-model
op de xGF/xGA van vorig seizoen. Dat weet niets van blessures, transfers, vorm
of opstellingsnieuws van deze week. De bookmakersmarkt weet dat wel, want daar
staat geld tegenover en de prijzen bewegen tot het aftrappen.

De rekenmethode
---------------
1. De drie 1X2-odds omzetten naar impliciete kansen (1/odd). Die tellen op tot
   meer dan 1: het verschil is de marge van de bookmaker. Die haal ik eruit met
   de gebruikelijke proportionele methode, zodat de kansen precies op 1 uitkomen.

2. Vervolgens zoek ik de twee doelpuntverwachtingen (lambda thuis, lambda uit)
   waarbij een Poisson-model exact die drie kansen oplevert. Drie kansen waarvan
   er twee onafhankelijk zijn, en twee onbekenden: het stelsel is precies bepaald.
   Er is dus geen over/under-markt nodig, wat maar goed is ook, want die pagina's
   waren niet betrouwbaar uit te lezen.

3. Uit die twee getallen volgt alles wat FPL nodig heeft:
       clean sheet thuis = P(uitploeg scoort nul) = e^(-lambda_uit)
       clean sheet uit   = e^(-lambda_thuis)
       verwachte goals   = lambda zelf

De aannames, expliciet
----------------------
* Doelpunten volgen een Poisson-verdeling en beide ploegen scoren onafhankelijk.
  Dat is het standaardmodel in de literatuur. Het onderschat licht het aantal
  0-0's en 1-1's; Dixon-Coles corrigeert daarvoor met een extra parameter, maar
  die valt niet uit 1X2 alleen te schatten zonder nog een markt erbij.
* De marge van de bookmaker verdeel ik proportioneel over de drie uitkomsten.
  Er bestaan verfijndere methodes (Shin, logaritmisch), die in de praktijk
  hooguit een fractie schelen bij deze marges.
* Ik gebruik het gemiddelde van de bookmakers dat OddsPortal toont, niet een
  enkele bookmaker.

Alles wat hier uitkomt is dus herleidbaar tot een marktprijs plus deze twee
aannames. Dat is een wezenlijk andere basis dan een schatting uit mijn eigen model.

Gebruik:  python3 odds_verwerk.py
"""
import json
import math
import os
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HIER, "state")


def poisson(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def uitkomstkansen(lh, la, maxg=12):
    """P(thuiswinst), P(gelijk), P(uitwinst) bij twee onafhankelijke Poissons."""
    th = np = ui = 0.0
    ph = [poisson(i, lh) for i in range(maxg)]
    pa = [poisson(j, la) for j in range(maxg)]
    for i in range(maxg):
        for j in range(maxg):
            p = ph[i] * pa[j]
            if i > j:
                th += p
            elif i == j:
                np += p
            else:
                ui += p
    return th, np, ui


def los_op(ph, pd, pa):
    """Zoek lambda's die precies deze drie kansen opleveren.

    Twee geneste bisecties. De buitenste stelt het totaal aantal goals in op de
    juiste gelijkspelkans, de binnenste verdeelt dat totaal over thuis en uit tot
    de thuiswinstkans klopt. Beide functies zijn monotoon, dus dit convergeert.
    """
    def verdeel(totaal):
        # zoek de verdeling die ph reproduceert bij dit totaal
        lo, hi = 0.01, totaal - 0.01
        for _ in range(80):
            mid = (lo + hi) / 2
            th, _, _ = uitkomstkansen(mid, totaal - mid)
            if th < ph:
                lo = mid
            else:
                hi = mid
        lh = (lo + hi) / 2
        return lh, totaal - lh

    lo, hi = 0.30, 8.0
    for _ in range(80):
        tot = (lo + hi) / 2
        lh, la = verdeel(tot)
        _, gelijk, _ = uitkomstkansen(lh, la)
        # meer goals betekent minder kans op gelijkspel
        if gelijk > pd:
            lo = tot
        else:
            hi = tot
    tot = (lo + hi) / 2
    return verdeel(tot)


def main():
    pad = os.path.join(STATE, "odds_ruw.json")
    if not os.path.exists(pad):
        sys.exit("state/odds_ruw.json ontbreekt — draai eerst: node odds_ophalen.js")
    ruw = json.load(open(pad))

    uit = {}
    controle = []
    for d in ruw["duels"]:
        o = d["odds"]
        rauw = [1 / o["thuis"], 1 / o["gelijk"], 1 / o["uit"]]
        marge = sum(rauw) - 1.0
        ph, pd, pa = [x / sum(rauw) for x in rauw]
        lh, la = los_op(ph, pd, pa)

        # controle: reproduceert het model de invoer?
        th, gl, ut = uitkomstkansen(lh, la)
        afw = max(abs(th - ph), abs(gl - pd), abs(ut - pa))
        controle.append(afw)

        rij = {
            "thuis": d["thuis"], "uit": d["uit"], "tijd": d.get("tijd"),
            "odds": o, "marge": round(marge * 100, 2),
            "kans": {"thuis": round(ph * 100, 1), "gelijk": round(pd * 100, 1),
                     "uit": round(pa * 100, 1)},
            "xg": {"thuis": round(lh, 3), "uit": round(la, 3)},
            "cs": {"thuis": round(math.exp(-la) * 100, 1),
                   "uit": round(math.exp(-lh) * 100, 1)},
            "afwijking": round(afw, 5),
        }
        uit.setdefault(d["thuis"], []).append({**rij, "thuisduel": True})
        uit.setdefault(d["uit"], []).append({**rij, "thuisduel": False})

    data = {
        "_bron": ruw["_bron"], "_markt": ruw["_markt"], "_opgehaald": ruw["_opgehaald"],
        "_methode": ("1X2-odds ontdaan van de bookmakersmarge (proportioneel), daarna "
                     "de twee doelpuntverwachtingen exact opgelost zodat een Poisson-model "
                     "diezelfde drie kansen oplevert. Clean sheet volgt als e^(-lambda) van "
                     "de tegenstander."),
        "_aannames": ["Poisson-verdeling per ploeg", "beide ploegen scoren onafhankelijk",
                      "marge proportioneel verdeeld over de drie uitkomsten"],
        "_grootste_afwijking": round(max(controle), 5) if controle else None,
        "_duels": len(ruw["duels"]),
        "per_club": uit,
    }
    with open(os.path.join(STATE, "odds.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    print(f"odds.json: {len(ruw['duels'])} duels, {len(uit)} clubs")
    print(f"  grootste afwijking tussen model en marktkans: {max(controle):.2e}")
    print(f"  {'duel':16s} {'xG thuis':>9s} {'xG uit':>7s} {'CS thuis':>9s} {'CS uit':>7s}  marge")
    for club, lijst in sorted(uit.items()):
        for r in lijst:
            if r["thuisduel"]:
                print(f"  {r['thuis']}-{r['uit']:12s} {r['xg']['thuis']:9.2f} {r['xg']['uit']:7.2f} "
                      f"{r['cs']['thuis']:8.1f}% {r['cs']['uit']:6.1f}%  {r['marge']:.1f}%")


if __name__ == "__main__":
    main()
