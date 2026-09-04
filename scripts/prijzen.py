#!/usr/bin/env python3
"""Aankoopprijs, verkoopprijs en squadwaarde — de drie getallen die door elkaar liepen.

Waarom dit bestaat
------------------
Het dashboard rekende overal met de HUIDIGE prijs van een speler. Dat is precies
één van de drie bedragen die ertoe doen, en meestal niet degene die je nodig hebt.
Gevolg: het meldde dat Justin geld tekortkwam terwijl FPL zelf £1,1m over toonde.

De drie bedragen, en wanneer je welke gebruikt:

  AANKOOPPRIJS   wat je ooit betaald hebt. Verandert nooit.
  HUIDIGE PRIJS  wat de speler nu kost. Dit betaal je als je hem KOOPT.
  VERKOOPPRIJS   wat je terugkrijgt als je hem VERKOOPT.

De verkoopprijs is de lastige. Stijgt een speler, dan houd je maar de HELFT van
die stijging, naar beneden afgerond op £0,1m. Daalt hij, dan draag je de hele
daling zelf. Dus:

  gekocht £5,0m, nu £5,3m  ->  verkoop £5,1m   (helft van 0,3 = 0,15 -> 0,1)
  gekocht £5,0m, nu £5,4m  ->  verkoop £5,2m   (helft van 0,4 = 0,2)
  gekocht £5,0m, nu £4,8m  ->  verkoop £4,8m   (dalingen tellen voluit)

Je SQUADWAARDE is de som van de verkoopprijzen, niet van de huidige prijzen. En
je budget is squadwaarde plus bank. Bij een wildcard tellen spelers die je HOUDT
mee tegen hun verkoopprijs, en spelers die je erbij haalt tegen de huidige prijs.

Dat verschil — tussen wat je selectie waard is en wat diezelfde spelers vandaag
kosten — heet hier de overwaarde. Die is onzichtbaar in de FPL-interface en is
precies waar het misging.

Alles rekent in TIENDEN van een miljoen (dus 55 = £5,5m), net als de FPL API.
Zo blijft afronden exact; met kommagetallen krijg je 5,1000000001.
"""


def verkoopprijs(gekocht, nu):
    """Wat FPL je teruggeeft voor een speler. Beide in tienden van een miljoen."""
    if gekocht is None:
        return nu
    if nu <= gekocht:
        return nu                       # dalingen draag je zelf, volledig
    winst = nu - gekocht
    return gekocht + winst // 2         # helft, naar beneden op £0,1m


def overwaarde(gekocht, nu):
    """Het deel van de stijging dat je NIET terugkrijgt."""
    return max(0, nu - verkoopprijs(gekocht, nu))


def squadwaarde(spelers):
    """Som van de verkoopprijzen. Dit is het getal dat FPL 'Squad Value' noemt.

    `spelers` is een reeks (gekocht, nu) in tienden.
    """
    return sum(verkoopprijs(g, n) for g, n in spelers)


def budget(spelers, bank):
    """Wat je te besteden hebt: squadwaarde plus bank."""
    return squadwaarde(spelers) + bank


def kosten_van_draft(draft, in_bezit):
    """Wat een wildcard-selectie kost.

    Spelers die je al hebt tellen tegen hun VERKOOPPRIJS — je houdt ze immers,
    dus er verandert niets aan je vermogen. Spelers die je erbij haalt kosten de
    HUIDIGE prijs. Dat onderscheid is precies wat het dashboard niet maakte.

    `draft`     is een reeks (id, huidige_prijs)
    `in_bezit`  is {id: (gekocht, nu)} van wat je nu hebt
    """
    tot = 0
    for pid, nu in draft:
        if pid in in_bezit:
            g, n = in_bezit[pid]
            tot += verkoopprijs(g, n)
        else:
            tot += nu
    return tot


def uitleg(gekocht, nu):
    """Eén regel die uitlegt waar de verkoopprijs vandaan komt."""
    if gekocht is None:
        return "aankoopprijs onbekend; gerekend met de huidige prijs"
    v = verkoopprijs(gekocht, nu)
    if nu == gekocht:
        return "gekocht voor £%.1fm, prijs onveranderd" % (gekocht / 10)
    if nu < gekocht:
        return ("gekocht voor £%.1fm, nu £%.1fm — een daling draag je voluit, "
                "je krijgt £%.1fm terug" % (gekocht / 10, nu / 10, v / 10))
    return ("gekocht voor £%.1fm, nu £%.1fm — van de £%.1fm stijging houd je de "
            "helft, dus je krijgt £%.1fm terug (£%.1fm overwaarde die je niet "
            "kunt uitgeven)" % (gekocht / 10, nu / 10, (nu - gekocht) / 10,
                                v / 10, (nu - v) / 10))


if __name__ == "__main__":
    proeven = [(50, 53, 51), (50, 54, 52), (50, 48, 48), (50, 50, 50),
               (155, 155, 155), (96, 99, 97), (40, 45, 42)]
    print("zelfcontrole van de verkoopregel:")
    goed = True
    for g, n, verwacht in proeven:
        v = verkoopprijs(g, n)
        ok = v == verwacht
        goed &= ok
        print("  gekocht £%.1f  nu £%.1f  ->  verkoop £%.1f  %s"
              % (g / 10, n / 10, v / 10, "ok" if ok else "FOUT, verwacht £%.1f" % (verwacht / 10)))
    print("alle proeven kloppen" if goed else "ER ZIT EEN FOUT IN DE REGEL")
