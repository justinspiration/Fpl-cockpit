#!/usr/bin/env node
/**
 * Haalt de bookmakersodds voor de komende Premier League-duels op en schrijft
 * state/odds_ruw.json.
 *
 * Waarom: clean sheet-kansen en verwachte goals waren tot nu toe mijn eigen
 * Poisson-berekening uit de xGF/xGA van vorig seizoen. De markt prijst dagelijks
 * met echt geld en verwerkt blessures, vorm en opstellingsnieuws. Dat is
 * objectief beter gekalibreerd dan wat ik uit seizoenstotalen afleid.
 *
 * Alleen de 1X2-markt wordt gelezen. Dat is genoeg: uit drie uitkomstkansen
 * (waarvan twee onafhankelijk) zijn de twee doelpuntverwachtingen exact op te
 * lossen. Over/under is dus niet nodig — zie odds_verwerk.py.
 *
 * Gebruik:  node odds_ophalen.js [--zichtbaar]
 */
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

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
const POORT = 9361;
const ZICHTBAAR = process.argv.includes("--zichtbaar");
const BRON = "https://www.oddsportal.com/football/england/premier-league/";

const wacht = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log("[odds]", ...a);

/* OddsPortal gebruikt eigen namen; hier de vertaling naar FPL-afkortingen. */
const CLUB = {
  "arsenal": "ARS", "aston villa": "AVL", "bournemouth": "BOU", "brentford": "BRE",
  "brighton": "BHA", "chelsea": "CHE", "coventry": "COV", "crystal palace": "CRY",
  "everton": "EVE", "fulham": "FUL", "hull": "HUL", "ipswich": "IPS",
  "leeds": "LEE", "liverpool": "LIV", "manchester city": "MCI", "manchester utd": "MUN",
  "manchester united": "MUN", "newcastle": "NEW", "nottingham": "NFO",
  "nottingham forest": "NFO", "sunderland": "SUN", "tottenham": "TOT",
  "wolves": "WOL", "west ham": "WHU", "burnley": "BUR", "leicester": "LEI",
  "southampton": "SOU", "sheffield utd": "SHU", "luton": "LUT", "norwich": "NOR",
};


/* Wachten met een grens — zie de toelichting in copilot_ophalen.js.
   Antwoordt Chrome niet, dan wacht dit script anders oneindig en eet het de
   hele runtijd van de verversing op. */
function metLimiet(belofte, ms, wat) {
  let t;
  return Promise.race([
    belofte.finally(() => clearTimeout(t)),
    new Promise((_, rej) => { t = setTimeout(
      () => rej(new Error(`geen antwoord van Chrome binnen ${ms / 1000}s bij ${wat}`)), ms); }),
  ]);
}

async function startChrome() {
  const profiel = fs.mkdtempSync(path.join(os.tmpdir(), "fplodds-"));
  const args = [`--remote-debugging-port=${POORT}`, `--user-data-dir=${profiel}`,
    "--no-first-run", "--no-default-browser-check", "--window-size=1440,1600"];
  if (!ZICHTBAAR) args.push("--headless=new");
  const proc = spawn(CHROME, args.concat(CI_ARGS), { stdio: "ignore" });
  for (let i = 0; i < 60; i++) {
    await wacht(250);
    try { if ((await fetch(`http://127.0.0.1:${POORT}/json/version`)).ok) return { proc, profiel }; }
    catch { /* nog niet op */ }
  }
  proc.kill();
  throw new Error("Chrome kwam niet op");
}

async function verbind() {
  const lijst = await (await fetch(`http://127.0.0.1:${POORT}/json/list`)).json();
  const doel = lijst.find((t) => t.type === "page");
  if (!doel) throw new Error("geen tabblad");
  const ws = new WebSocket(doel.webSocketDebuggerUrl);
  await metLimiet(new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; }),
                  20000, "het openen van de verbinding");
  let id = 0; const open = new Map();
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && open.has(m.id)) { open.get(m.id)(m); open.delete(m.id); } };
  const stuur = (method, params = {}) => metLimiet(new Promise((res, rej) => {
    const mijn = ++id;
    open.set(mijn, (m) => (m.error ? rej(new Error(m.error.message)) : res(m.result)));
    ws.send(JSON.stringify({ id: mijn, method, params }));
  }), 45000, method);
  const ev = async (expr) => {
    const r = await stuur("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.text);
    return r.result.value;
  };
  return { stuur, ev, sluit: () => ws.close() };
}

const LEES = `(()=>{
  const uit=[];
  document.querySelectorAll('div[data-testid="game-row"]').forEach(r=>{
    const t=r.innerText.split("\\n").map(x=>x.trim()).filter(Boolean);
    // vorm: [tijd, thuis, "-", uit, oddsThuis, oddsGelijk, oddsUit]
    const getal=t.filter(x=>/^\\d+\\.\\d+$/.test(x)).map(Number);
    if(getal.length<3) return;
    const namen=t.filter(x=>!/^\\d+[:.]/.test(x)&&x!=="-"&&!/^\\d+\\.\\d+$/.test(x));
    if(namen.length<2) return;
    uit.push({thuis:namen[0], uitTeam:namen[1], tijd:t[0],
              o:[getal[0],getal[1],getal[2]]});
  });
  return uit;
})()`;

(async () => {
  const { proc, profiel } = await startChrome();
  let cdp;
  try {
    cdp = await verbind();
    const { stuur, ev } = cdp;
    await stuur("Page.enable"); await stuur("Runtime.enable");
    await stuur("Page.navigate", { url: BRON });
    await wacht(9000);
    await ev("window.scrollTo(0,1000)");
    await wacht(2500);

    const rijen = await ev(LEES);
    if (!rijen || rijen.length < 4)
      throw new Error(`slechts ${rijen ? rijen.length : 0} duels gelezen — pagina waarschijnlijk niet geladen`);

    const duels = [];
    const onbekend = new Set();
    for (const r of rijen) {
      const th = CLUB[r.thuis.toLowerCase()], ut = CLUB[r.uitTeam.toLowerCase()];
      if (!th) onbekend.add(r.thuis);
      if (!ut) onbekend.add(r.uitTeam);
      if (!th || !ut) continue;
      const [oh, od, oa] = r.o;
      if (!(oh > 1 && od > 1 && oa > 1)) continue;
      duels.push({ thuis: th, uit: ut, tijd: r.tijd, odds: { thuis: oh, gelijk: od, uit: oa } });
    }
    if (onbekend.size) log("clubnamen niet herkend:", [...onbekend].join(", "));
    if (!duels.length) throw new Error("geen enkel duel kon aan een club gekoppeld worden");

    const uit = {
      _bron: "OddsPortal — " + BRON,
      _markt: "1X2, gemiddelde van de getoonde bookmakers",
      _opgehaald: new Date().toISOString(),
      _methode: "Alleen de 1X2-markt. Uit drie uitkomstkansen zijn de twee " +
        "doelpuntverwachtingen exact op te lossen; over/under is daarvoor niet nodig.",
      _duels: duels.length, _onbekend: [...onbekend],
      duels,
    };
    fs.writeFileSync(path.join(STATE, "odds_ruw.json"), JSON.stringify(uit, null, 1));
    log(`${duels.length} duels geschreven naar state/odds_ruw.json`);
    duels.slice(0, 4).forEach(d =>
      log(`  ${d.thuis}-${d.uit}  ${d.odds.thuis} / ${d.odds.gelijk} / ${d.odds.uit}`));
  } finally {
    if (cdp) cdp.sluit();
    proc.kill();
    try { fs.rmSync(profiel, { recursive: true, force: true }); } catch { /* laat staan */ }
  }
})().catch((e) => { console.error("[odds] MISLUKT:", e.message); process.exit(1); });
