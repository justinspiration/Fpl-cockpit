# FPL Cockpit — wat dit is en hoe je ermee werkt

Dit bestand is bedoeld om te lezen aan het begin van een nieuw gesprek, zodat je
niet opnieuw hoeft uit te leggen wat er is en waarom. Het staat in de repository
zodat het meeverhuist naar elke machine.

Bijgewerkt: 11 september 2026, gameweek 4.

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
- Site: `https://justinspiration.github.io/Fpl-cockpit/` (hoofdletter F; de
  kleine letter geeft 404). GitHub Pages publiceert bij elke push.
- De Action draait op minuut 13 en 43 van elk uur; op het hele uur werden
  rondes massaal overgeslagen. Eerste bouw duurt ~7 minuten (chipteams),
  de tweede hergebruikt `state/zwaar_cache.json`. Een mislukte uitkomst
  wordt niet gecachet.
- Odds wachten op `ODDS_API_KEY`; zonder sleutel rekent GoalIQ + eigen model.

## Wat nog open staat

1. `ODDS_API_KEY` aanvragen en in GitHub Secrets zetten
2. Netlify mag uit: alleen `netlify/functions/chat.mjs` (taalmodel-assistent)
   draait daar, en die werkt vanaf GitHub Pages toch niet (`/api/chat`
   bestaat daar niet). Wil je de assistent met taalmodel, dan moet die
   functie ergens anders komen te staan (Cloudflare Worker o.i.d.).
3. Supabase: Site URL en Redirect URLs op het GitHub Pages-adres, zie
   `INLOGGEN-OPZETTEN.md` stap 3. Anders komt de inloglink op Netlify uit.

## Werken aan de pagina

De pagina is `scripts/dashboard_template.html`; `index.html` wordt daaruit
gebouwd en overschreven door de Action. Wijzig dus altijd het sjabloon.
Lokaal bouwen zonder bronnen op te halen:

```
cd scripts
python -c "import json,dashboard as d;print(open('../index.html','w',encoding='utf-8').write('<!doctype html>\n'+d.render_web(json.load(open('../data/dashboard.json',encoding='utf-8')))))"
```

Volledige bouw met ophalen: `python dashboard.py --web ..` (Windows werkt
sinds september; bestanden worden expliciet als UTF-8 gelezen). Let op
`core.autocrlf` op Windows: na een rebase staan werkbestanden op CRLF.
Patchscripts moeten daar tegen kunnen (`.replace(b"\r\n", b"\n")`).

Hoe het sjabloon in elkaar zit sinds september 2026:

- Zeven pagina's (`VLAKKEN`); Seizoensplan en Prijzen zijn onderdelen van
  Transfers, Fixtures van Research (`SUBTABS`). Oude sleutels werken via
  `OUDE_VLAKKEN`. Elke pagina heeft een icoon (`VLAKIKOON`) en kleur
  (`VLAKKLEUR`) -- die laatste wijst naar bestaande tokens, niet naar
  `--t-<paginanaam>`.
- Spelersfoto's: `spelerFoto(p, maat)` bouwt de URL uit `p.code` (nieuw
  veld uit de FPL API). Actuele reeks:
  `resources.premierleague.com/premierleague25/photos/players/{110x140|500x500}/<code>.png`
  (server-datum augustus 2026). De oude reeks `premierleague/.../p<code>.png`
  is van 2024 (oude shirts) en is alleen terugval. `fotoFout()` loopt de
  keten af; `portret()` en `clubTegel()` zijn de bouwstenen.
- Een geplande transfer legt `c_in`/`c_uit`/`op` vast; `budgetIn()` rekent
  daarmee, niet met de prijs van vandaag.
- Transfers: `transferRoutes(g,N)` bouwt complete routes (één, twee,
  gespreid, geld vrij); winst = beste elf ná min beste elf vóór, aanvoerder
  en al gepland strafwerk inbegrepen. `UITSLUIT` (localStorage,
  gesynchroniseerd) haalt spelers uit alle routes.
- Assistent "Wat nu": `beslissingen(G)` vóór de signalen van `analyseer()`.
- Terugblik: league-gemiddelde en hoogste per gameweek komen uit
  `D.archief.gameweeks` (echte punten van alle managers), nooit geschat.
- Vergelijken: standaard het lopende seizoen zodra er drie ronden zijn;
  xG dit seizoen is FPL's `sxg`, vorig seizoen Opta. Ondergrens per-90:
  twee duels.
- Stijl: alles nieuws staat in de "VISUELE LAAG"-blokken onderaan het
  stijlblok, in volgorde. Nieuwe pagina's krijgen daar hun eigen blok.
