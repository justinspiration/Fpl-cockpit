#!/usr/bin/env node
/**
 * Haalt de xPts van FPL Estimator op en schrijft state/xp_estimator.json.
 *
 * Waarom deze bron erbij.
 * Justin vroeg om bronnen die hij vertrouwt, in plaats van te middelen met een
 * cijfer waar hij niets in ziet. Estimator geeft een TOTAAL over een venster van
 * gameweeks (standaard GW3-7) in plaats van een cijfer per week. Dat is grover
 * dan Pundit, maar het is wel een onafhankelijk model: waar twee onafhankelijke
 * bronnen hetzelfde zeggen, is een advies aanzienlijk steviger.
 *
 * De lijst laadt twintig spelers per keer. We klikken door tot de top 300 --
 * daaronder speelt niemand een rol in een FPL-beslissing, en elke extra klik is
 * tijd en breekbaarheid.
 *
 * Bron: https://www.fplestimator.com/best-picks
 */
const fs = require("fs");
const path = require("path");
const { execFileSync, spawn } = require("child_process");
const os = require("os");

const STATE = path.join(__dirname, "state");
const URL = "https://www.fplestimator.com/best-picks";
const POORT = 9377;
const MINSTENS = 300;

function vindChrome() {
  const kandidaten = [
    process.env.CHROME_PAD, process.env.CHROME_PATH, process.env.CHROME_BIN,
    // macOS
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    // Linux
    "/usr/bin/google-chrome-stable", "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser", "/usr/bin/chromium",
    "/opt/google/chrome/chrome",
    // Windows. Edge staat er bewust bij: die draait op dezelfde Chromium-motor
    // en spreekt hetzelfde debug-protocol, dus als Chrome ontbreekt werkt hij ook.
    ...(process.platform === "win32" ? [
      path.join(process.env["PROGRAMFILES"] || "C:\\Program Files",
                "Google", "Chrome", "Application", "chrome.exe"),
      path.join(process.env["PROGRAMFILES(X86)"] || "C:\\Program Files (x86)",
                "Google", "Chrome", "Application", "chrome.exe"),
      path.join(process.env.LOCALAPPDATA || "",
                "Google", "Chrome", "Application", "chrome.exe"),
      path.join(process.env["PROGRAMFILES(X86)"] || "C:\\Program Files (x86)",
                "Microsoft", "Edge", "Application", "msedge.exe"),
      path.join(process.env["PROGRAMFILES"] || "C:\\Program Files",
                "Microsoft", "Edge", "Application", "msedge.exe"),
    ] : []),
  ].filter(Boolean);
  for (const p of kandidaten) { try { if (fs.existsSync(p)) return p; } catch {} }
  /* Staat hij ergens anders, dan vragen we het besturingssysteem zelf.
     `command -v` is een shell-ingebouwde die op Windows niet bestaat; daar heet
     het `where`. Zonder dat onderscheid faalde de zoektocht op Windows stil. */
  try {
    const { execSync } = require("child_process");
    const cmd = process.platform === "win32"
      ? "where chrome.exe 2>nul || where msedge.exe 2>nul"
      : "command -v google-chrome-stable google-chrome chromium 2>/dev/null | head -1";
    const uit = execSync(cmd, { encoding: "utf8", shell: true }).trim().split(/\r?\n/)[0];
    if (uit && fs.existsSync(uit)) return uit;
  } catch {}
  throw new Error(
    "Geen Chrome gevonden. Zet de omgevingsvariabele CHROME_PAD naar het pad van je Chrome.\n" +
    (process.platform === "win32"
      ? "  Op Windows meestal: C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe\n" +
        "  PowerShell:  $env:CHROME_PAD = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'"
      : "  Op macOS meestal: /Applications/Google Chrome.app/Contents/MacOS/Google Chrome"));
}
/* In een container draait alles als root en weigert Chrome zonder deze twee
   vlaggen te starten. Op een gewone Mac zijn ze overbodig maar onschadelijk. */
const CI_ARGS = process.env.CI ? ["--no-sandbox", "--disable-dev-shm-usage",
                                 "--disable-gpu", "--headless=new"] : [];
const CHROME = vindChrome();

const ZICHTBAAR = process.argv.includes("--zichtbaar");

const wacht = (ms) => new Promise((r) => setTimeout(r, ms));

function log(...a) { console.log("[estimator]", ...a); }


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

let cdp = null;

(async () => {
  const { proc, profiel } = await startChrome();
  try {
    cdp = await verbind();
    const { stuur, evalueer: ev } = cdp;
    await metLimiet(stuur("Page.enable"), 20000, "Page.enable");
    await metLimiet(stuur("Page.navigate", { url: URL }), 30000, "navigeren");
    await new Promise((r) => setTimeout(r, 5000));

    // doorklikken tot we genoeg spelers hebben
    let vorige = 0;
    for (let ronde = 0; ronde < 20; ronde++) {
      const n = await ev("document.querySelectorAll('.player-row').length");
      log(`ronde ${ronde}: ${n} spelers`);
      if (n >= MINSTENS) break;
      if (n === vorige && ronde > 1) { log("de lijst groeit niet meer"); break; }
      vorige = n;
      const geklikt = await ev(
        "(()=>{const b=[...document.querySelectorAll('button')]" +
        ".find(x=>/Show more/i.test(x.innerText)); if(!b) return false; b.click(); return true;})()");
      if (!geklikt) { log("geen 'Show more' meer"); break; }
      await new Promise((r) => setTimeout(r, 1400));
    }

    const venster = await ev(
      "(()=>{const m=(document.body.innerText||'').match(/GW\\s*(\\d+)\\s*[-\\u2010-\\u2015]\\s*(\\d+)/);" +
      "return m?m[1]+'-'+m[2]:null;})()");
    const ruw = await ev(
      "JSON.stringify([...document.querySelectorAll('.player-row')].map(r=>r.innerText" +
      ".replace(/\\s+/g,' ').trim()))");
    const regels = JSON.parse(ruw);
    if (regels.length < 50)
      throw new Error(`slechts ${regels.length} spelerregels gelezen`);

    /* Een regel ziet eruit als:
         "1 FWD Haaland Haaland Man City 34.7 £15.5m COV (H) +4"
       rang, positie, naam (twee keer: kort en lang), club, xPts, prijs, fixture.
       We ankeren op de PRIJS -- dat is het enige veld met een gegarandeerd
       formaat -- en lezen het getal er direct voor als xPts. Op naam ankeren
       breekt bij spelers met een spatie of accent in hun naam. */
    const rijen = [];
    for (const r of regels) {
      const m = r.match(/^(\d+)\s+(GKP|DEF|MID|FWD)\s+(.+?)\s+([\d.]+)\s+£([\d.]+)m/);
      if (!m) continue;
      const namen = m[3].trim();
      rijen.push({ rang: +m[1], pos: m[2], tekst: namen,
                   xpts: parseFloat(m[4]), prijs: parseFloat(m[5]) });
    }
    if (rijen.length < 50)
      throw new Error(`${regels.length} regels gelezen maar slechts ${rijen.length} ontleed`);

    fs.mkdirSync(STATE, { recursive: true });
    fs.writeFileSync(path.join(STATE, "estimator_ruw.json"), JSON.stringify({
      _bron: "FPL Estimator — " + URL,
      _opgehaald: new Date().toISOString().slice(0, 19).replace("T", " "),
      _venster: venster,
      _aantal: rijen.length,
      rijen }, null, 1));
    log(`geschreven: state/estimator_ruw.json (${rijen.length} spelers, venster GW${venster})`);
  } catch (e) {
    console.error("[estimator] MISLUKT:", e.message);
    if (cdp) {
      try {
        const r = await metLimiet(cdp.stuur("Runtime.evaluate", {
          expression: "JSON.stringify({t:document.title,u:location.href," +
                      "n:document.querySelectorAll('.player-row').length," +
                      "b:(document.body?document.body.innerText:'').slice(0,240)})",
          returnByValue: true }), 10000, "storingsrapport");
        const d = JSON.parse(r.result.value);
        console.error("[estimator] pagina bij de storing: ", d.u, "|", d.t, "| rijen", d.n);
        console.error("[estimator] tekst:", String(d.b).replace(/\s+/g, " ").slice(0, 220));
      } catch (e2) { console.error("[estimator] storingsrapport mislukt:", e2.message); }
    }
    throw e;
  } finally {
    if (cdp) cdp.sluit();
    proc.kill();
    try { fs.rmSync(profiel, { recursive: true, force: true }); } catch (e) {}
  }
})().catch((e) => {
  /* Deze afsluiter slikte alles. startChrome() staat BUITEN het try-blok, dus
     faalde het opstarten van de browser, dan eindigde het script met exitcode 1
     en geen enkele regel uitvoer — onmogelijk te diagnosticeren. */
  console.error("[estimator] MISLUKT:", e && e.message ? e.message : e);
  if (e && e.stack) console.error(String(e.stack).split("\n").slice(1, 4).join("\n"));
  process.exit(1);
});
