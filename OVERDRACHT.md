# FPL Cockpit — wat dit is en hoe je ermee werkt

Dit bestand is bedoeld om te lezen aan het begin van een nieuw gesprek, zodat je
niet opnieuw hoeft uit te leggen wat er is en waarom. Het staat in de repository
zodat het meeverhuist naar elke machine.

Bijgewerkt: 10 september 2026, gameweek 4.

---

## Waar het om gaat

Justin verdedigt zijn mini-league titel (10 managers, prijzenpot ~€250) en jaagt
op een zo hoog mogelijke wereldrang. Vorig seizoen: 2352 punten, wereldrang
49.961, eerste in de league.

Alles draait om één dashboard dat elk uur verse data ophaalt en daar advies uit
afleidt.

## De harde regels

Deze staan niet ter discussie; ze zijn er gekomen na fouten die geld hadden
kunnen kosten.

1. **Nul verzonnen data.** Elk cijfer heeft een bron en een ophaalmoment. Kun je
   iets niet ophalen, dan zeg je dat — je gokt niet.
2. **Meerdere bronnen per cijfer**, gemiddeld, met de opbouw zichtbaar bij hover.
   Eén bron is een mening.
3. **Elke bron wordt gekeurd** tegen de levende FPL API voordat hij meetelt:
   kloppen de speler-id's, kloppen de prijzen, hoe oud is hij. Dit is niet
   theoretisch — FPL Prophet bleek een heel seizoen achter te lopen en zou
   stilzwijgend meegemiddeld zijn.
4. **Geen sleutels in de repository.** Die is publiek. API-sleutels horen in
   GitHub Secrets of als omgevingsvariabele; Justin zet ze zelf, ik zie ze nooit.
5. Nederlands, getallen boven tien als cijfer, geen opgeklopte taal.

## Hoe het in elkaar zit

```
GitHub Action (elk uur)
   |
   +-- haalt op: Copilot, Pundit, Estimator, GoalIQ, odds, wedstrijdhistorie
   +-- bouwt:    data/dashboard.json  +  index.html
   +-- commit naar de repository
                 |
                 v
   de pagina haalt data/dashboard.json RECHTSTREEKS bij GitHub op
   (raw.githubusercontent.com, CORS staat open, cache 5 minuten)
```

Waarom die laatste stap: de hostingpartij hoeft dan niets te bouwen bij een
datawijziging. Dat scheelde ruim vierduizend Netlify-credits per maand.

## De bronnen, en wat je van ze moet weten

| Bron | Wat | Eigenaardigheid |
|---|---|---|
| FPL API | prijzen, punten, minuten, xG dit seizoen | leidend, altijd waar |
| FPL Copilot | verwachte punten per gameweek | ververst niet dagelijks; tussen gameweeks staat hij stil |
| Fantasy Football Pundit | punten per gameweek + startkans apart | JSON zit in de HTML, geen browser nodig |
| FPL Estimator | totaal over 5 gameweeks | via Chrome, 20 spelers per klik |
| GoalIQ | xG en clean sheet per duel | Dixon-Coles op Understat |
| the-odds-api.com | bookmakersodds | API, 500 gratis credits/maand, 2x per dag ophalen |

**Scrapen van odds werkt niet meer.** Oddschecker geeft 403 met bot-controle,
OddsPortal serveert alleen advertenties, ook in een echte browser met de
cookiemuur afgehandeld. Vandaar de API.

## Valkuilen die al een keer zijn misgegaan

- **Seizoenswissel**: FPL overschrijft historische velden zodra GW1 klaar is.
  Wie op de rauwe velden bouwt, kijkt naar cijfers die stil van betekenis zijn
  veranderd. Zie `_historie()` in `dashboard.py`.
- **Opgehaald is niet gewijzigd**: een bron kan elke ronde netjes opgehaald
  worden terwijl de cijfers al dagen stilstaan. Er zit nu een vingerafdruk over
  de inhoud (`_inhoud_gewijzigd`).
- **Tijdzones**: alle stempels staan in UTC. `time.mktime` leest ze als lokale
  tijd; gebruik `calendar.timegm`.
- **Verkoopprijs is niet de huidige prijs.** Van een koersstijging houd je de
  helft, naar beneden afgerond op £0,1m. Zie `prijzen.py`.
- **De GitHub-webuploader laat mappen die met een punt beginnen vallen.**
  `.github/workflows/` moet je via "Create new file" of de editor plaatsen.

## De bestanden

| | |
|---|---|
| `scripts/dashboard.py` | bouwt alles; het hart |
| `scripts/dashboard_template.html` | de hele pagina, ruim 14.000 regels |
| `scripts/bronnen.py` | keuring en menging van bronnen |
| `scripts/prijzen.py` | aankoop-, verkoop- en squadwaarde |
| `scripts/awards.py` | awards per gameweek, bewaard voor het seizoen |
| `scripts/*_ophalen.*` | één per bron |
| `.github/workflows/ververs.yml` | de keten, elk uur |

## Draaien op Windows

Nodig: Python 3.9+, Node 18+, Chrome of Edge.

```
git clone https://github.com/justinspiration/Fpl-cockpit
cd Fpl-cockpit/scripts
python -m pip install pillow
python dashboard.py --web ..
```

Chrome wordt automatisch gevonden, ook op Windows en ook als je alleen Edge hebt.
Lukt dat niet, zet dan `CHROME_PAD` naar het pad van je browser.

## Waar het nu staat

- Gameweek 4, deadline zaterdag 12 september 14:30
- 196 punten, wereldrang 2.764.150, teamwaarde £100,5m
- Vier van de vijf bronnen vers; odds wachten op een API-sleutel
- Awards en terugblik lopen vanaf GW1

## Wat nog open staat

1. `ODDS_API_KEY` aanvragen en in GitHub Secrets zetten
2. Hosting: Netlify-credits zijn op tot 19 september; GitHub Pages is gratis en
   werkt meteen (Settings > Pages > main / root)
