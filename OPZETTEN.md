# Het dashboard online zetten

Drie onderdelen, in deze volgorde. Stap 1 en 2 kun je vandaag doen; stap 3 kan
later zonder dat er iets omvalt.

---

## 1. De data laten verversen zonder Netlify-credits

**Het idee.** De site verandert zelden, de data elke dag. Door die twee te
scheiden hoeft Netlify nooit opnieuw te bouwen.

| | Verandert | Waar | Kosten |
|---|---|---|---|
| De pagina | alleen als er iets gebouwd wordt | Netlify | credits, maar zelden |
| De data | vier keer per dag | GitHub | niets |

**Wat jij doet**

1. Maak een GitHub-repository, bijvoorbeeld `fpl-cockpit`. Privé mag.
2. Zet de **hele inhoud van `netlify/repo/`** erin. Die map is compleet en
   wordt bij elke bouw automatisch gelijkgetrokken, dus je hoeft niets te
   selecteren:

   | Wat | Waarvoor |
   |---|---|
   | `index.html` | het dashboard |
   | `data/dashboard.json` | de data die de pagina bij het openen ophaalt |
   | `scripts/` | de 15 scripts van de verversing, plus de gegevens die ze nodig hebben |
   | `.github/workflows/ververs.yml` | de verversing zelf |
   | `netlify/functions/chat.mjs` | de assistent, voor als je een sleutel instelt |
   | `netlify.toml` | zorgt dat een data-update geen nieuwe build kost |
   | `CHAT-OPZETTEN.md` | hoe je het taalmodel aanzet |

3. Push. De Action draait daarna om 06:00, 12:00, 17:00 en 22:00 UTC.

> **Let op bij een update.** Kopieer altijd de hele `netlify/repo/` opnieuw.
> `scripts/state/league_archief.json` is de uitzondering: dat bestand groeit
> elke gameweek met de opstellingen van je mini-league en is nergens anders te
> herstellen. De Action schrijft het zelf weg; overschrijf het niet met een
> oudere lokale versie.

**Waarom dit gratis is.** GitHub Actions geeft 2000 minuten per maand voor
privérepo's; een verversing kost er ongeveer drie. Vier per dag is 360 minuten.
De Action commit alleen naar `data/`, en Netlify is zo ingesteld dat het daar
niet op bouwt.

**De rem op Netlify.** Zet in `netlify.toml`:

```toml
[build]
  publish = "."
  command = "echo geen build nodig"

[build.ignore]
  # bouw alleen opnieuw als er iets buiten data/ verandert
  command = "git diff --quiet HEAD^ HEAD -- . ':(exclude)data'"
```

---

## 2. Netlify koppelen

1. Netlify → Add new site → Import an existing project → kies je repo.
2. Build command: leeg laten. Publish directory: `.`
3. Deploy.

De pagina haalt zijn data bij het openen op van
`https://raw.githubusercontent.com/<jij>/fpl-cockpit/main/data/dashboard.json`.
Dat is een ander domein, maar GitHub stuurt de juiste CORS-koppen mee, dus dat
werkt zonder omweg.

---

## 3. Inloggen en opslaan

Hier heb ik jou nodig: ik maak geen accounts aan en voer geen wachtwoorden in.

**Mijn advies: Supabase.** Gratis, doet authenticatie én opslag, en werkt vanuit
een statische pagina zonder eigen server.

1. Maak een project op supabase.com.
2. Table editor → nieuwe tabel `voorkeuren`:

   | kolom | type | opmerking |
   |---|---|---|
   | `gebruiker` | uuid | primaire sleutel, verwijst naar `auth.users` |
   | `data` | jsonb | alles wat je opslaat |
   | `bijgewerkt` | timestamptz | standaard `now()` |

3. Zet Row Level Security aan met deze regel, zodat niemand bij andermans data kan:

   ```sql
   create policy "eigen data" on voorkeuren
     for all using (auth.uid() = gebruiker);
   ```

4. Geef me de **project-URL** en de **anon key**. Die twee mogen publiek in de
   pagina staan — dat is waar ze voor bedoeld zijn. Je wachtwoord en je
   `service_role` key heb ik nooit nodig en wil ik ook niet zien.

Daarna bouw ik de inlogknop en de synchronisatie. Tot die tijd blijft alles in
`localStorage` staan: het werkt, maar per apparaat.

**Wat er gesynchroniseerd wordt:** je opstelling en transferplan, aanvoerder,
volglijst, teamversies, de indeling van Mijn pagina, en je periodevoorkeuren
per tabblad.
