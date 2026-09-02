#!/usr/bin/env node
/**
 * Haalt de expected points van FPL Copilot op en schrijft state/xp_copilot.json.
 *
 * Waarom een eigen script: Copilot rendert de tabel met JavaScript, dus curl
 * levert een lege pagina. Playwright is niet geinstalleerd en hoeft ook niet:
 * Chrome heeft een debug-protocol en Node 24 heeft WebSocket ingebouwd, dus
 * dit draait op wat er al staat.
 *
 * Werkwijze, gelijk aan de handmatige uitlezing van 14-08-2026:
 *   1. open /expected-points
 *   2. klik "Show all" zodat alle 581 rijen in de DOM staan
 *   3. lees per positiefilter (GK/DEF/MID/FWD) uit, zodat elke speler een
 *      positie krijgt en de telling controleerbaar is
 *   4. los namen op die in dezelfde positie dubbel voorkomen via het clubfilter
 *
 * Gebruik:  node copilot_ophalen.js [--zichtbaar]
 */

const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const os = require("os");

const HIER = __dirname;
const STATE = path.join(HIER, "state");
/* Waar staat Chrome?

   Hier stond één hard pad naar /Applications — dat werkt op deze Mac en
   nergens anders. Op GitHub draait Ubuntu, dus startte Chrome daar nooit en
   mislukte het ophalen elke ronde stil. Nu: eerst kijken wat de omgeving
   aangeeft (CHROME_PAD zet de workflow), daarna de gebruikelijke plekken op
   macOS en Linux aflopen. */
function vindChrome() {
  const kandidaten = [
    process.env.CHROME_PAD, process.env.CHROME_PATH, process.env.CHROME_BIN,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome-stable", "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser", "/usr/bin/chromium",
    "/opt/google/chrome/chrome",
  ].filter(Boolean);
  for (const p of kandidaten) { try { if (fs.existsSync(p)) return p; } catch {} }
  // setup-chrome zet hem soms in een eigen map onder de home van de runner
  try {
    const { execSync } = require("child_process");
    const uit = execSync("command -v google-chrome-stable google-chrome chromium 2>/dev/null | head -1",
      { encoding: "utf8" }).trim();
    if (uit && fs.existsSync(uit)) return uit;
  } catch {}
  throw new Error("Geen Chrome gevonden. Zet CHROME_PAD naar het pad van je Chrome.");
}
/* In een container draait alles als root en weigert Chrome zonder deze twee
   vlaggen te starten. Op een gewone Mac zijn ze overbodig maar onschadelijk. */
const CI_ARGS = process.env.CI ? ["--no-sandbox", "--disable-dev-shm-usage",
                                 "--disable-gpu", "--headless=new"] : [];
const CHROME = vindChrome();
const POORT = 9333;
const ZICHTBAAR = process.argv.includes("--zichtbaar");

const wacht = (ms) => new Promise((r) => setTimeout(r, ms));

function log(...a) { console.log("[copilot]", ...a); }


/* Wachten met een grens.

   Hieronder stonden twee beloftes die alleen afliepen als Chrome antwoordde:
   het openen van de WebSocket, en elk commando dat erover ging. Antwoordt de
   browser niet — een vastgelopen renderer, een pagina die op een datacenter-IP
   anders reageert — dan wacht het script tot in de eeuwigheid. Lokaal viel dat
   nooit op omdat er altijd binnen een seconde antwoord kwam; op GitHub at het
   de hele runtijd op en werd de complete verversing afgebroken.

   Alles wat op de browser wacht loopt nu langs deze grens. */
function metLimiet(belofte, ms, wat) {
  let t;
  return Promise.race([
    belofte.finally(() => clearTimeout(t)),
    new Promise((_, rej) => { t = setTimeout(
      () => rej(new Error(`geen antwoord van Chrome binnen ${ms / 1000}s bij ${wat}`)), ms); }),
  ]);
}

/* ---------- Chrome starten ---------- */
async function startChrome() {
  const profiel = fs.mkdtempSync(path.join(os.tmpdir(), "fplcopilot-"));
  const args = [
    `--remote-debugging-port=${POORT}`,
    `--user-data-dir=${profiel}`,
    "--no-first-run", "--no-default-browser-check",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--window-size=1440,900",
  ];
  if (!ZICHTBAAR) args.push("--headless=new");
  const proc = spawn(CHROME, args.concat(CI_ARGS), { stdio: "ignore", detached: false });

  for (let i = 0; i < 60; i++) {
    await wacht(250);
    try {
      const r = await fetch(`http://127.0.0.1:${POORT}/json/version`);
      if (r.ok) { log("Chrome draait"); return { proc, profiel }; }
    } catch { /* nog niet op */ }
  }
  proc.kill();
  throw new Error("Chrome kwam niet op binnen 15 seconden");
}

/* ---------- CDP-verbinding ---------- */
async function verbind() {
  const lijst = await (await fetch(`http://127.0.0.1:${POORT}/json/list`)).json();
  let doel = lijst.find((t) => t.type === "page");
  if (!doel) throw new Error("geen tabblad gevonden");
  const ws = new WebSocket(doel.webSocketDebuggerUrl);
  await metLimiet(new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; }),
                  20000, "het openen van de verbinding");

  let id = 0;
  const open = new Map();
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.id && open.has(m.id)) { open.get(m.id)(m); open.delete(m.id); }
  };
  const stuur = (method, params = {}) =>
    metLimiet(new Promise((res, rej) => {
      const mijn = ++id;
      open.set(mijn, (m) => (m.error ? rej(new Error(m.error.message)) : res(m.result)));
      ws.send(JSON.stringify({ id: mijn, method, params }));
    }), 45000, method);

  const evalueer = async (expr) => {
    const r = await stuur("Runtime.evaluate", {
      expression: expr, returnByValue: true, awaitPromise: true,
    });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + " :: " + expr.slice(0, 90));
    return r.result.value;
  };
  return { stuur, evalueer, sluit: () => ws.close() };
}

/* ---------- uitlezen ---------- */
const LEES = `(()=>{const uit=[];
document.querySelectorAll("table tbody tr").forEach(r=>{const td=[...r.querySelectorAll("td")];
  if(td.length<11)return; const gw=[],mn=[];
  for(let i=1;i<=8;i++){const p=td[i].innerText.trim().split("\\n");
    gw.push(parseFloat(p[0])); mn.push(parseInt((p[1]||"").replace("'",""))||null);}
  uit.push({n:td[0].innerText.trim().split("\\n")[0].trim(),gw,mn});});
return uit})()`;

const KNOP = (tekst) =>
  `(()=>{const b=[...document.querySelectorAll("button")].find(x=>x.innerText.trim()===${JSON.stringify(tekst)});
   if(b){b.click();return true} return false})()`;

const AANTAL = `(()=>{const m=document.body.innerText.match(/(\\d+) players/);return m?+m[1]:null})()`;

async function pakPositie(ev, knop, naam) {
  await ev(KNOP("Clear all"));                     // faalt stil als er niets aanstaat
  await wacht(500);
  if (!(await ev(KNOP(knop)))) throw new Error(`filterknop ${knop} niet gevonden`);
  await wacht(900);
  const verwacht = await ev(AANTAL);
  await ev(KNOP("Show all"));
  await wacht(900);
  const rijen = await ev(LEES);
  if (rijen.length !== verwacht)
    throw new Error(`${naam}: ${rijen.length} rijen gelezen, ${verwacht} verwacht`);
  log(`${naam}: ${rijen.length}`);
  return rijen;
}

async function pakClub(ev, club) {
  await ev(KNOP("Clear all"));
  await wacht(500);
  await ev(`(()=>{const b=[...document.querySelectorAll("button")].find(x=>/^Team/.test(x.innerText.trim()));
    if(b)b.click();return !!b})()`);
  await wacht(400);
  if (!(await ev(KNOP(club)))) return null;
  await wacht(800);
  await ev(KNOP("Show all"));
  await wacht(700);
  return await ev(LEES);
}

/* ---------- hoofdprogramma ---------- */
let cdp = null;

/* Waarom dit bestaat.
   Dit script werkt op Justins Mac en faalde tegelijk drie dagen lang op GitHub,
   zonder dat we konden zien waarom: het log zei alleen "MISLUKT" met de melding.
   Dat kan van alles zijn — Chrome niet gevonden, pagina niet geladen, of een
   bot-controle die datacenter-adressen weert. Die laatste is bij een GitHub-runner
   het meest waarschijnlijk, en herken je alleen aan wat er op de pagina STAAT. */
async function storingsrapport(fout) {
  if (!cdp) {
    console.error("[copilot] geen browserverbinding — Chrome is niet opgestart. " +
                  "Controleer de stap 'Chrome installeren' op de runner.");
    return;
  }
  try {
    const r = await metLimiet(cdp.stuur("Runtime.evaluate", {
      expression: "JSON.stringify({t:document.title,u:location.href," +
                  "n:document.querySelectorAll('tr').length," +
                  "b:(document.body?document.body.innerText:'').slice(0,300)})",
      returnByValue: true }), 10000, "storingsrapport");
    const d = JSON.parse(r.result.value);
    console.error("[copilot] pagina op het moment van de storing:");
    console.error("[copilot]   url   :", d.u);
    console.error("[copilot]   titel :", d.t);
    console.error("[copilot]   rijen :", d.n);
    console.error("[copilot]   tekst :", String(d.b).replace(/\s+/g, " ").slice(0, 260));
    if (/just a moment|checking your browser|cloudflare|access denied|forbidden|captcha/i
        .test(d.t + " " + d.b))
      console.error("[copilot]   >> dit oogt als een bot-controle. Een GitHub-runner komt " +
                    "van een datacenter-adres; die worden vaker geweerd dan een thuisverbinding.");
  } catch (e2) {
    console.error("[copilot] storingsrapport zelf mislukt:", e2.message);
  }
}

(async () => {
  const { proc, profiel } = await startChrome();
  cdp = null;
  try {
    cdp = await verbind();
    const { stuur, evalueer: ev } = cdp;
    await stuur("Page.enable");
    await stuur("Runtime.enable");
    await stuur("Page.navigate", { url: "https://fplcopilot.com/expected-points" });
    await wacht(6000);

    // promotieoverlay weghalen; die vangt alle kliks af
    await ev(`(()=>{let n=0;document.querySelectorAll("body>div,div").forEach(d=>{
      const s=getComputedStyle(d);
      if(s.position==="fixed"&&+s.zIndex>=40&&d.getBoundingClientRect().height>200){d.remove();n++}});
      document.body.style.overflow="auto";return n})()`);

    const stempel = await ev(`(()=>{const m=document.body.innerText.match(/Updated [^\\n]+/);return m?m[0]:""})()`);
    log("sitestempel:", stempel || "onbekend");

    const posities = {
      GKP: await pakPositie(ev, "GK", "keepers"),
      DEF: await pakPositie(ev, "DEF", "verdedigers"),
      MID: await pakPositie(ev, "MID", "middenvelders"),
      FWD: await pakPositie(ev, "FWD", "aanvallers"),
    };
    const totaal = Object.values(posities).reduce((a, v) => a + v.length, 0);
    log("totaal", totaal, "spelers");

    // namen die binnen dezelfde positie dubbel voorkomen: club erbij halen
    const dubbel = {};
    for (const [pos, rijen] of Object.entries(posities)) {
      const tel = {};
      rijen.forEach((r) => (tel[r.n] = (tel[r.n] || 0) + 1));
      Object.entries(tel).filter(([, c]) => c > 1).forEach(([n]) => (dubbel[`${n}|${pos}`] = true));
    }
    const clubVan = {};
    if (Object.keys(dubbel).length) {
      log("dubbele naam+positie:", Object.keys(dubbel).join(", "));
      const CLUBS = ["ARS","AVL","BHA","BOU","BRE","CHE","COV","CRY","EVE","FUL",
                     "HUL","IPS","LEE","LIV","MCI","MUN","NEW","NFO","SUN","TOT"];
      for (const club of CLUBS) {
        const rijen = await pakClub(ev, club);
        if (!rijen) continue;
        rijen.forEach((r) => {
          for (const sleutel of Object.keys(dubbel)) {
            const [naam] = sleutel.split("|");
            if (r.n === naam) clubVan[`${naam}|${r.gw[0]}`] = club;
          }
        });
      }
      log("clubs opgelost voor", Object.keys(clubVan).length, "rijen");
    }

    /* Vanaf welke gameweek loopt deze tabel?

       Hier stond hard "1". Copilot toont altijd vanaf de eerstvolgende
       gameweek, dus zodra GW1 gespeeld was begon zijn tabel bij GW2 terwijl
       het dashboard hem als GW1 inlas. Elke projectie stond daardoor een week
       verschoven: Haaland kreeg in GW2 het cijfer van GW3. Nu wordt de
       startweek bij FPL zelf opgehaald: de eerste gameweek waarvan de deadline
       nog niet verstreken is. */
    let startGW = 1;
    try {
      const bs = await (await fetch("https://fantasy.premierleague.com/api/bootstrap-static/",
        { headers: { "User-Agent": "Mozilla/5.0" } })).json();
      const nu = Date.now();
      const volgende = bs.events.find(e => new Date(e.deadline_time).getTime() > nu);
      if (volgende) startGW = volgende.id;
      log("startgameweek volgens FPL:", startGW);
    } catch (e) {
      log("LET OP: startgameweek niet op te halen, val terug op 1 —", e.message);
    }

    /* Datum EN tijd. Er stond alleen een datum, en dat maakte de leeftijd tot
       24 uur onnauwkeurig: een bestand van vanmiddag las als middernacht. Voor
       een bron die elke drie uur zou moeten verversen is dat te grof.

       En de stempel van de site zelf ("Updated 2 hours ago") is relatieve tekst.
       Zodra je die opslaat betekent hij niets meer — over twee dagen staat er nog
       steeds "2 hours ago". Daarom rekenen we hem hier meteen om naar een absoluut
       moment, zolang we nog weten wanneer "nu" was. */
    const nu = new Date();
    let siteMoment = null;
    if (stempel) {
      const m = String(stempel).match(/(\d+)\s*(minute|minuut|hour|uur|day|dag)/i);
      if (m) {
        const n = Number(m[1]);
        const eenheid = m[2].toLowerCase();
        const ms = /min/.test(eenheid) ? 6e4 : /uur|hour/.test(eenheid) ? 36e5 : 864e5;
        siteMoment = new Date(nu.getTime() - n * ms).toISOString().slice(0, 19).replace("T", " ");
      }
    }
    const uit = { _bron: "FPL Copilot — https://fplcopilot.com/expected-points",
      _opgehaald: nu.toISOString().slice(0, 19).replace("T", " "),
      _copilot_bijgewerkt: stempel,
      _copilot_moment: siteMoment, _start_gw: startGW, _horizon: 8,
      _methode: "Automatisch opgehaald met copilot_ophalen.js via Chrome DevTools Protocol. " +
        "Per positiefilter uitgelezen; de telling per filter moet overeenkomen met wat de site meldt, " +
        "anders breekt het script af. Dubbele naam+positie wordt opgelost via het clubfilter.",
      _telling: Object.fromEntries(Object.entries(posities).map(([k, v]) => [k, v.length])),
      _totaal: totaal,
      posities, clubVan };
    fs.writeFileSync(path.join(STATE, "copilot_ruw.json"), JSON.stringify(uit));
    log("geschreven: state/copilot_ruw.json");
  } catch (e) {
    /* Rapporteren VOORDAT het finally-blok Chrome afsluit. Stond dit in de
       .catch() onderaan, dan was de verbinding al dicht en kregen we alleen
       "geen antwoord van Chrome" — precies wat de test liet zien. */
    await storingsrapport(e);
    throw e;
  } finally {
    if (cdp) cdp.sluit();
    proc.kill();
    try { fs.rmSync(profiel, { recursive: true, force: true }); } catch {}
  }
})().catch(() => process.exit(1));

