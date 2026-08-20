# De assistent laten praten

De knop **Assistent** linksonder werkt al zonder dat je iets doet. Hij gebruikt
dan de patroonmotor die in de pagina zit: die kent alle 587 spelers, hun
projecties, de odds per duel, blessures, jouw vijftien en je mini-league, en
beantwoordt vragen over aanvoerders, vergelijken, chips, kopen en verkopen.
Wat hij niet kan is een vrij gesprek voeren.

Wil je dat wel, dan zet je er een taalmodel achter. Dat is één instelling.

## Waarom de sleutel niet in de pagina mag

Een API-sleutel in de HTML is een sleutel die iedereen kan lezen die de pagina
opent — en die dan op jouw rekening kan praten. Daarom staat hij op de server
van Netlify, in een omgevingsvariabele. De pagina stuurt je vraag naar
`/api/chat`, die functie praat met Anthropic en stuurt alleen het antwoord
terug. De sleutel komt nooit in je browser.

Zolang er geen sleutel staat, geeft de functie netjes niets terug en valt de
chat terug op de patroonmotor. Er gaat dus niets stuk als je dit overslaat.

## Een sleutel aanmaken

1. Ga naar **console.anthropic.com** en log in of maak een account.
2. Linksonder op je naam → **API keys**, of direct via Settings → API keys.
3. **Create key**, geef hem een naam (bijvoorbeeld `fpl-cockpit`) en kopieer
   de sleutel. Hij begint met `sk-ant-`.
   Bewaar hem meteen ergens veilig: na dit scherm laat Anthropic hem niet
   nog een keer zien.
4. Onder **Billing** zet je een bedrag klaar. Zonder tegoed geeft de API een
   foutmelding en blijft de chat op de patroonmotor draaien.

Wat het ongeveer kost: een vraag met jouw teamcontext erbij is een paar duizend
tokens. Met Sonnet kom je daarmee op ongeveer een cent per vraag. Honderd vragen
per maand is dus rond een euro. Zet in de console een maandlimiet als je zeker
wilt weten dat het niet oploopt.

## De sleutel in Netlify zetten

1. Open je site op **app.netlify.com**.
2. **Site configuration** → **Environment variables** → **Add a variable**.
3. Key: `ANTHROPIC_API_KEY`
   Value: je sleutel (`sk-ant-...`)
   Scopes: laat op alle staan.
4. Opslaan, daarna **Deploys** → **Trigger deploy** → **Deploy site**, zodat de
   functie de nieuwe variabele oppikt.

Klaar. De chat merkt zelf dat er nu een model achter zit; onder elk antwoord
staat voortaan "taalmodel · met jouw actuele selectie" in plaats van de
herkomst van de patroonmotor.

## Wat het model wél en niet krijgt

Meegestuurd wordt een samenvatting van je situatie: de gameweek, je basiself
en bank met prijzen en projecties, je aanvoerder, je vrije transfers, de top
van je mini-league en je volglijst. Plus de laatste acht berichten van het
gesprek.

Niet meegestuurd wordt de hele database van 587 spelers — dat zou elke vraag
duur en traag maken. Vraag je naar een speler die niet in de samenvatting zit,
dan zal het model dat zeggen en je naar het juiste tabblad verwijzen. De
instructie is expliciet: geen cijfers noemen die niet in de context staan.

## Als het niet werkt

- **Antwoorden blijven van de patroonmotor komen** — de functie geeft niets
  terug. Controleer of de variabele exact `ANTHROPIC_API_KEY` heet en of je
  na het toevoegen opnieuw hebt gedeployed.
- **"API gaf 401"** — de sleutel klopt niet of is ingetrokken. Maak een nieuwe.
- **"API gaf 400"** — meestal een modelnaam die niet meer bestaat. De naam
  staat bovenin `netlify/functions/chat.mjs` bij `MODEL`.
- **"API gaf 429"** — je zit aan een limiet. Kijk bij Billing in de console.

## In het artifact

De artifact-versie op claude.ai kan dit niet: die omgeving blokkeert elk
verzoek naar een externe host, ook naar je eigen Netlify-functie. Daar blijft
de chat dus altijd op de patroonmotor draaien. Dat is geen instelling die je
kunt omzetten — het is hoe die omgeving beveiligd is.
