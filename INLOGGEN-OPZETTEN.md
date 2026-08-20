# Inloggen en synchroniseren aanzetten

Zonder dit werkt alles gewoon, maar per apparaat: je telefoon en je laptop
weten niets van elkaar. Met dit erbij houden ze elkaar bij.

Kost niets. Supabase is gratis tot ruim boven wat jij ooit gaat gebruiken.

---

## 1. Project maken

1. Ga naar **supabase.com**, maak een account en klik **New project**.
2. Naam: `fpl-cockpit`. Regio: **West EU (Ireland)** — dat scheelt wachttijd.
3. Er wordt een databasewachtwoord gevraagd. Bewaar dat in je wachtwoord-
   manager. **Ik heb het nooit nodig en wil het ook niet zien.**

## 2. De tabel aanmaken

Ga naar **SQL Editor** → **New query**, plak dit en klik **Run**:

```sql
create table voorkeuren (
  gebruiker  uuid primary key references auth.users on delete cascade,
  data       jsonb not null default '{}'::jsonb,
  bijgewerkt timestamptz not null default now()
);

alter table voorkeuren enable row level security;

create policy "eigen data lezen"    on voorkeuren for select using (auth.uid() = gebruiker);
create policy "eigen data schrijven" on voorkeuren for insert with check (auth.uid() = gebruiker);
create policy "eigen data bijwerken" on voorkeuren for update using (auth.uid() = gebruiker);

-- de gebruiker hoeft zijn eigen id niet mee te sturen
alter table voorkeuren alter column gebruiker set default auth.uid();
```

Die regels zorgen dat niemand bij andermans gegevens kan, ook niet met de
publieke sleutel uit stap 3.

## 3. De inloglink toestaan

**Authentication** → **URL Configuration**:

| Veld | Waarde |
|---|---|
| Site URL | het adres van je Netlify-site, bijvoorbeeld `https://reijning-champion.netlify.app` |
| Redirect URLs | hetzelfde adres |

Zonder deze stap komt de link in je mail wel aan, maar loopt hij dood.

## 4. De twee waarden doorgeven

**Project Settings** → **API**. Daar staan:

- **Project URL** — ziet eruit als `https://abcdefgh.supabase.co`
- **anon public** key — een lange sleutel die met `eyJ` begint

Die twee mogen publiek in de pagina staan; daar zijn ze voor gemaakt. De
`service_role` key mag dat **niet** — die heb ik nooit nodig, geef hem nergens
door.

Zet ze bovenin `index.html`, direct na de `<title>`-regel:

```html
<script>window.FPL_SYNC = {
  url:  "https://abcdefgh.supabase.co",
  anon: "eyJ..."
};</script>
```

Of stuur ze mij, dan zet ik ze erin en lever ik het bestand terug.

---

## Hoe het daarna werkt

Rechtsboven staat een knop **Inloggen**. Je vult je e-mailadres in, krijgt een
link, klikt die op hetzelfde apparaat — klaar. Geen wachtwoord.

- Dat apparaat blijft ingelogd; de sessie wordt stil verlengd.
- Elke wijziging wordt anderhalve seconde later weggeschreven.
- Bij het openen van de pagina, bij terugkeer naar het tabblad en elke twee
  minuten wordt gekeken of er elders iets veranderd is.
- Samenvoegen gaat **per onderdeel** op tijdstempel. Deed je op je telefoon een
  transfer en op je laptop een notitie, dan blijven ze allebei staan.

**Wat er meegaat:** transferplan, chips, opstelling, aanvoerder, volglijst,
notities, teamversies, de indeling van Mijn pagina, je periodevoorkeuren en je
weergavemodus.

**Wat er niet meegaat:** de chatgeschiedenis met de assistent. Die blijft per
apparaat, want hij hoort bij het gesprek dat je daar voerde.

**Uitloggen** doe je met dezelfde knop. Je werk blijft dan gewoon op dat
apparaat staan; alleen het bijhouden stopt.
