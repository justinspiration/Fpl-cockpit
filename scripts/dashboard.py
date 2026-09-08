#!/usr/bin/env python3
"""
Genereert het FPL-dashboard voor Justin als self-contained HTML.

WAAROM DE DATA IS INGEBAKKEN: de FPL API stuurt geen CORS-headers
(cross-origin-resource-policy: same-origin) en een gepubliceerd artifact heeft een
strikte CSP die elke externe host blokkeert. Een browserpagina kan de data dus nooit
zelf ophalen. Daarom server-side ophalen, inbakken, en per blok tonen hoe oud het is.

BRONNEN PER VELD (Justins eis: nul verzonnen data)
  prijzen, ownership, minuten, starts, set-pieces, blessures, fixtures, FDR
      -> officiele FPL API /bootstrap-static/ en /fixtures/
  xG, goals-vs-xG, shots, conversie, xG per shot (spelers)
      -> Opta via theanalyst.com, tabblad PLAYERS > Attacking (state/opta_raw.tsv)
  xGF / xGA per club
      -> Opta via theanalyst.com, tabblad TABLE > EXPECTED (state/opta_teams.tsv)
  promovendi COV/HUL/IPS
      -> Championship 25/26 eindstand, omgerekend (zie opta.py)
  verwachte goals + clean sheet% per fixture
      -> BEREKEND uit bovenstaande Opta-cijfers, formule in opta.py
  puntenprojectie per speler per gameweek
      -> BEREKEND: punten per 90 (FPL) x minutenverwachting x fixture-aanpassing
         Dit is de enige laag die van ons is; het dashboard labelt hem als berekening.

Gebruik:
  python3 dashboard.py [--out pad.html]
"""
import json
import re
import unicodedata, os, argparse, urllib.request, sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import opta as OPTA
import bronnen as BRONNEN


def _getal(v, standaard=0.0):
    """De FPL API levert sommige velden als tekst ("0", "12.4"). Netjes omzetten."""
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return standaard

BASE = "https://fantasy.premierleague.com/api"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")


def _lees(naam, standaard=None):
    """Een optioneel gegevensbestand inlezen zonder de hele bouw te riskeren.

    Deze bestanden werden ingelezen met een simpele bestaat-controle. Maar een
    ophaalscript dat halverwege afbreekt laat een half geschreven bestand
    achter: het bestaat, en json.load klapt erop. Dan valt de hele bouw om op
    een bron die niet eens noodzakelijk is — precies wat er op GitHub gebeurde
    nadat de odds waren mislukt. Kapot is nu hetzelfde als afwezig, met een
    melding zodat het niet stil gebeurt.
    """
    pad = os.path.join(STATE, naam)
    if not os.path.exists(pad):
        return standaard
    try:
        return json.load(open(pad, encoding="utf-8"))
    except Exception as ex:
        print("  LET OP: %s is onleesbaar (%s) — wordt overgeslagen"
              % (naam, str(ex)[:80]))
        return standaard


# Hoeveel van een spelerspositie hangt aan aanvallende vs verdedigende fixture-kwaliteit.
# Gebaseerd op hoe FPL punten toekent: spitsen scoren vrijwel alleen aanvallend,
# keepers vrijwel alleen via clean sheets en saves.
POS_WEGING = {"FWD": (1.00, 0.00), "MID": (0.75, 0.25),
              "DEF": (0.35, 0.65), "GKP": (0.05, 0.95)}

# Hoeveel gameweeks vooruit we zoeken naar het beste moment voor een chip.
# Twaalf is een afweging: verder kijken kan technisch wel — ons eigen model
# vult alle 38 weken — maar FPL Copilot levert er acht, en daarna vallen de
# spelers zonder Premier League-historie (promovendi, verse aankopen, langdurig
# geblesseerden) terug op nul omdat er geen punten-per-90 is om op te bouwen.
# Het dashboard labelt daarom elke gameweek voorbij Copilots bereik apart.
CHIPVENSTER = 12

# Clubkleuren voor de shirts op het veld: [hoofdkleur, accent].
# Overgenomen van de officiele tenues 26/27.
CLUBKLEUR = {
    "ARS": ["#EF0107", "#FFFFFF"], "AVL": ["#95BFE5", "#670E36"], "BOU": ["#DA291C", "#000000"],
    "BRE": ["#E30613", "#FFFFFF"], "BHA": ["#0057B8", "#FFCD00"], "CHE": ["#034694", "#FFFFFF"],
    "COV": ["#59CBE8", "#FFFFFF"], "CRY": ["#1B458F", "#C4122E"], "EVE": ["#003399", "#FFFFFF"],
    "FUL": ["#FFFFFF", "#000000"], "HUL": ["#F5A12D", "#000000"], "IPS": ["#3A64A3", "#FFFFFF"],
    "LEE": ["#FFFFFF", "#1D428A"], "LIV": ["#C8102E", "#00B2A9"], "MCI": ["#6CABDD", "#1C2C5B"],
    "MUN": ["#DA291C", "#FBE122"], "NEW": ["#241F20", "#FFFFFF"], "NFO": ["#DD0000", "#FFFFFF"],
    "SUN": ["#EB172B", "#FFFFFF"], "TOT": ["#FFFFFF", "#132257"],
}


def get(u):
    return json.load(urllib.request.urlopen(
        urllib.request.Request(u, headers={"User-Agent": UA}), timeout=30))


def try_get(u):
    try:
        return get(u)
    except Exception:
        return None


def _last_sunday(y, m):
    d = datetime(y, m, 31)
    while d.weekday() != 6:
        d -= timedelta(days=1)
    return d


def to_nl(dt):
    a = _last_sunday(dt.year, 3).replace(hour=1, tzinfo=timezone.utc)
    b = _last_sunday(dt.year, 10).replace(hour=1, tzinfo=timezone.utc)
    return dt.astimezone(timezone(timedelta(hours=2 if a <= dt < b else 1)))


def build():
    now = datetime.now(timezone.utc)
    bs = get(BASE + "/bootstrap-static/")
    fixtures = get(BASE + "/fixtures/")
    ids = {}
    p = os.path.join(STATE, "ids.json")
    if os.path.exists(p):
        ids = json.load(open(p))
    entry_id, league_id = ids.get("entry_id"), ids.get("league_id")

    teams = {t["id"]: t for t in bs["teams"]}
    sn = {t["id"]: t["short_name"] for t in bs["teams"]}
    pos = {x["id"]: x["singular_name_short"] for x in bs["element_types"]}
    ev = next((e for e in bs["events"] if e.get("is_next")), None) \
        or next((e for e in bs["events"] if not e.get("finished")), bs["events"][0])
    gw = ev["id"]
    deadline = datetime.fromisoformat(ev["deadline_time"].replace("Z", "+00:00"))
    alle_gws = list(range(gw, 39))

    # ---- Opta-laag ----
    O = OPTA.bouw(alle_gws)
    teamdata, spelers_opta, fx_proj = O["teams"], O["spelers"], O["fixture_proj"]

    # gemiddelde xG en CS per club over de horizon, als ijkpunt voor fixture-aanpassing
    club_gem = {}
    for club, per_gw in fx_proj.items():
        xs = [c["xg"] for cs in per_gw.values() for c in cs]
        cz = [c["cs"] for cs in per_gw.values() for c in cs]
        club_gem[club] = (sum(xs) / len(xs) if xs else 1.0,
                          sum(cz) / len(cz) if cz else 25.0)

    # ---- FPL Copilot: ECHTE expected points van een FPL-site (primaire bron) ----
    # Justin wil xP van FPL-sites, niet van ons eigen model. Waar Copilot een speler
    # heeft, gebruiken we hun cijfer; ons model is alleen terugval voor de rest.
    xpp = os.path.join(STATE, "xp_copilot.json")
    copilot = _lees("xp_copilot.json", {"spelers": {}}) or {"spelers": {}}
    # Tweede, onafhankelijke puntenbron. Copilot vermenigvuldigt speelkans en
    # punten tot één getal; FPL Form houdt ze apart. Waar ze uiteenlopen zit
    # bijna altijd een meningsverschil over MINUTEN, niet over kwaliteit — en
    # dat is precies wat we willen zien in plaats van wegmiddelen.
    fplform = _lees("xp_fplform.json", {"spelers": {}}) or {"spelers": {}}
    # Fantasy Football Pundit: tweede bron met een cijfer PER gameweek, plus een
    # aparte startkans. Justin gaf aan FPL Form geen bruikbare maatstaf te vinden;
    # Pundit vervangt die rol. Waar FPL Form speelkans en punten tot één getal
    # vermenigvuldigde, houdt Pundit ze uit elkaar — en juist over minuten gaan
    # de meeste meningsverschillen tussen modellen.
    pundit = _lees("xp_pundit.json", {"spelers": {}}) or {"spelers": {}}
    # FPL Estimator: derde mening, maar als TOTAAL over een venster van vijf
    # gameweeks in plaats van per week. Die gebruiken we niet in het gemiddelde
    # per gameweek — dat zou een weekcijfer met een vijfweekscijfer optellen —
    # maar als onafhankelijke controle op de som over datzelfde venster.
    estimator = _lees("xp_estimator.json", {"spelers": {}}) or {"spelers": {}}
    cp_start = copilot.get("_start_gw", 1)
    # Klopt die startweek met waar we nu staan? Zo niet, dan staat elke
    # projectie verschoven en is elk advies fout. Dat mag nooit stil gebeuren.
    _volgende_gw = next((e["id"] for e in bs["events"]
                         if not e.get("finished") and not e.get("is_previous")), gw)
    cp_scheef = (cp_start != gw)
    if cp_scheef:
        print("  LET OP: Copilot begint bij GW%s terwijl de eerstvolgende gameweek GW%s is."
              % (cp_start, gw))
        print("          Draai copilot_ophalen.js opnieuw; anders staan alle projecties scheef.")
    # Tot hoe ver Copilot komt. Daarna is de projectie van ons eigen model,
    # en dat moet zichtbaar zijn in plaats van stilletjes doorlopen.
    cp_laatste = max((cp_start + len(v.get("gw") or []) - 1
                      for v in (copilot.get("spelers") or {}).values()),
                     default=cp_start - 1)

    # ---- Blessurelaag ----------------------------------------------------
    # FPL's eigen statusveld miste op 14-08-2026 zeventien van de tweeenveertig
    # spelers op de officiele Premier League-blessurepagina. Deze laag corrigeert dat.
    vmp = os.path.join(STATE, "vorm.json")
    vormdata = _lees("vorm.json", {}) or {}

    stp = os.path.join(STATE, "stand.json")
    stand = _lees("stand.json", {}) or {}

    arp = os.path.join(STATE, "league_archief.json")
    archief = _lees("league_archief.json", {}) or {}

    blp = os.path.join(STATE, "blessures.json")
    blessures = _lees("blessures.json", {}) or {}

    def _nrm(x):
        x = unicodedata.normalize("NFKD", x or "").encode("ascii", "ignore").decode().lower()
        return set(re.sub(r"[^a-z ]", " ", x).split())

    bl_index = {}
    for r in blessures.get("officieel_pl", []):
        bl_index.setdefault(r["club"], []).append(("blessure", r["speler"], r["blessure"], None))
    for r in blessures.get("los_geverifieerd", []):
        bl_index.setdefault(r["club"], []).append(("blessure", r["speler"], r["blessure"], r.get("uit_tot")))
    for r in blessures.get("schorsingen", []):
        bl_index.setdefault(r["club"], []).append(("schorsing", r["speler"], "schorsing", r.get("tot")))

    # ---- verwachte starters: overschrijft de minutenschatting waar de rol veranderd is ----
    sp = os.path.join(STATE, "starters.json")
    starters = _lees("starters.json", {"spelers": {}, "clubs": {}, "blessures": {}}) \
               or {"spelers": {}, "clubs": {}, "blessures": {}}

    # Spelers zonder Premier League-minuten (promovendi, verse buitenlandse aankopen)
    # zouden anders projectie 0 krijgen en nooit in een advies opduiken. Dat was precies
    # de Thomas-fout. Schat hun punten per 90 uit de mediaan van spelers met dezelfde
    # positie in dezelfde prijsklasse die WEL PL-data hebben. FPL's prijs is zelf een
    # marktinschatting van verwachte opbrengst, dus dat is een verdedigbare proxy.
    # Referentie voor spelers zonder eigen historie: de mediaan p90 van
    # positiegenoten in dezelfde prijsklasse. Die tabel werd gevuld uit spelers
    # met 900+ minuten DIT seizoen — en dat zijn er in gameweek 2 nul, dus viel
    # alles terug op een vaste positiewaarde. Nu wordt hij zo nodig uit de
    # momentopname van vorig seizoen gevuld.
    ref = defaultdict(list)
    for e in bs["elements"]:
        if e["minutes"] >= 900:
            ref[(pos[e["element_type"]], round(e["now_cost"] / 5) * 5)].append(
                e["total_points"] / e["minutes"] * 90.0)
    if sum(len(v) for v in ref.values()) < 40:
        _snappad0 = os.path.join(STATE, "seizoen_2025_26.json")
        if os.path.exists(_snappad0):
            try:
                for pid, v in (json.load(open(_snappad0, encoding="utf-8")).get("spelers") or {}).items():
                    m = v.get("minutes") or 0
                    if m < 900:
                        continue
                    e0 = next((x for x in bs["elements"] if x["id"] == int(pid)), None)
                    if not e0:
                        continue
                    ref[(pos[e0["element_type"]], round(e0["now_cost"] / 5) * 5)].append(
                        (v.get("total_points") or 0) / m * 90.0)
                print("  Referentie-p90 uit 2025/26 (dit seizoen nog te weinig minuten): "
                      "%d waarden" % sum(len(v) for v in ref.values()))
            except Exception:
                pass

    def geschat_p90(P, kosten):
        bucket = round(kosten / 5) * 5
        for delta in (0, 5, -5, 10, -10, 15, -15):
            v = ref.get((P, bucket + delta))
            if v and len(v) >= 3:
                v = sorted(v)
                return v[len(v) // 2]
        return {"GKP": 3.0, "DEF": 3.0, "MID": 3.2, "FWD": 3.4}[P]

    # ── Vorig seizoen erbij halen ────────────────────────────────────────
    # FPL's API draagt maar één seizoen tegelijk. Zodra gameweek 1 is
    # afgerond, overschrijft FPL de historische velden met de cijfers van dít
    # seizoen. Na één speelronde staat dan bij iedereen "1 start, 90 minuten".
    #
    # Dat sloopte de projectie volledig: min_factor werd 1/30 = 0,03 en p90
    # viel terug op een positiegemiddelde, waardoor elke gameweek voorbij
    # Copilots horizon dertig keer te laag uitkwam. Haaland stond op 0,3 punten
    # voor GW10 tot en met GW38 — en het chipadvies wees precies daarheen.
    #
    # De momentopname van 14-08-2026 heeft die cijfers wél. Die wordt hier
    # gemengd met dit seizoen: in het begin telt vorig seizoen bijna volledig,
    # naarmate er minuten bijkomen schuift het gewicht op.
    _snap_vorig = {}
    _snappad = os.path.join(STATE, "seizoen_2025_26.json")
    if os.path.exists(_snappad):
        try:
            for pid, v in (json.load(open(_snappad, encoding="utf-8")).get("spelers") or {}).items():
                _snap_vorig[int(pid)] = v
        except Exception as ex:
            print("  LET OP: momentopname vorig seizoen onleesbaar (%s)" % str(ex)[:60])

    def _historie(e):
        """Minuten, starts en punten om een p90 op te baseren.

        Geeft (minuten, starts, punten, bron) terug. Zolang dit seizoen te
        weinig minuten heeft, weegt vorig seizoen mee — evenredig aan hoeveel
        er dit seizoen al gespeeld is.
        """
        nu_min = e.get("minutes") or 0
        nu_st = e.get("starts") or 0
        nu_pt = e.get("total_points") or 0
        v = _snap_vorig.get(e["id"])
        if not v:
            return nu_min, nu_st, nu_pt, "dit seizoen"
        vo_min = v.get("minutes") or 0
        vo_st = v.get("starts") or 0
        vo_pt = v.get("total_points") or 0
        if vo_min < 90:
            return nu_min, nu_st, nu_pt, "dit seizoen"
        # weeg dit seizoen zwaarder naarmate er meer van gespeeld is; bij
        # 900 minuten (tien duels) telt alleen dit seizoen nog
        w = min(1.0, nu_min / 900.0)
        if w <= 0.01:
            return vo_min, vo_st, vo_pt, "2025/26"
        # samenvoegen op tarief, niet op totaal: anders telt een half seizoen
        # als een heel seizoen
        p90_nu = (nu_pt / nu_min * 90.0) if nu_min else 0.0
        p90_vo = (vo_pt / vo_min * 90.0) if vo_min else 0.0
        p90 = w * p90_nu + (1 - w) * p90_vo
        st_ratio = w * (nu_st / max(1, nu_min / 90.0)) + (1 - w) * (vo_st / max(1, vo_min / 90.0))
        gedacht_min = max(nu_min, 900)
        return (gedacht_min, round(st_ratio * gedacht_min / 90.0),
                round(p90 * gedacht_min / 90.0), "gemengd %d%% dit seizoen" % round(w * 100))

    # Schaalverhouding tussen de twee bronnen, per positie. Zonder dit middel je
    # appels met peren: FPL Form ligt structureel iets lager dan Copilot en dan
    # zouden spelers die maar bij één bron voorkomen kunstmatig beter of slechter
    # lijken. Gemeten op de spelers die BEIDE bronnen kennen, binnen Copilots horizon.
    # ---- BRONNENKEURING ----
    # Geen bron doet mee zonder controle tegen de LEVENDE FPL API. Dit is geen
    # theorie: FPL Prophet bleek op 31-08-2026 een compleet seizoen achter te
    # lopen (hun gameweek stond op 38, Haaland kostte er £14,7m en hun speler-id's
    # waren die van 25/26). Zonder keuring was dat gewoon meegemiddeld.
    keuringen = []
    _kf = BRONNEN.keur_spelerbron("FPL Copilot", copilot, bs)
    keuringen.append(_kf.dict())
    if not _kf.ok:
        print("  BRON GEWEIGERD - FPL Copilot: %s" % _kf.reden)
        copilot = {"spelers": {}}
    _kp = BRONNEN.keur_spelerbron("Pundit", pundit, bs)
    keuringen.append(_kp.dict())
    if not _kp.ok:
        print("  BRON GEWEIGERD - Pundit: %s" % _kp.reden)
        pundit = {"spelers": {}}
    if estimator.get("spelers"):
        _ke = BRONNEN.keur_spelerbron("FPL Estimator", estimator, bs, min_dekking=150)
        keuringen.append(_ke.dict())
        if not _ke.ok:
            print("  BRON GEWEIGERD - FPL Estimator: %s" % _ke.reden)
            estimator = {"spelers": {}}
    _gq = _lees("goaliq.json", {}) or {}
    _gq_uur = None
    if _gq.get("per_club"):
        _gq_uur = BRONNEN._leeftijd_uur(_gq.get("opgehaald"))
        keuringen.append({"bron": "GoalIQ", "ok": _gq_uur is None or _gq_uur <= 36,
                          "reden": "" if (_gq_uur is None or _gq_uur <= 36)
                                   else "%.0f uur oud" % _gq_uur,
                          "leeftijd_uur": round(_gq_uur, 1) if _gq_uur is not None else None,
                          "dekking": len(_gq["per_club"])})
    _od = _lees("odds.json", {}) or {}
    if _od.get("per_club"):
        _od_uur = BRONNEN._leeftijd_uur(_od.get("_opgehaald"))
        keuringen.append({"bron": "bookmakers (OddsPortal)",
                          "ok": _od_uur is None or _od_uur <= 36,
                          "reden": "" if (_od_uur is None or _od_uur <= 36)
                                   else "%.0f uur oud" % _od_uur,
                          "leeftijd_uur": round(_od_uur, 1) if _od_uur is not None else None,
                          "dekking": len(_od["per_club"])})
    for k in keuringen:
        print("  Bron %-24s %s%s" % (k["bron"], "toegelaten" if k["ok"] else "GEWEIGERD",
                                     (" - " + k["reden"]) if k["reden"] else ""))

    # ---- schaalcorrectie: Pundit op het niveau van Copilot ----
    # Twee modellen die hetzelfde meten liggen zelden op dezelfde schaal. Zonder
    # correctie middel je appels met peren en schuift elk advies naar de bron die
    # toevallig hoger uitkomt.
    #
    # Twee vallen zitten hierin, en ik ben in allebei gelopen.
    #
    # De eerste: middelen over ALLE gedeelde spelers. Dan absorbeer je
    # meningsverschillen over SPEELTIJD in wat een schaalfactor heet. Copilot gaf
    # Mosquera 8,4 punten over zes weken, Pundit 26,8 — dat is geen schaal, dat is
    # onenigheid over of hij speelt. Zulke spelers trokken de factor voor
    # middenvelders naar 0,55, waarna Pundit voor IEDEREEN met 45% werd gekort.
    #
    # De tweede: het gemiddelde nemen. Eén uitschieter verpest die. Daarom de
    # mediaan, en alleen over spelers waarvan BEIDE bronnen zeggen dat hij vast
    # speelt. Dan meet je zuiver de schaal. Zo gemeten liggen de bronnen 0,86 tot
    # 0,92 uit elkaar over alle posities — bescheiden en consistent, precies wat
    # een schaalverschil hoort te zijn.
    ff_ratio = {}
    if pundit.get("spelers"):
        import statistics as _st
        _paren = {}
        for _e in bs["elements"]:
            _cp = copilot.get("spelers", {}).get(str(_e["id"]))
            _pd = pundit.get("spelers", {}).get(str(_e["id"]))
            if not _cp or not _pd:
                continue
            _gs = [cp_start + i for i in range(len(_cp["gw"]))]
            _gedeeld = [g for g in _gs if str(g) in (_pd.get("xp") or {})]
            if not _gedeeld:
                continue
            _h = sum(float(_cp["gw"][g - cp_start]) for g in _gedeeld)
            _f = sum(float(_pd["xp"][str(g)]) for g in _gedeeld)
            if _h <= 4 or _f <= 4:
                continue
            _cpmin = (_cp.get("mn") or [0])[0] or 0
            _stk = (_pd.get("start") or {}).get(str(_gedeeld[0]), 0)
            if _cpmin < 70 or _stk < 0.70:
                continue                      # geen eensgezindheid over speeltijd
            _paren.setdefault(pos[_e["element_type"]], []).append(_h / _f)
        for _P, _v in _paren.items():
            if len(_v) >= 10:
                ff_ratio[_P] = max(0.5, min(2.0, _st.median(_v)))
        if _paren:
            print("  Pundit geschaald naar Copilot-niveau (mediaan over spelers die "
                  "volgens beide bronnen vast spelen): "
                  + ", ".join("%s %.3f (n=%d)" % (P, ff_ratio.get(P, 1.0), len(_paren[P]))
                              for P in sorted(_paren)))
    _ff_ratio_oud = {}
    if False and fplform.get("spelers"):
        _som = {}
        for _e in bs["elements"]:
            _cp = copilot.get("spelers", {}).get(str(_e["id"]))
            _ff = fplform.get("spelers", {}).get(str(_e["id"]))
            if not _cp or not _ff:
                continue
            _P = pos[_e["element_type"]]
            _gs = [cp_start + i for i in range(len(_cp["gw"]))]
            _h = sum(float(v) for v in _cp["gw"])
            _f = sum(float(_ff["xp"].get(str(g), 0)) for g in _gs)
            if _h > 4 and _f > 4:
                a, b, n = _som.get(_P, (0.0, 0.0, 0))
                _som[_P] = (a + _h, b + _f, n + 1)
        for _P, (a, b, n) in _som.items():
            if n >= 10 and b > 0:
                ff_ratio[_P] = max(0.5, min(2.0, a / b))
        print("  FPL Form geschaald naar Copilot-niveau: "
              + ", ".join("%s %.3f (n=%d)" % (P, ff_ratio.get(P, 1.0), _som[P][2])
                          for P in sorted(_som)))

    # ---- spelersdatabase met projectie PER gameweek ----
    db = []
    for e in bs["elements"]:
        P = pos[e["element_type"]]
        club = sn[e["team"]]
        # Niet de rauwe velden van de API: die dragen na gameweek 1 alleen nog
        # dit seizoen en zeggen dan niets. Zie _historie().
        mins, starts, hist_pt, hist_bron = _historie(e)
        p90 = (hist_pt / mins * 90.0) if mins >= 450 else 0.0
        p90_geschat = False
        # De prijsgrens van 45 (£4,5m) die hier stond sloot juist de klasse uit
        # waar promovendi-verdedigers zitten: een £4,0m speler met te weinig
        # minuten hield p90 = 0 en projecteerde dus 0,0 zodra Copilots horizon
        # ophield. Dat maakte elke goedkope inschuiver waardeloos op lange
        # horizon. De prijs zegt niets over of iemand speelt; de minuten wel.
        # Daarom: schat altijd, maar alleen bij bewijs dat hij het veld op komt.
        if p90 <= 0:
            _cp_ev = copilot.get("spelers", {}).get(str(e["id"]))
            _mn_ev = (_cp_ev.get("mn") or [None])[0] if _cp_ev else None
            speelt = (float(_mn_ev) >= 45 if _mn_ev else False) or (e.get("starts") or 0) >= 1
            if speelt:
                p90 = geschat_p90(P, e["now_cost"])
                p90_geschat = True
        # minutenverwachting uit starts vorig seizoen; promovendi hebben geen PL-historie
        # Copilot geeft per speler een verwachting van minuten per duel. Dat is
        # vooruitkijkend en dus beter dan tellen hoe vaak hij vorig jaar startte.
        _cp0 = copilot.get("spelers", {}).get(str(e["id"]))
        pd0 = pundit.get("spelers", {}).get(str(e["id"]))
        _cpmin0 = (_cp0.get("mn") or [None])[0] if _cp0 else None
        if _cpmin0:
            min_factor = max(0.0, min(1.0, float(_cpmin0) / 90.0))
        elif starts:
            min_factor = min(1.0, starts / 30.0)
        elif p90_geschat:
            # geen PL-historie: we weten niet of hij start. Bewust gedempt tot er
            # teamnieuws is; starters.json kan dit per speler overschrijven.
            min_factor = 0.55
        else:
            min_factor = 0.35 if mins else 0.15
        ov = starters.get("spelers", {}).get("%s|%s" % (e["web_name"], club))
        min_bron = None
        if ov:
            min_factor = float(ov["minuten_factor"])
            min_bron = {"reden": ov["reden"], "bron": ov["bron"], "datum": ov.get("datum")}
            # zonder bruikbare p90 (te weinig minuten) valt de projectie alsnog op nul terug;
            # gebruik dan het positiegemiddelde van spelers met een vergelijkbare prijs
            if p90 <= 0:
                p90 = {"GKP": 3.3, "DEF": 3.4, "MID": 3.6, "FWD": 3.8}[P]
        st = e.get("status", "a")
        if st in ("i", "s", "u"):
            besch = 0.0
        elif st == "d":
            besch = float(e.get("chance_of_playing_next_round") or 50) / 100.0
        else:
            besch = 1.0

        wa, wd = POS_WEGING[P]
        gem_xg, gem_cs = club_gem.get(club, (1.0, 25.0))
        per_gw, uitleg = {}, None
        for g in alle_gws:
            cellen = fx_proj.get(club, {}).get(g, [])
            tot = 0.0
            for c in cellen:
                fa = (c["xg"] / gem_xg) if gem_xg else 1.0
                fd = (c["cs"] / gem_cs) if gem_cs else 1.0
                tot += p90 * min_factor * besch * (wa * fa + wd * fd)
                if uitleg is None:   # bewaar de opbouw van de eerste gameweek als voorbeeld
                    uitleg = {"gw": g, "opp": c["opp"], "thuis": c["thuis"],
                              "p90": round(p90, 2), "minf": round(min_factor, 2),
                              "besch": besch, "wa": wa, "wd": wd,
                              "fa": round(fa, 2), "fd": round(fd, 2),
                              "xg": c["xg"], "cs": c["cs"],
                              "uit": round(p90 * min_factor * besch * (wa * fa + wd * fd), 2)}
            per_gw[g] = round(tot, 2)
        eigen_gw = dict(per_gw)

        # Copilot overschrijft onze berekening waar hij die speler kent.
        # Sinds 14-08-2026 is de koppeling op FPL-id in plaats van naam+club,
        # waardoor de dekking van 240 naar 581 van de 584 spelers ging.
        cp = copilot.get("spelers", {}).get(str(e["id"]))
        bron_proj = "eigen model"
        ff = fplform.get("spelers", {}).get(str(e["id"]))
        # Startkans voor de eerstvolgende gameweek. Kwam van FPL Form; komt nu van
        # Pundit, die hem als apart veld publiceert (`start_pct`) in plaats van
        # hem in het puntencijfer te verstoppen.
        ff_kans = None
        if pd0:
            _k = pd0.get("start") or {}
            ff_kans = _k.get(str(alle_gws[0])) if alle_gws else None
        if cp:
            bron_proj = "FPL Copilot"
            for i, v in enumerate(cp["gw"]):
                g = cp_start + i
                if g in per_gw:
                    per_gw[g] = float(v)
            # gameweeks voorbij Copilots horizon: schaal ons model zo dat het
            # aansluit op hun niveau, in plaats van een sprong in de reeks
            dekking = [cp_start + i for i in range(len(cp["gw"])) if cp_start + i in per_gw]
            if dekking:
                eigen = sum(round(p90 * min_factor * besch *
                                  (wa * ((c["xg"] / gem_xg) if gem_xg else 1) +
                                   wd * ((c["cs"] / gem_cs) if gem_cs else 1)), 2)
                            for g2 in dekking for c in fx_proj.get(club, {}).get(g2, []))
                hun = sum(float(v) for v in cp["gw"][:len(dekking)])
                sch = (hun / eigen) if eigen > 0.5 else 1.0
                sch = max(0.4, min(2.5, sch))
                for g2 in per_gw:
                    if g2 not in dekking:
                        per_gw[g2] = round(per_gw[g2] * sch, 2)

        # ---- BRONNEN VERZAMELEN ----
        # Hier alleen inzamelen, nog niet middelen. De schaalcorrectie voor ons
        # eigen model wordt pas na deze lus bepaald (die meet over alle spelers
        # heen), en middelen vóór dat moment zou appels met peren optellen.
        # Het mengen gebeurt verderop, in een tweede doorloop.
        bron_strijd = None
        bron_ruw = {}
        for g in list(per_gw):
            w = {}
            if cp:
                _i = g - cp_start
                if 0 <= _i < len(cp["gw"]):
                    w["FPL Copilot"] = round(float(cp["gw"][_i]), 2)
            if pd0 and str(g) in (pd0.get("xp") or {}):
                w["Pundit"] = round(float(pd0["xp"][str(g)]), 3)
            if eigen_gw.get(g) is not None:
                w["eigen model"] = round(float(eigen_gw[g]), 3)
            if w:
                bron_ruw[str(g)] = w
        eigen_schaal = sch if (cp and "sch" in dir()) else None

        # ---- CEILING EN FLOOR ----
        # xP is een gemiddelde. Voor de aanvoerder wil je juist weten wie kan uitschieten.
        # Berekend uit Poisson-kansen op goals en assists (Opta xG/xA per duel), de
        # clean-sheetkans van zijn ploeg en de DefCon-kans. Geen simulatie met willekeur:
        # de puntenverdeling wordt exact uitgerekend en daarvan nemen we percentielen.
        import math as _m
        duels = max(1, (mins or 0) / 90.0)
        xg_pd = (op["xg"] / duels) if (op := spelers_opta.get(e["id"])) and duels else 0.0
        xa_pd = (float(e.get("expected_assists") or 0) / duels) if duels else 0.0
        goal_ptn = {"GKP": 10, "DEF": 6, "MID": 5, "FWD": 4}[P]
        cs_ptn = {"GKP": 4, "DEF": 4, "MID": 1, "FWD": 0}[P]
        cel0 = fx_proj.get(club, {}).get(alle_gws[0], [])
        cs_kans = (cel0[0]["cs"] / 100.0) if cel0 else 0.25
        dc_kans = 0.0
        if P != "GKP" and (e.get("defensive_contribution_per_90") or 0):
            drem = 10 if P == "DEF" else 12
            dc_kans = min(0.9, max(0.0, (float(e["defensive_contribution_per_90"]) / drem) ** 2 * 0.5))

        def _pois(k, lam):
            return _m.exp(-lam) * lam ** k / _m.factorial(k) if lam > 0 else (1.0 if k == 0 else 0.0)

        verdeling = {}
        for g_n in range(0, 4):
            for a_n in range(0, 3):
                for cs in (0, 1):
                    for dc in (0, 1):
                        kans = (_pois(g_n, xg_pd) * _pois(a_n, xa_pd)
                                * (cs_kans if cs else 1 - cs_kans)
                                * (dc_kans if dc else 1 - dc_kans))
                        if kans < 1e-6:
                            continue
                        ptn = 2 + g_n * goal_ptn + a_n * 3 + (cs * cs_ptn) + (dc * 2)
                        verdeling[ptn] = verdeling.get(ptn, 0) + kans
        speelkans = min_factor * besch
        rij = sorted(verdeling.items())
        tot, cum, floor_v, ceil_v = sum(v for _, v in rij), 0.0, 0, 0
        for ptn, kans in rij:
            cum += kans / (tot or 1)
            if floor_v == 0 and cum >= 0.20:
                floor_v = ptn
            if ceil_v == 0 and cum >= 0.90:
                ceil_v = ptn
        floor_v = round(floor_v * speelkans)
        ceil_v = round(ceil_v * speelkans)

        op = spelers_opta.get(e["id"])
        db.append({
            "id": e["id"], "n": e["web_name"], "t": club, "tid": e["team"], "p": P,
            "c": e["now_cost"] / 10.0, "tp": e["total_points"],
            "ppg": float(e["points_per_game"] or 0), "p90": round(p90, 2),
            "own": float(e["selected_by_percent"] or 0),
            # Let op: mins/starts hierboven zijn de GEMENGDE rekenwaarden voor
            # de projectie. Wat je op het scherm ziet moet zijn wat er dit
            # seizoen werkelijk gebeurd is — anders staat Haaland na één duel
            # op 900 minuten en tien starts.
            "min": e.get("minutes") or 0, "st": e.get("starts") or 0,
            "g": e["goals_scored"], "a": e["assists"],
            # waar de projectie zijn tarief vandaan haalt, zodat dat navraagbaar is
            "histbron": hist_bron, "hist_min": mins,
            # Opta-cijfers gaan over 2025/26 en moeten dus door DAT aantal
            # duels gedeeld worden, niet door de duels van dit seizoen. Zonder
            # dit kreeg Haaland straks 25,39 xG over drie duels toebedeeld.
            "o_min": (_snap_vorig.get(e["id"], {}).get("minutes") or 0),
            "status": st, "news": (e.get("news") or "")[:110],
            # FPL's eigen speelkans, nodig om een twijfelgeval bij het
            # opstellen naar beneden te wegen in plaats van vol mee te tellen
            "speelkans": _getal(e.get("chance_of_playing_next_round")),
            "tin": e.get("transfers_in_event", 0), "tout": e.get("transfers_out_event", 0),
            # Prijsverandering. FPL publiceert sinds dit seizoen zelf een
            # voortgangsmeter (price_change_percent): hoe ver een speler is
            # richting de volgende stijging of daling. Dat is de officiele
            # bron; de exacte drempel houdt FPL geheim. We nemen hem over
            # zoals hij is en verzinnen er zelf geen kans bij.
            "pcp": _getal(e.get("price_change_percent")),
            "cc_gw": e.get("cost_change_event", 0),
            "cc_start": e.get("cost_change_start", 0),
            "dc": e.get("defensive_contribution") or 0,
            "dc90": round(float(e.get("defensive_contribution_per_90") or 0), 1),
            "cbi": e.get("clearances_blocks_interceptions") or 0,
            "tkl": e.get("tackles") or 0, "rec": e.get("recoveries") or 0,
            "bonus": e.get("bonus") or 0, "bps": e.get("bps") or 0,
            # per-90-cijfers: Justin wil verwachte goals per wedstrijd, niet per seizoen
            "xa": round(float(e.get("expected_assists") or 0), 2),
            "gc": e.get("goals_conceded") or 0,
            "geel": e.get("yellow_cards") or 0, "rood": e.get("red_cards") or 0,
            "drempel": 10 if P == "DEF" else (0 if P == "GKP" else 12),
            "eig_n": round(float(e["selected_by_percent"] or 0) / 100.0 * (bs.get("total_players") or 1)),
            "pen": e.get("penalties_order"), "cor": e.get("corners_and_indirect_freekicks_order"),
            "dfk": e.get("direct_freekicks_order"),
            "gw": per_gw,
            "uitleg": uitleg,
            "minf": round(min_factor, 2), "minbron": min_bron,
            "projbron": bron_proj,
            "ffkans": ff_kans,
            "bronstrijd": bron_strijd,
            "_ruw": bron_ruw or None,
            "_eigenschaal": eigen_schaal,
            "cpmin": (next((m for m in cp["mn"] if m is not None), None) if cp else None),
                        "floor": floor_v, "ceiling": ceil_v,
                        "p90geschat": p90_geschat,
            "wa": wa, "wd": wd,
            # Opta-velden: alleen aanwezig als er een match was
            "oxg": op["xg"] if op else None,
            "ogxg": op["g_min_xg"] if op else None,
            "oshots": op["shots"] if op else None,
            "oxgs": op["xg_per_shot"] if op else None,
            "oconv": op["conv"] if op else None,
        })
    # KALIBRATIE. Copilot en ons model liggen niet op dezelfde schaal; zonder correctie
    # lijken spelers zonder Copilot-cijfer kunstmatig beter in vergelijkingen.
    # Bepaal de verhouding op de spelers die beide hebben, per positie, en schaal
    # de rest daarmee. Zo zijn alle getallen in het dashboard onderling vergelijkbaar.
    kal_gws = [g for g in alle_gws[:8]]
    ratio_per_pos, kal_info = {}, {}
    for P in ("GKP", "DEF", "MID", "FWD"):
        hun, onze = 0.0, 0.0
        for d0 in db:
            if d0["projbron"] != "FPL Copilot" or d0["p"] != P:
                continue
            hun += sum(d0["gw"].get(g, 0) for g in kal_gws)
            onze += sum(d0.get("eigen", {}).get(g, 0) for g in kal_gws)
        r = (hun / onze) if onze > 1 else 1.0
        ratio_per_pos[P] = max(0.35, min(2.5, r))
        kal_info[P] = {"ratio": round(ratio_per_pos[P], 3),
                       "n": sum(1 for d0 in db if d0["projbron"] == "FPL Copilot" and d0["p"] == P)}
    for d0 in db:
        if d0["projbron"] == "FPL Copilot":
            continue
        r = ratio_per_pos.get(d0["p"], 1.0)
        d0["gw"] = {g: round(v * r, 2) for g, v in d0["gw"].items()}
        d0["kalibratie"] = round(r, 3)

    # ---- TWEEDE DOORLOOP: BRONNEN MIDDELEN MET HERKOMST ----
    # Nu alle schalen bekend zijn kan er gemiddeld worden. Elk getal dat hierna
    # in het dashboard staat is het gewogen gemiddelde van de bronnen die het
    # kennen, en draagt in `gwbron` mee hoe het is opgebouwd — dat is wat je ziet
    # als je op een projectie hovert.
    #
    # Wegingen. De twee externe modellen krijgen vol gewicht; ons eigen model telt
    # half mee, want het weet niets van blessures, rolwijzigingen of teamnieuws.
    # Het is een stem, geen doorslaggevende.
    #
    # Eén uitzondering op het middelen: zegt FPL Form dat iemand vrijwel zeker NIET
    # speelt (kans onder 15%), dan is dat een uitspraak over een feit — geblesseerd,
    # geschorst, niet in de selectie — en geen modelmening. Middelen zou een speler
    # die niet op het veld staat alsnog punten geven.
    # Twee externe modellen tellen vol mee. Ons eigen model telt licht: het weet
    # niets van blessures, rolwijzigingen of teamnieuws en is er alleen om gaten
    # te vullen waar geen enkele bron iets zegt. Justin vroeg terecht waarom een
    # cijfer dat hij niet kan navertellen even zwaar zou wegen als een model dat
    # de markt verwerkt. Antwoord: dat hoort niet, en nu is het ook zo.
    GEWICHT = {"FPL Copilot": 1.0, "Pundit": 1.0, "eigen model": 0.35}
    gemengd_n = strijd_n = gevolgd_n = 0
    for d0 in db:
        ruw = d0.pop("_ruw", None)
        eigen_schaal = d0.pop("_eigenschaal", None)
        d0["gwbron"] = None
        if not ruw:
            continue
        P = d0["p"]
        f_sch = ff_ratio.get(P, 1.0)
        e_sch = eigen_schaal if eigen_schaal else ratio_per_pos.get(P, 1.0)
        kans = d0.get("ffkans")
        niet_speler = kans is not None and kans < 0.15
        toon, strijd_tekst = {}, None
        # `gw` gebruikt dezelfde sleutels als de rest van het bestand; die kunnen
        # geheel getal of tekst zijn. Door elkaar heen schrijven laat json.dumps
        # struikelen op het sorteren, dus we nemen over wat er al ligt.
        _sleutel = (lambda x: int(x)) if any(isinstance(k, int) for k in d0["gw"]) \
                   else (lambda x: str(x))
        for g, w in ruw.items():
            op_schaal = {}
            if "FPL Copilot" in w:
                op_schaal["FPL Copilot"] = w["FPL Copilot"]
            if "Pundit" in w:
                op_schaal["Pundit"] = round(w["Pundit"] * f_sch, 2)
            if "eigen model" in w:
                op_schaal["eigen model"] = round(w["eigen model"] * e_sch, 2)
            if not op_schaal:
                continue
            if niet_speler and "Pundit" in op_schaal:
                d0["gw"][_sleutel(g)] = op_schaal["Pundit"]
            else:
                gem = BRONNEN.meng(op_schaal, GEWICHT)
                d0["gw"][_sleutel(g)] = gem["waarde"]
                # De vlag gaat alleen over de EXTERNE bronnen. Dat ons eigen model
                # ergens anders uitkomt is te verwachten en zou de vlag betekenisloos
                # maken; twee externe modellen die elkaar tegenspreken is het signaal
                # dat je wilt zien.
                if strijd_tekst is None and int(g) in alle_gws[:6]:
                    extern = {k: v for k, v in op_schaal.items() if k != "eigen model"}
                    if len(extern) > 1:
                        strijd_tekst = BRONNEN.strijd(BRONNEN.meng(extern), 0.30)
            if len(op_schaal) > 1:
                toon[g] = op_schaal
        if toon:
            d0["gwbron"] = toon
            gemengd_n += 1
            eerste = toon.get(str(alle_gws[0])) or list(toon.values())[0]
            d0["projbron"] = "%d bronnen: %s" % (len(eerste), ", ".join(sorted(eerste)))
        if niet_speler:
            d0["bronstrijd"] = ("Pundit: startkans %.0f%% — die bron gevolgd, "
                                "niet gemiddeld" % (kans * 100))
            gevolgd_n += 1
        elif strijd_tekst:
            d0["bronstrijd"] = strijd_tekst
            strijd_n += 1
    print("  Bronnen gemengd: %d spelers met 2+ bronnen, %d met een echt "
          "meningsverschil, %d waar de startkans onder 15%% ligt"
          % (gemengd_n, strijd_n, gevolgd_n))

    dbmap = {d["id"]: d for d in db}

    # ---- fixture ticker: FDR + xG + clean sheet, alle gameweeks ----
    ticker = []
    for tid, club in sn.items():
        cellen = {}
        for g in alle_gws:
            cs = fx_proj.get(club, {}).get(g, [])
            cellen[g] = [{"opp": c["opp"], "h": c["thuis"], "fdr": c["fdr"],
                          "xg": c["xg"], "xga": c.get("xga"), "cs": c["cs"],
                          "bronxg": c.get("bronxg"), "broncs": c.get("broncs"),
                          "bron": c.get("bron", "model"),
                          "eigen_xg": c.get("eigen_xg"), "eigen_cs": c.get("eigen_cs"),
                          "kick": c.get("kick")} for c in cs]
        td = teamdata.get(club, {})
        ticker.append({"team": club, "cellen": cellen,
                       "xgf": td.get("xgf"), "xga": td.get("xga"),
                       "bron": td.get("bron"), "toelichting": td.get("toelichting")})

    # ---- mijn squad ----
    squad, picks_gw, entry_meta = [], None, None
    # Een nog niet bevestigd concept voor DEZE gameweek gaat voor op de laatst
    # bevestigde picks. Zonder dit toont het dashboard tijdens een openstaande
    # wildcard nog altijd het oude elftal, want de API kent de draft niet.
    _concept = False
    _tp0 = os.path.join(STATE, "team.json")
    if os.path.exists(_tp0):
        try:
            if int((json.load(open(_tp0, encoding="utf-8")) or {}).get("concept_gw") or 0) == gw:
                _concept = True
                print("  Concept voor GW%d uit team.json gaat voor op de bevestigde picks" % gw)
        except Exception:
            pass
    if entry_id and not _concept:
        entry_meta = try_get("%s/entry/%s/" % (BASE, entry_id))
        for g in (gw - 1, gw):
            if g < 1:
                continue
            pk = try_get("%s/entry/%s/event/%d/picks/" % (BASE, entry_id, g))
            if pk and pk.get("picks"):
                squad = [dict(dbmap[x["element"]], slot=x["position"],
                              cap=x["multiplier"] >= 2, vice=bool(x.get("is_vice_captain")))
                         for x in pk["picks"] if x["element"] in dbmap]
                picks_gw = g
    if _concept and entry_id and entry_meta is None:
        entry_meta = try_get("%s/entry/%s/" % (BASE, entry_id))
    if not squad:
        tp = os.path.join(STATE, "team.json")
        if os.path.exists(tp):
            tj = json.load(open(tp))
            # De aanvoerder stond alleen in een `rol`-veld per speler. Wordt het
            # bestand met de hand bijgewerkt en schrijft iemand hem bovenaan als
            # "aanvoerder": "Haaland", dan zag het dashboard geen aanvoerder en
            # rekende het de hele gameweek zonder dubbele punten. Nu tellen beide
            # schrijfwijzen, met het rolveld als de sterkste.
            _capnaam = (tj.get("aanvoerder") or "").lower()
            _vicenaam = (tj.get("vice") or "").lower()
            n = 1
            for grp in ("basis", "bank"):
                for pl in tj.get(grp, []) or []:
                    d = next((x for x in db if x["n"].lower() == pl["naam"].lower()
                              and x["t"] == pl["club"]), None)
                    if d:
                        nm = (pl.get("naam") or "").lower()
                        squad.append(dict(
                            d, slot=n,
                            cap=(pl.get("rol") == "captain") or (bool(_capnaam) and nm == _capnaam),
                            vice=(pl.get("rol") == "vice-captain") or (bool(_vicenaam) and nm == _vicenaam)))
                    n += 1
            if not any(p.get("cap") for p in squad):
                print("  LET OP: geen aanvoerder in team.json — zet 'rol': 'captain' "
                      "bij een speler of 'aanvoerder': '<naam>' bovenaan het bestand")

    # ---- mini-league ----
    league = None
    if league_id:
        stz = try_get("%s/leagues-classic/%s/standings/" % (BASE, league_id))
        if stz:
            res = stz["standings"]["results"]
            nieuw = stz.get("new_entries", {}).get("results", [])
            league = {"naam": stz["league"]["name"],
                      "rijen": [{"rank": r["rank"], "team": r["entry_name"],
                                 "manager": r["player_name"], "totaal": r["total"],
                                 "gw": r["event_total"], "ik": r["entry"] == entry_id}
                                for r in res],
                      "nieuw": [{"team": r.get("entry_name"),
                                 "manager": ("%s %s" % (r.get("player_first_name", ""),
                                                        r.get("player_last_name", ""))).strip(),
                                 "ik": r.get("entry") == entry_id} for r in nieuw]}
            if res and gw > 1:
                own, cap, n = defaultdict(int), defaultdict(int), 0
                for r in res:
                    pk = try_get("%s/entry/%s/event/%d/picks/" % (BASE, r["entry"], gw - 1))
                    if not pk:
                        continue
                    n += 1
                    # volledige teamsheet per rivaal, zodat je erop kunt klikken
                    rij = next((x for x in league["rijen"] if x.get("team") == r["entry_name"]), None)
                    if rij is not None:
                        rij["squad"] = [{"n": dbmap[x["element"]]["n"], "t": dbmap[x["element"]]["t"],
                                         "p": dbmap[x["element"]]["p"], "c": dbmap[x["element"]]["c"],
                                         "bank": x["position"] > 11, "cap": x["multiplier"] >= 2}
                                        for x in pk["picks"] if x["element"] in dbmap]
                        rij["chip"] = pk.get("active_chip")
                    for x in pk["picks"]:
                        if x["position"] <= 11:
                            own[x["element"]] += 1
                        if x["multiplier"] >= 2:
                            cap[x["element"]] += 1
                # ── Awards: wie deed wat goed of fout in de afgelopen ronde ──
                # Alles hieronder komt uit de werkelijke picks van elke manager
                # plus de live puntenstand van die gameweek. Niets geschat.
                gwlive = try_get("%s/event/%d/live/" % (BASE, gw - 1)) or {}
                punten = {e["id"]: e["stats"]["total_points"] for e in gwlive.get("elements", [])}
                mins = {e["id"]: e["stats"]["minutes"] for e in gwlive.get("elements", [])}
                deelnemers = []
                for r in res:
                    pk = try_get("%s/entry/%s/event/%d/picks/" % (BASE, r["entry"], gw - 1))
                    hist = try_get("%s/entry/%s/history/" % (BASE, r["entry"]))
                    if not pk:
                        continue
                    xi = [x for x in pk["picks"] if x["position"] <= 11]
                    bank = [x for x in pk["picks"] if x["position"] > 11]
                    capp = next((x for x in pk["picks"] if x["multiplier"] >= 2), None)
                    vice = next((x for x in pk["picks"] if x.get("is_vice_captain")), None)
                    hev = (hist or {}).get("current", [])
                    dezeGw = next((h for h in hev if h["event"] == gw - 1), {})
                    vorige = next((h for h in hev if h["event"] == gw - 2), None)
                    # Bankpunten: wat er op de bank bleef liggen. Met een Bench
                    # Boost telden die punten juist WEL mee — dan is het geen
                    # gemiste kans maar de opbrengst van je chip. Beide getallen
                    # apart houden, want het is hetzelfde cijfer met een
                    # tegenovergestelde betekenis.
                    chip_actief = pk.get("active_chip")
                    bankpt = sum(punten.get(x["element"], 0) for x in bank
                                 if mins.get(x["element"], 0) > 0)
                    capId = capp["element"] if capp else None
                    besteXIpt = max((punten.get(x["element"], 0) for x in xi), default=0)
                    deelnemers.append({
                        "team": r["entry_name"], "manager": r["player_name"],
                        "ik": r["entry"] == entry_id,
                        "punten": dezeGw.get("points"),
                        "rang": r["rank"], "vorige_rang": r.get("last_rank"),
                        "transfers": dezeGw.get("event_transfers", 0),
                        "hits": dezeGw.get("event_transfers_cost", 0),
                        # gemist = op de bank blijven liggen; opbrengst = door de chip
                        # alsnog binnengehaald. Nooit allebei tegelijk.
                        "bankpunten": 0 if chip_actief == "bboost" else bankpt,
                        "bank_opbrengst": bankpt if chip_actief == "bboost" else 0,
                        "chip": chip_actief,
                        "cap": dbmap[capId]["n"] if capId in dbmap else None,
                        "cap_punten": punten.get(capId, 0) if capId else 0,
                        "beste_mogelijk": besteXIpt,
                        "vice": dbmap[vice["element"]]["n"] if vice and vice["element"] in dbmap else None,
                        "xi": [x["element"] for x in xi],
                        "alle15": [x["element"] for x in pk["picks"]],
                        "waarde": (dezeGw.get("value") or 0) / 10.0,
                        "opDeBank": (dezeGw.get("bank") or 0) / 10.0,
                    })
                if deelnemers:
                    # hoe uniek is elke selectie binnen deze league?
                    # Uniek betekent: niemand anders in de league HEEFT hem.
                    # Dit telde alleen de basiselftallen, dus stond White als
                    # differential terwijl drie anderen hem op de bank hadden.
                    # Voor het bezit tellen alle vijftien; voor de punten telt
                    # alleen wie hem ook opstelde.
                    tel = defaultdict(int)
                    for d in deelnemers:
                        for pid in set(d["alle15"]):
                            tel[pid] += 1
                    for d in deelnemers:
                        eigen = [pid for pid in d["xi"] if tel[pid] == 1]
                        d["uniek"] = len(eigen)
                        d["uniek_punten"] = sum(punten.get(pid, 0) for pid in eigen)
                        # wie die spelers zijn: een aantal zonder namen zegt niets
                        d["uniek_namen"] = sorted(
                            [{"n": dbmap[pid]["n"], "t": dbmap[pid]["t"],
                              "pt": punten.get(pid, 0)} for pid in eigen if pid in dbmap],
                            key=lambda x: -x["pt"])
                        d["gemist"] = max(0, d["beste_mogelijk"] * 2 - d["cap_punten"] * 2) \
                            if d["cap_punten"] < d["beste_mogelijk"] else 0
                        # de beste speler in zijn elftal, zodat de blunder een
                        # naam krijgt in plaats van alleen een getal
                        besteId = max(d["xi"], key=lambda pid: punten.get(pid, 0), default=None)
                        d["beste_naam"] = dbmap[besteId]["n"] if besteId in dbmap else None
                    # ── Extra cijfers voor een gameweek-terugblik ────────
                    # Alles hieronder komt uit dezelfde picks; niets geschat.
                    aantal_m = len(deelnemers)
                    top_punten = max((d["punten"] or 0) for d in deelnemers)
                    for d in deelnemers:
                        eigen15 = set(d["alle15"])
                        xi_set = set(d["xi"])
                        # hoe erg leunde hij op één club?
                        clubtel = defaultdict(int)
                        for pid in d["xi"]:
                            if pid in dbmap:
                                clubtel[dbmap[pid]["t"]] += 1
                        if clubtel:
                            club, n_club = max(clubtel.items(), key=lambda kv: kv[1])
                            d["club_zwaar"] = {"club": club, "n": n_club,
                                               "punten": sum(punten.get(pid, 0) for pid in d["xi"]
                                                             if pid in dbmap and dbmap[pid]["t"] == club)}
                        # de gok die uitpakte: laagst bezeten speler in zijn XI
                        # die wel scoorde, gemeten binnen deze league
                        kans = [(tel[pid], punten.get(pid, 0), pid) for pid in d["xi"] if pid in dbmap]
                        kans = [k for k in kans if k[1] >= 6]
                        if kans:
                            kans.sort(key=lambda k: (k[0], -k[1]))
                            bez, pt_, pid = kans[0]
                            d["gok"] = {"n": dbmap[pid]["n"], "t": dbmap[pid]["t"],
                                        "pt": pt_, "bezit": bez, "van": aantal_m}
                        # achterstand op de nummer 1 van de week
                        d["achterstand"] = top_punten - (d["punten"] or 0)
                        # hoeveel van zijn elftal deelde hij met de rest?
                        gedeeld_tot = sum(tel[pid] - 1 for pid in eigen15 if pid in tel)
                        d["gelijkenis"] = round(gedeeld_tot / max(1, (aantal_m - 1) * 15) * 100)
                        # wie op zijn bank scoorde het hardst (los van chip)
                        bankp = [(punten.get(pid, 0), pid) for pid in eigen15 - xi_set]
                        if bankp:
                            bankp.sort(reverse=True)
                            pt_, pid = bankp[0]
                            if pid in dbmap:
                                d["bank_top"] = {"n": dbmap[pid]["n"], "pt": pt_}
                    # welke speler had bijna iedereen, en wat deed hij?
                    if tel:
                        meest = max(tel.items(), key=lambda kv: kv[1])
                        if meest[0] in dbmap and meest[1] > 1:
                            league["template"] = {
                                "n": dbmap[meest[0]]["n"], "t": dbmap[meest[0]]["t"],
                                "bezit": meest[1], "van": aantal_m,
                                "pt": punten.get(meest[0], 0)}
                    # de sterkste speler die NIEMAND had
                    had_iemand = set(tel)
                    gemist_all = [(v, k) for k, v in punten.items()
                                  if k not in had_iemand and k in dbmap and v >= 6]
                    if gemist_all:
                        gemist_all.sort(reverse=True)
                        pt_, pid = gemist_all[0]
                        league["niemand_had"] = {"n": dbmap[pid]["n"], "t": dbmap[pid]["t"], "pt": pt_}
                    league["awards_gw"] = gw - 1
                    league["deelnemers"] = deelnemers
                if n:
                    eo = [{"n": dbmap[pid]["n"], "t": dbmap[pid]["t"], "p": dbmap[pid]["p"],
                           "eo": round(100.0 * (c + cap[pid]) / n),
                           "capt": round(100.0 * cap[pid] / n),
                           "wereld": dbmap[pid]["own"],
                           "mijn": any(s["id"] == pid for s in squad)}
                          for pid, c in own.items() if pid in dbmap]
                    eo.sort(key=lambda x: -x["eo"])
                    league["eo"], league["eo_n"], league["eo_gw"] = eo[:24], n, gw - 1

    # ---- seizoenslaag: momentopname 25/26 naast de live cijfers ----
    gespeeld = sum(1 for x in bs["events"] if x.get("finished"))
    snp = os.path.join(STATE, "seizoen_2025_26.json")
    _vorig = {}
    if os.path.exists(snp):
        _sn = json.load(open(snp))
        for pid, r in _sn.get("spelers", {}).items():
            mn = r.get("minutes") or 0
            _vorig[pid] = {
                "tp": r.get("total_points"), "min": mn, "st": r.get("starts"),
                "g": r.get("goals_scored"), "a": r.get("assists"),
                "xg": r.get("expected_goals"), "xa": r.get("expected_assists"),
                "dc": r.get("defensive_contribution"), "cbi": r.get("clearances_blocks_interceptions"),
                "tkl": r.get("tackles"), "rec": r.get("recoveries"),
                "bonus": r.get("bonus"), "bps": r.get("bps"),
                "cs": r.get("clean_sheets"), "gc": r.get("goals_conceded"),
                "geel": r.get("yellow_cards"), "rood": r.get("red_cards"),
                "ppg": r.get("points_per_game")}

    # ---- blessurelaag over de FPL-status heen ----
    extern_geflagd = 0
    for d0 in db:
        for soort, naam, wat, tot in bl_index.get(d0["t"], []):
            if _nrm(naam) & _nrm(d0["n"]):
                d0["extern"] = {"soort": soort, "wat": wat, "tot": tot,
                                "bron": "Premier League" if soort == "blessure" else "FPL"}
                if d0["status"] == "a" and not d0["news"]:
                    extern_geflagd += 1
                    d0["status"] = "d"          # twijfel; FPL wist het nog niet
                    d0["news"] = "%s (%s) — niet gemeld door FPL, wel op de officiele PL-blessurepagina" % (
                        wat, soort)
                break

    def _gwv(speler, g):
        """Waarde voor gameweek g. De sleutels van 'gw' zijn hier nog ints;
        pas na de JSON-serialisatie worden het strings. Beide afvangen."""
        d = speler.get("gw") or {}
        v = d.get(g)
        return v if v is not None else (d.get(str(g)) or 0)

    # ---- chipteams: wildcard en free hit ----
    # Twee wezenlijk verschillende vragen:
    #   Free Hit  — het team geldt EEN gameweek en verdwijnt daarna. Toekomstige
    #               fixtures doen er niet toe; maximaliseer die ene week.
    #   Wildcard  — het team blijft staan. Optimaliseer over meerdere gameweeks,
    #               anders koop je spelers met een makkelijke openingsweek en een
    #               beroerd programma daarna.
    chipteams = None
    try:
        if _hergebruik and _cache.get("chipteams"):
            chipteams = _cache["chipteams"]
            raise StopIteration
        import bouwer as BW
        budget_nu = ((sum(x["c"] for x in squad) +
                      ((entry_meta or {}).get("last_deadline_bank") or 0) / 10.0)
                     if squad else 100.0)
        chipteams = {"_budget": round(budget_nu, 1), "varianten": {}}
        for sleutel, horizon, naam in (("freehit", 1, "Free Hit"),
                                       ("wildcard5", 5, "Wildcard over 5"),
                                       ("wildcard8", 8, "Wildcard over 8")):
            r = BW.bouw(db, budget_nu, gw, horizon, breedte=40)
            if not r:
                continue
            xp, clubs, ids, kost = r
            sp = [dbmap[i] for i in ids if i in dbmap]
            chipteams["varianten"][sleutel] = {
                "naam": naam, "horizon": horizon,
                "xp": round(xp, 1), "kosten": kost / 10.0, "ids": list(ids),
                "spelers": [{"id": x["id"], "n": x["n"], "t": x["t"], "p": x["p"],
                             "c": x["c"],
                             "gw": {str(g): _gwv(x, g)
                                    for g in range(gw, min(39, gw + max(horizon, 5)))}}
                            for x in sp],
                # per gameweek, zodat je de vorm van het team ziet en niet alleen het totaal
                "per_gw": {str(g): round(sum(_gwv(x, g) for x in sp), 1)
                           for g in range(gw, min(39, gw + max(horizon, 5)))},
            }
        if squad:
            eigen = [dbmap[p_["id"]] for p_ in squad if p_["id"] in dbmap]
            chipteams["eigen_per_gw"] = {
                str(g): round(sum(_gwv(x, g) for x in eigen), 1)
                for g in range(gw, min(39, gw + 8))}

            # ── Wanneer is elke chip het meest waard, en met WELK team? ──
            #
            # Hier zat een echte fout in. Er werd per kandidaat-gameweek wel
            # uitgerekend hoeveel een chip daar zou opleveren, maar alleen het
            # TOTAAL werd bewaard. Het team dat het paneel liet zien was altijd
            # het team voor de huidige gameweek, met de punten van de huidige
            # gameweek. Stond het advies op "wachten tot GW4", dan zag je dus
            # de elf van GW1 met de xP van GW1 — twee getallen die niets met
            # elkaar te maken hadden. Nu bewaren we per gameweek het team dat
            # je in DIE week zou neerzetten, met de punten van DIE week.
            #
            # Tweede fout: het beste elftal werd genomen als "de elf hoogste
            # scores", zonder formatieregels. Dat kan een elftal zonder keeper
            # opleveren en telt dus te hoog — aan beide kanten van de
            # vergelijking. Nu via SOLVER.beste_xi, die 1 keeper, 3-5
            # verdedigers, 2-5 middenvelders en 1-3 aanvallers afdwingt en de
            # aanvoerder dubbel telt, precies zoals FPL het rekent.
            import solver as SOLVER
            import xi_opt as XI

            venster_eind = min(38, gw + CHIPVENSTER - 1)
            kandidaten_gw = list(range(gw, venster_eind + 1))

            # Basislijn voor de Wildcard: niet "je huidige team bevriezen",
            # maar "gewoon doorgaan met je vrije transfers". Anders lijkt elke
            # wildcard veel meer waard dan hij is, want zonder chip zou je die
            # weken ook niet stilzitten. Eén solverrun over het hele venster
            # levert de score per gameweek voor het beste normale pad.
            basis_per_gw = {}
            try:
                bank_nu = ((entry_meta or {}).get("last_deadline_bank") or 0) / 10.0
                gws_basis = [g for g in alle_gws if gw <= g <= min(38, venster_eind + 4)]
                paden = SOLVER.solve(db, eigen, bank_nu, gws_basis, beam=18, max_hits=1)
                if paden:
                    for st in paden[0]["pad"]:
                        basis_per_gw[st["gw"]] = st.get("sc", 0) - st.get("hit", 0)
            except Exception:
                basis_per_gw = {}
            # Lukt de solver niet, dan valt de basislijn terug op je eigen elftal
            # zonder transfers. Dat staat dan ook zo in de uitleg.
            basis_bron = "met normale transfers" if basis_per_gw else "zonder transfers"
            if not basis_per_gw:
                for g3 in range(gw, min(39, venster_eind + 5)):
                    basis_per_gw[g3] = SOLVER.beste_xi(eigen, g3)[0]

            def _team(ids, g_toon, gws_toon):
                """Het team, met de punten van de gameweek waar het advies over gaat."""
                sp = [dbmap[i] for i in ids if i in dbmap]
                sc, xi = SOLVER.beste_xi(sp, g_toon)
                xi_ids = {x["id"] for x in (xi or [])}
                cap = max(xi or sp, key=lambda x: _gwv(x, g_toon))
                return {
                    "ids": list(ids),
                    "formatie": "%d-%d-%d" % (
                        sum(1 for x in (xi or []) if x["p"] == "DEF"),
                        sum(1 for x in (xi or []) if x["p"] == "MID"),
                        sum(1 for x in (xi or []) if x["p"] == "FWD")),
                    "cap": cap["id"],
                    "spelers": [{"id": x["id"], "n": x["n"], "t": x["t"], "p": x["p"],
                                 "c": x["c"], "basis": x["id"] in xi_ids,
                                 # punten van de gameweek waarin je de chip speelt,
                                 # plus het verloop over het venster van de chip
                                 "xp": round(_gwv(x, g_toon), 2),
                                 "gw": {str(g4): round(_gwv(x, g4), 2) for g4 in gws_toon}}
                                for x in sp],
                }

            wanneer = {"freehit": [], "wildcard": []}
            for g2 in kandidaten_gw:
                # Free Hit: geldt precies één gameweek, dus bouwen op die week.
                # Deze lus draait twee bouwopdrachten per gameweek over twaalf
                # gameweeks: vierentwintig volledige optimalisaties. Op de hele
                # database met breedte 25 was dat veruit de duurste stap van de
                # verversing en de reden dat hij op GitHub in de tijdslimiet
                # liep. Voorfilteren geeft dezelfde uitkomst; een smallere
                # breedte kost hier hooguit een paar tienden op een getal dat
                # bedoeld is om weken te vergelijken, niet om op te acteren.
                kand1 = BW.voorfilter(db, g2, [g2])
                r1 = BW.bouw(kand1, budget_nu, g2, 1, breedte=12)
                if r1:
                    # De bouwer maximaliseert de som van vijftien; je stelt er elf
                    # op. Deze pas verschuift bankgeld naar de basis zolang de
                    # echte score stijgt. Zie xi_opt.py.
                    ids1, _v1, _n1 = XI.verbeter(db, r1[2], int(round(budget_nu * 10)), [g2])
                    sp1 = [dbmap[i] for i in ids1 if i in dbmap]
                    chip1 = SOLVER.beste_xi(sp1, g2)[0]
                    eigen1 = SOLVER.beste_xi(eigen, g2)[0]
                    rij = {"gw": g2, "chip": round(chip1, 1), "eigen": round(eigen1, 1),
                           "zeker": "copilot" if g2 <= cp_laatste else "eigen model"}
                    rij["team"] = _team(ids1, g2, [g2])
                    wanneer["freehit"].append(rij)

                # Wildcard: het team blijft staan, dus bouwen over vijf weken
                # vanaf die gameweek — en vergelijken over diezelfde vijf weken.
                venster = [g for g in range(g2, min(39, g2 + 5))]
                kand5 = BW.voorfilter(db, g2, venster)
                r5 = BW.bouw(kand5, budget_nu, g2, len(venster), breedte=12)
                if r5:
                    ids5, _v5, _n5 = XI.verbeter(db, r5[2], int(round(budget_nu * 10)), venster)
                    sp5 = [dbmap[i] for i in ids5 if i in dbmap]
                    chip5 = sum(SOLVER.beste_xi(sp5, g4)[0] for g4 in venster)
                    basis5 = sum(basis_per_gw.get(g4, 0) for g4 in venster)
                    rij = {"gw": g2, "chip": round(chip5, 1), "eigen": round(basis5, 1),
                           "venster": [venster[0], venster[-1]],
                           "zeker": "copilot" if venster[-1] <= cp_laatste else "eigen model"}
                    rij["team"] = _team(ids5, g2, venster)
                    wanneer["wildcard"].append(rij)

            chipteams["wanneer"] = wanneer
            chipteams["_venster"] = [kandidaten_gw[0], kandidaten_gw[-1]]
            chipteams["_cp_laatste"] = cp_laatste
            chipteams["_basis"] = basis_bron
        chipteams["_methode"] = (
            "bouwer.py met een horizon per chip: Free Hit over een gameweek, "
            "Wildcard over vijf en acht. Alle regels gelden: 2-5-5-3, maximaal drie "
            "per club, binnen het budget, en alleen spelers met minstens 45 verwachte "
            "minuten die niet geblesseerd of geschorst zijn.")
    except Exception as ex:
        chipteams = {"fout": str(ex)[:200]}

    # ---- beste selectie voor hetzelfde budget, als ijkpunt ----
    # ── Hoeveel vrije transfers heb je werkelijk? ────────────────────────
    # De pagina nam aan dat het er één is en telde daarna zelf verder. Dat
    # klopt alleen als je nooit iets hebt overgeslagen of juist extra hebt
    # gedaan. FPL geeft het getal niet rechtstreeks, maar het is exact af te
    # leiden uit je geschiedenis: vanaf gameweek 2 krijg je er elke week één
    # bij, je mag er maximaal vijf sparen, en een Wildcard of Free Hit laat de
    # teller ongemoeid.
    vrij_nu = None
    if entry_meta:
        hist_eigen = try_get("%s/entry/%s/history/" % (BASE, entry_id)) or {}
        chip_per_gw = {c.get("event"): c.get("name") for c in (hist_eigen.get("chips") or [])}
        eigen_gws = sorted([h for h in (hist_eigen.get("current") or [])],
                           key=lambda h: h.get("event") or 0)
        if eigen_gws:
            v = 1
            for h in eigen_gws:
                g = h.get("event")
                if g == 1:            # vóór de eerste deadline is alles gratis
                    v = 1
                    continue
                chip = chip_per_gw.get(g)
                if chip in ("wildcard", "freehit"):
                    continue          # kost niets en spaart niet op
                v = max(1, min(5, v - (h.get("event_transfers") or 0) + 1))
            # de eerstvolgende gameweek levert er nog één op
            laatste = eigen_gws[-1].get("event") or 0
            if chip_per_gw.get(laatste) not in ("wildcard", "freehit"):
                v = max(1, min(5, v))
            vrij_nu = v

    # ---- BUDGET: squadwaarde, verkoopprijzen en overwaarde ----
    # Hier ging het mis. Het dashboard telde overal de HUIDIGE prijzen op en
    # noemde dat je budget. Maar je budget is de som van je VERKOOPprijzen plus
    # je bank, en een verkoopprijs is lager dan de huidige prijs zodra een speler
    # gestegen is: van elke stijging houd je maar de helft. Zie prijzen.py.
    #
    # De API geeft ons `last_deadline_value`, en dat is de squadwaarde bij de
    # VORIGE deadline — niet die van vandaag. Prijzen bewegen elke nacht, dus dat
    # getal loopt altijd achter. Daarom mag team.json een `budget_fpl` dragen:
    # het bedrag dat FPL zelf op de Transfers-pagina toont. Dat is het enige
    # actuele, harde getal dat er is, en het gaat voor op onze eigen som.
    budget_info = None
    if entry_meta:
        _tj = _lees("team.json", {}) or {}
        _bf = _tj.get("budget_fpl") or {}
        _huidig = round(sum(p["c"] for p in squad), 1) if squad else None
        _waarde = (entry_meta.get("last_deadline_value") or 0) / 10.0
        _bank = (entry_meta.get("last_deadline_bank") or 0) / 10.0
        budget_info = {
            "squadwaarde_deadline": round(_waarde, 1),
            "bank": round(_bank, 1),
            "huidige_prijzen": _huidig,
            "bron": "FPL API /entry/%s/ (last_deadline_value + last_deadline_bank)" % entry_id,
            "achterstand": ("last_deadline_value is de waarde bij de vorige deadline; "
                            "prijzen bewegen dagelijks, dus dit loopt achter"),
        }
        # Zelfconsistent: wat je selectie NU kost plus wat er in de bank ligt.
        # Alleen dat laatste getal kunnen we niet zelf bepalen — daarvoor zouden
        # we je aankoopprijzen moeten kennen — dus dat lezen we af bij FPL.
        # Zo blijft het kloppen ook als prijzen 's nachts bewegen: beide kanten
        # schuiven dan mee.
        if _bf.get("bank") is not None and _huidig is not None:
            budget_info.update({
                "bank_nu": float(_bf["bank"]),
                "totaal": round(_huidig + float(_bf["bank"]), 1),
                "gezien_op": _bf.get("gezien_op"),
                "bron": "selectie plus de bank die FPL toont (%s)" % _bf.get("gezien_op", "?"),
                "uitleg": _bf.get("_uitleg"),
            })
        elif _bf.get("totaal"):
            budget_info.update({
                "totaal": float(_bf["totaal"]),
                "gezien_op": _bf.get("gezien_op"),
                "bron": "afgelezen bij FPL zelf op %s" % _bf.get("gezien_op", "?"),
                "uitleg": _bf.get("_uitleg"),
            })
        else:
            budget_info["totaal"] = round(_waarde + _bank, 1)
        if _huidig is not None:
            budget_info["overwaarde"] = round(_huidig - budget_info["totaal"], 1)
        print("  Budget: £%.1fm te besteden, selectie kost £%.1fm (%s)"
              % (budget_info["totaal"], _huidig or 0, budget_info["bron"]))

    # ---- TERUGBLIK: je eigen afgelopen gameweeks ----
    # Het dashboard keek alleen vooruit. Wat er in de weken ervoor gebeurde stond
    # nergens, terwijl juist dat je vertelt of een keuze werkte. Per afgelopen
    # gameweek: je punten, je rang, wat er op de bank bleef liggen, welke chip er
    # aan stond, en de volledige opstelling met punten per speler.
    #
    # De opstellingen worden gecachet: de API geeft ze wel, maar een afgelopen
    # gameweek verandert niet meer, dus die halen we precies één keer op.
    terugblik = []
    if entry_meta and entry_id:
        _pcache = _lees("eigen_picks.json", {"gws": {}}) or {"gws": {}}
        _pgws = _pcache.get("gws") or {}
        _nieuw = False
        hist_speler = (_lees("historie.json", {}) or {}).get("spelers") or {}
        _lopend = (_lees("historie.json", {}) or {}).get("lopend")
        for h in eigen_gws:
            g = h.get("event")
            if not g:
                continue
            pk = _pgws.get(str(g))
            # Ook de opstelling opnieuw halen zolang de gameweek nog liep toen we
            # hem bewaarden. Autosubs worden pas definitief als alles gespeeld is.
            _was_voorlopig = g in set(
                (_lees("historie.json", {}) or {}).get("voorlopig") or [])
            if pk is None or g == _lopend or _was_voorlopig:
                pk = try_get("%s/entry/%s/event/%d/picks/" % (BASE, entry_id, g))
                if pk and pk.get("picks"):
                    _pgws[str(g)] = pk
                    _nieuw = True
            if not pk or not pk.get("picks"):
                continue
            rijen = []
            for x in pk["picks"]:
                d0 = dbmap.get(x["element"])
                st = (hist_speler.get(str(x["element"])) or {}).get(str(g)) or {}
                rijen.append({
                    "id": x["element"],
                    "n": d0["n"] if d0 else "?", "t": d0["t"] if d0 else "?",
                    "p": d0["p"] if d0 else "?",
                    "slot": x["position"], "basis": x["position"] <= 11,
                    "mult": x.get("multiplier", 1),
                    "cap": x.get("is_captain", False), "vice": x.get("is_vice_captain", False),
                    "pt": (st.get("pt") or 0) * max(1, x.get("multiplier", 1) or 1),
                    "ruw": st.get("pt") or 0, "min": st.get("min") or 0,
                    "g": st.get("g") or 0, "a": st.get("a") or 0,
                    "cs": st.get("cs") or 0, "dc": st.get("dc") or 0,
                    "bo": st.get("bo") or 0, "opp": st.get("opp"),
                    "thuis": st.get("thuis"), "uit": st.get("uit")})
            terugblik.append({
                "gw": g, "punten": h.get("points"), "straf": h.get("event_transfers_cost") or 0,
                "totaal": h.get("total_points"), "rang": h.get("overall_rank"),
                "gw_rang": h.get("rank"), "bank_punten": h.get("points_on_bench") or 0,
                "transfers": h.get("event_transfers") or 0,
                "waarde": round((h.get("value") or 0) / 10.0, 1),
                "chip": chip_per_gw.get(g),
                "voorlopig": g == _lopend,
                "picks": rijen})
        if _nieuw:
            _pcache["gws"] = _pgws
            try:
                json.dump(_pcache, open(os.path.join(STATE, "eigen_picks.json"), "w",
                                        encoding="utf-8"), ensure_ascii=False)
            except Exception as ex:
                print("  LET OP: opstellingen niet bewaard (%s)" % str(ex)[:60])
        if terugblik:
            print("  Terugblik: GW%s"
                  % ", GW".join(str(t["gw"]) + ("*" if t["voorlopig"] else "")
                                for t in terugblik))

    # ── Welke chips heb je al opgebrand? ─────────────────────────────────
    # Zonder dit bood het dashboard een Bench Boost aan die allang op was.
    # FPL kent twee sets: de eerste moet vóór de GW19-deadline op, de tweede
    # geldt vanaf GW20. Een chip uit set één is dus alleen "weg" voor de
    # eerste seizoenshelft.
    chips_op = []
    if entry_meta:
        for c in ((try_get("%s/entry/%s/history/" % (BASE, entry_id)) or {}).get("chips") or []):
            g = c.get("event") or 0
            chips_op.append({"naam": c.get("name"), "gw": g,
                             "helft": 1 if g <= 19 else 2})

    # ── Zware berekeningen hergebruiken ──────────────────────────────────
    # De verversing bouwt twee keer: eerst het dashboard, dan het nieuws
    # erbij, dan opnieuw. De tweede keer verandert alleen dat nieuws, maar het
    # hele optimalisatiewerk werd toch overgedaan — vier minuten die nergens
    # toe leiden. Dit legt de uitkomst vast onder een vingerafdruk van de
    # invoer; verandert er iets aan de projecties, de selectie of het budget,
    # dan wordt er gewoon opnieuw gerekend.
    import hashlib as _hl
    _vinger = _hl.sha1(json.dumps({
        "gw": gw,
        "squad": sorted(x["id"] for x in squad) if squad else [],
        "bank": (entry_meta or {}).get("last_deadline_bank"),
        "proj": _hl.sha1(json.dumps(
            {str(p["id"]): p["gw"] for p in db}, sort_keys=True).encode()).hexdigest(),
    }, sort_keys=True).encode()).hexdigest()
    _cachepad = os.path.join(STATE, "zwaar_cache.json")
    _cache = _lees("zwaar_cache.json", {}) or {}
    _hergebruik = _cache.get("_vinger") == _vinger
    if _hergebruik:
        print("  Zware berekeningen hergebruikt (invoer ongewijzigd)")

    ideaal = None
    try:
        if _hergebruik and _cache.get("ideaal"):
            ideaal = _cache["ideaal"]
            raise StopIteration           # rest van dit blok overslaan
        import bouwer as BOUWER
        budget_totaal = (sum(x["c"] for x in squad) + ((entry_meta or {}).get("last_deadline_bank") or 0) / 10.0) if squad else 100.0
        # Vier horizonnen: deze gameweek, en dan drie steeds langere blikken.
        # Acht gameweeks is waar Justin naar vroeg — lang genoeg om een
        # fixturezwenking mee te nemen, kort genoeg om nog iets te betekenen.
        import xi_opt as XI2
        import solver as SOLVER2
        for horizon in (1, 3, 5, 8):
            # Breedte 40 kostte 32 seconden per horizon en leverde tegenover
            # breedte 16 precies 0,3 punt op — op één gameweek, in een getal
            # dat als bovengrens dient en niet als advies. Vier horizonnen
            # duurden zo samen drie minuten, en dat is wat de verversing op
            # GitHub over de tijd hielp. Met voorfilteren erbij: exact dezelfde
            # uitkomst, een fractie van de tijd.
            # De bouwer maximaliseert de som van vijftien; xi_opt klimt daarna
            # naar de echte elftalscore. Die klim blijft in een dal hangen: bij
            # een test gaf de vrije zoektocht 467 punten terwijl dezelfde
            # zoektocht mét Haaland verplicht op 486 uitkwam — een oplossing die
            # in de vrije ruimte gewoon bestond maar niet gevonden werd.
            #
            # Daarom meerdere startpunten: vrij, en met elk van de duurste
            # spelers verplicht erin. Die dwingen de zoektocht een ander dal in.
            # De beste uitkomst wint.
            kandidaten = BOUWER.voorfilter(db, gw, horizon)
            venster_i = [g for g in range(gw, min(39, gw + horizon))]
            duur = sorted([p for p in db if BOUWER.bruikbaar(p) and p["c"] >= 9.0],
                          key=lambda p: -BOUWER.opbrengst(p, gw, horizon))[:3]
            pogingen = [()] + [(p["n"],) for p in duur]
            beste_r, beste_ids, beste_score = None, None, -1.0
            for vast in pogingen:
                kand = list(kandidaten)
                for n in vast:
                    q = next((x for x in db if x["n"] == n), None)
                    if q is not None and q not in kand:
                        kand.append(q)
                try:
                    rr = BOUWER.bouw(kand, budget_totaal, gw, horizon, breedte=16, vast=list(vast))
                except SystemExit:
                    continue
                if not rr:
                    continue
                try:
                    kids, _a, _b = XI2.verbeter(db, rr[2], int(round(budget_totaal * 10)), venster_i)
                except Exception:
                    kids = rr[2]
                score = sum(SOLVER2.beste_xi([dbmap[i] for i in kids if i in dbmap], g)[0]
                            for g in venster_i)
                if score > beste_score:
                    beste_r, beste_ids, beste_score = rr, list(kids), score
            if not beste_r:
                continue
            r = beste_r
            xp, clubs, ids, kost = r
            # Ook hier: de bouwer telt vijftien, je scoort er elf. Zonder deze
            # pas staat er een "haalbaar maximum" met miljoenen op de bank.
            ids = beste_ids
            kost = int(round(sum(dbmap[i]["c"] for i in ids if i in dbmap) * 10))
            xi_echt = sum(SOLVER2.beste_xi([dbmap[i] for i in ids if i in dbmap], g)[0]
                          for g in venster_i)
            ideaal = ideaal or {}
            ideaal[str(horizon)] = {
                "xp": round(xi_echt, 1),
                "xp_squad": round(sum(sum(_gwv(dbmap[i], g) for g in venster_i)
                                      for i in ids if i in dbmap), 1),
                "kosten": kost / 10.0,
                "ids": list(ids),
                "spelers": [{"id": i, "n": dbmap[i]["n"], "t": dbmap[i]["t"],
                             "p": dbmap[i]["p"], "c": dbmap[i]["c"]}
                            for i in ids if i in dbmap]}
        if ideaal:
            ideaal["_budget"] = round(budget_totaal, 1)
            ideaal["_methode"] = ("bouwer.py — exacte selectie van 15 binnen 2-5-5-3, "
                                  "max 3 per club en het budget. Alleen spelers met minstens "
                                  "45 verwachte minuten en zonder blessure- of schorsingsvlag.")
    except StopIteration:
        pass
    except StopIteration:
        pass
    except Exception as ex:
        ideaal = {"fout": str(ex)[:200]}

    # uitkomst bewaren zodat de tweede bouw (met het nieuws erbij) hem hergebruikt
    if not _hergebruik:
        try:
            json.dump({"_vinger": _vinger, "ideaal": ideaal, "chipteams": chipteams},
                      open(_cachepad, "w", encoding="utf-8"))
        except Exception:
            pass

    # ---- multi-gameweek solver ----
    solverplan = None
    try:
        import solver as SOLVER
        sq = [next(x for x in db if x["id"] == p_["id"]) for p_ in squad] if squad else []
        if len(sq) == 15:
            gws_s = alle_gws[:6]
            bank = ((entry_meta or {}).get("last_deadline_bank") or 0) / 10.0
            paden = SOLVER.solve(db, sq, bank, gws_s, beam=35, max_hits=1)
            basis = sum(SOLVER.beste_xi(sq, g)[0] for g in gws_s)
            b = paden[0]
            solverplan = {"gws": gws_s, "basis": round(basis, 1),
                          "beste": round(b["punten"], 1),
                          "winst": round(b["punten"] - basis, 1),
                          "hits": b["kosten"],
                          "pad": [{"gw": st["gw"], "acties": st["acties"], "hit": st["hit"]}
                                  for st in b["pad"]],
                          "alt": [{"punten": round(p2["punten"], 1),
                                   "pad": [{"gw": st["gw"], "acties": st["acties"]} for st in p2["pad"]]}
                                  for p2 in paden[1:4]]}
    except Exception as ex:
        solverplan = {"fout": str(ex)[:160]}

    return {
        "gegenereerd": now.isoformat(timespec="seconds"),
        "gw": gw, "gw_naam": ev["name"], "alle_gws": alle_gws,
        "deadline_utc": deadline.isoformat(),
        "deadline_nl": to_nl(deadline).strftime("%a %d %b %Y %H:%M"),
        "spelers_totaal": bs.get("total_players"),
        "db": db, "ticker": ticker, "squad": squad, "picks_gw": picks_gw,
        "entry": ({"naam": entry_meta.get("name"),
                   "punten": entry_meta.get("summary_overall_points"),
                   "rang": entry_meta.get("summary_overall_rank"),
                   "gw_punten": entry_meta.get("summary_event_points"),
                   "gw_rang": entry_meta.get("summary_event_rank"),
                   "waarde": (entry_meta.get("last_deadline_value") or 0) / 10.0,
                   "manager": ("%s %s" % (entry_meta.get("player_first_name", ""),
                                          entry_meta.get("player_last_name", ""))).strip(),
                   "bank": (entry_meta.get("last_deadline_bank") or 0) / 10.0,
                   "vrij": vrij_nu,
                   "vrij_bron": ("afgeleid uit /entry/%s/history/: één per gameweek vanaf GW2, "
                                 "maximaal vijf gespaard, wildcard en free hit tellen niet mee"
                                 % entry_id) if vrij_nu is not None else None}
                  if entry_meta else None),
        "chips_gebruikt": chips_op,
        "league": league,
        "archief": archief,
        "stand": stand,
        "vorm": vormdata,
        "starters": starters,
        "clubkleur": CLUBKLEUR,
        # De volledige clubnamen komen uit de API zelf. Ze stonden hardgecodeerd
        # in de pagina en waren na de promotie/degradatie verouderd: Burnley,
        # West Ham en Wolves stonden er nog in terwijl ze dit seizoen niet in de
        # Premier League spelen. Zo blijft de lijst elk seizoen vanzelf kloppen.
        "clubnamen": {t["short_name"]: t["name"] for t in bs["teams"]},
        "solver": solverplan,
        "regels": _lees("regels.json", {}),
        "template": {
            "_bron": "FPL Focal Template Team, fpl.page",
            "_opgehaald": "2026-08-13",
            "xi": ["Raya", "Diop", "Gabriel", "Shaw", "van Ewijk", "B.Fernandes",
                   "Semenyo", "Szoboszlai", "Calvert-Lewin", "Haaland", "João Pedro"],
            "bank": ["Dubravka", "Hughes", "Thomas", "Yates"]},
        "doel": {
            "vorig_punten": 2352, "vorig_rang": 49961,
            "_bron": "Justins eigen eindstand seizoen 2025/26",
            "league_naam": "FPL met de mannon", "league_deelnemers": 10, "pot_euro": 250},
        "rotatie": _lees("rotatie.json"),
        "nieuws": _lees("nieuws.json"),
        "odds_meta": ((lambda o: {k: v for k, v in o.items() if k != "per_club"} if o else None)
                      (_lees("odds.json"))),
        "chipteams": chipteams,
        "ideaal": ideaal,
        "kalibratie": kal_info,
        # Welk seizoen beschrijven de historische velden in bootstrap-static?
        # Voor de eerste afgeronde gameweek draagt FPL nog de eindstand van 25/26;
        # daarna schrijft FPL ze over met de cijfers van dit seizoen.
        "seizoen": {
            "live_is": "2025/26" if gespeeld == 0 else "2026/27",
            "gespeeld": gespeeld,
            "snapshot_beschikbaar": os.path.exists(os.path.join(STATE, "seizoen_2025_26.json")),
            "uitleg": ("FPL's API draagt maar een seizoen tegelijk. Er is nog geen gameweek gespeeld, "
                       "dus alle historische kolommen gaan over 2025/26."
                       if gespeeld == 0 else
                       "Er %s %d gameweek%s gespeeld in 2026/27; de live kolommen gaan daarover. "
                       "De cijfers van 2025/26 komen uit een momentopname van 14-08-2026."
                       % ("is" if gespeeld == 1 else "zijn", gespeeld, "" if gespeeld == 1 else "s")),
            "vorig": _vorig},
        "blessures_meta": {"bronnen": blessures.get("_bronnen", []),
                           "waarom": blessures.get("_waarom", ""),
                           "officieel": len(blessures.get("officieel_pl", [])),
                           "extra_geflagd": extern_geflagd,
                           "fpl_zelf": sum(1 for d0 in db if d0["status"] != "a" and not d0.get("extern"))},
        "copilot_scheef": bool(cp_scheef),
        # Tot en met deze gameweek komt de projectie van Copilot; daarna van
        # het eigen model. Dat verschil hoort zichtbaar te zijn, want elk
        # advies verderop rust op een andere bron.
        "copilot_grens": cp_laatste,
        "bronkeuring": keuringen,
        "concept": _concept,
        "budget_info": budget_info,
        "awards": _lees("awards_historie.json", {}) or {},
        "terugblik": terugblik,
        "historie": _lees("historie.json", {}) or {},
        "copilot_meta": {"aantal": len(copilot.get("spelers", {})),
                         "bron": copilot.get("_bron", ""),
                         "opgehaald": copilot.get("_opgehaald", ""),
                         "horizon": cp_start + 7,
                         "dekking": copilot.get("_dekking", ""),
                         "methode": copilot.get("_methode", ""),
                         "zonder": copilot.get("_zonder_copilot", [])},
        "helften": {"een": [g for g in alle_gws if g <= 19],
                    "twee": [g for g in alle_gws if g >= 20]},
        "opta_meta": {"spelers": O["aantal_opta_spelers"], "gekoppeld": len(spelers_opta),
                      "gemist": O["gemist"][:20],
                      "promovendi": [k for k, v in teamdata.items() if v["bron"] == "championship"],
                      "teamdata": {k: {"xgf": v["xgf"], "xga": v["xga"], "bron": v["bron"]}
                                   for k, v in teamdata.items()}},
    }


def render(d):
    """Artifactversie: alle data in de pagina gebakken."""
    tpl = open(os.path.join(HERE, "dashboard_template.html"), encoding="utf-8").read()
    return tpl.replace("/*__DATA__*/null",
                       json.dumps(d, ensure_ascii=False, separators=(",", ":")))


def render_web(d):
    """Webversie voor Netlify: de pagina haalt de data bij het openen op.

    Waarom dit anders moet dan de artifactversie: daar mag een pagina geen
    externe verzoeken doen, dus is alles ingebakken. Op een eigen domein mag
    dat wel, en dan is het beter ook — de pagina zakt van ruim een megabyte
    naar honderd kilobyte, en je ziet de data van vandaag zonder dat de site
    opnieuw gebouwd hoeft te worden.

    Het script wordt in een async-wrapper gezet zodat we op het ophalen kunnen
    wachten. Dat kan veilig: de pagina gebruikt geen inline onclick-handlers
    die van de globale scope afhangen.
    """
    tpl = open(os.path.join(HERE, "dashboard_template.html"), encoding="utf-8").read()
    merk = "<script>\nconst D = /*__DATA__*/null;"
    if merk not in tpl:
        raise SystemExit("Kon het scriptbegin niet vinden in het sjabloon")

    # ── Inloggen en synchroniseren ───────────────────────────────────────
    # Alleen in de webversie. In een artifact wordt elk verzoek naar een
    # externe host geblokkeerd, dus daar zou de knop verschijnen en meteen
    # falen; dan is hem verbergen eerlijker.
    #
    # Deze twee waarden horen publiek te zijn. De beveiliging zit niet in de
    # sleutel maar in de regels op de tabel: iedereen kan alleen bij zijn eigen
    # rij. De service_role-sleutel, die dat wél omzeilt, staat hier niet en
    # hoort nergens in een webpagina.
    sync = ('<script>window.FPL_SYNC={url:"https://pzqplgwyhnldevydhhpd.supabase.co",'
            'anon:"sb_publishable_juwI5GF8x7kfaID2gO2Z9w_nw8ThZXq"};</script>\n')

    kop = sync + """<div id="laadscherm" style="position:fixed;inset:0;display:flex;align-items:center;
  justify-content:center;background:#0A0818;color:#B4ABDC;font:15px -apple-system,
  BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;z-index:999;flex-direction:column;gap:14px">
  <div style="width:34px;height:34px;border:3px solid rgba(168,152,255,.2);
    border-top-color:#8B6CFF;border-radius:50%;animation:tol 1s linear infinite"></div>
  <div>Data van vandaag ophalen…</div>
  <style>@keyframes tol{to{transform:rotate(360deg)}}</style>
</div>
<script>
(async function(){
  /* Waar de data vandaan komt, en waarom in deze volgorde.

     Eerst GitHub zelf. De Action schrijft daar elke ronde verse cijfers naartoe,
     en raw.githubusercontent.com stuurt `access-control-allow-origin: *` met een
     cache van vijf minuten -- dus de pagina mag hem gewoon ophalen en ziet nieuwe
     data binnen minuten. Netlify hoeft daar niets voor te doen.

     Dat laatste is precies waar het misging. Netlify bouwde tot nu toe bij ELKE
     push opnieuw, ook als alleen data/ veranderde, en dat kostte 15 credits per
     keer. Bij acht rondes per dag is dat ruim vierduizend credits per maand voor
     een site die niet verandert. Nu haalt de pagina zijn data rechtstreeks bij
     GitHub en bouwt Netlify alleen nog als de PAGINA zelf wijzigt.

     Daarna het lokale bestand als terugval. Dat blijft nodig: staat de repository
     op privé, dan weigert raw.githubusercontent.com zonder token en moet de site
     op zijn eigen kopie terugvallen in plaats van een foutscherm te tonen. */
  const BRONNENLIJST = [
    window.FPL_DATA_URL,
    "https://raw.githubusercontent.com/__REPO__/main/data/dashboard.json",
    "data/dashboard.json",
  ].filter(Boolean);
  let D, gebruikt = null, fouten = [];
  for (const bron of BRONNENLIJST) {
    try {
      const r = await fetch(bron, {cache: "no-store"});
      if (!r.ok) throw new Error("HTTP " + r.status);
      D = await r.json();
      gebruikt = bron;
      break;
    } catch (e) { fouten.push(bron.replace(/^https:\/\/[^/]+/, "") + ": " + e.message); }
  }
  if (!D) {
    document.getElementById("laadscherm").innerHTML =
      '<div style="max-width:460px;text-align:center;line-height:1.6">' +
      '<b style="color:#F0426B;display:block;margin-bottom:8px">De data kon niet geladen worden</b>' +
      'Geprobeerd:<br><code style="font-size:12px">' + fouten.join('<br>') + '</code><br><br>' +
      'Draait de GitHub Action al, en staat <code>data/dashboard.json</code> in je repository?</div>';
    return;
  }
  window.__DATABRON = gebruikt;
  document.getElementById("laadscherm").remove();
"""
    # Het repo-adres staat in ids.json, zodat het niet in de code hoeft te staan
    # en meeverhuist als Justin de repository ooit hernoemt.
    _repo = (_lees("ids.json", {}) or {}).get("github_repo") or "justinspiration/Fpl-cockpit"
    kop = kop.replace("__REPO__", _repo)
    tpl = tpl.replace(merk, kop, 1)
    # het script sluiten met de wrapper erbij
    laatste = tpl.rindex("</script>")
    tpl = tpl[:laatste] + "\n})();\n" + tpl[laatste:]
    return tpl


# Wat de verversing op GitHub nodig heeft om te draaien. Dit stond met de hand
# gekopieerd in de repo en liep achter: xi_opt.py ontbrak, terwijl dashboard.py
# hem importeert — de Action zou dus zijn gestrand op een ImportError. Nu wordt
# de map bij elke bouw gelijkgetrokken, zodat dat niet meer kan gebeuren.
WEB_SCRIPTS = [
    "dashboard.py", "dashboard_template.html", "opta.py", "bouwer.py", "solver.py",
    "xi_opt.py", "fpl.py", "copilot_ophalen.js", "copilot_verwerk.py",
    "odds_ophalen.js", "odds_verwerk.py", "rotatie.py", "nieuws_ophalen.py",
    "stand.py", "archief.py", "vorm.py",
]
# Gegevens die de scripts nodig hebben maar niet zelf ophalen: jouw id's, de
# spelregels, handmatig geverifieerde blessures en rolwijzigingen, en de
# Opta-uitvoer die je met de hand van theanalyst.com haalt.
WEB_STATE = [
    "ids.json", "regels.json", "starters.json", "team.json", "blessures.json",
    "opta_raw.tsv", "opta_teams.tsv", "seizoen_2025_26.json",
    # ── Laatst bekende goede uitkomst van elke ophaalstap ─────────────────
    # Deze bestanden worden bij elke ronde opnieuw opgehaald, dus in theorie
    # hoeven ze niet mee. In de praktijk wel: mislukt een ophaalstap op
    # GitHub, dan stond er niets en viel het dashboard terug op het eigen
    # model. Alle 595 spelers kregen dan andere cijfers dan FPL Copilot —
    # Haaland van 6,7 naar 8,4 in gameweek 1 — zonder dat er iets stukging.
    # Met een gezaaide versie in de repo degradeert een mislukte ronde naar
    # "de cijfers van gisteren" in plaats van naar een heel ander model.
    "xp_copilot.json", "copilot_ruw.json",
    "odds.json", "odds_ruw.json",
    "stand.json", "vorm.json", "rotatie.json",
    # prijsgeschiedenis: zonder deze twee kan een prijswijziging niet gezien
    # worden, want daar is een vorige stand voor nodig
    "snapshot.json", "snapshot_prev.json",
    "nieuws.json",
    # groeit elke gameweek en is nergens anders vandaan te halen
    "league_archief.json",
]
# Documentatie die met de repo mee moet.
WEB_DOCS = ["OPZETTEN.md", "CHAT-OPZETTEN.md"]


def sync_scripts(web):
    """Zet alles wat de verversing nodig heeft in de repo-map.

    Let op de zelfde-bestandcontrole: op GitHub draait dit script vanuit
    scripts/ met `--web ..`, en dan wijzen bron en doel naar hetzelfde bestand.
    Zonder die controle valt de hele bouw om op een SameFileError.
    """
    import shutil
    doel = os.path.join(web, "scripts")
    os.makedirs(os.path.join(doel, "state"), exist_ok=True)
    n_s = n_d = 0

    def kopieer(bron, naar):
        if not os.path.exists(bron):
            return False
        try:
            if os.path.samefile(bron, naar):
                return True          # al op zijn plek; niets te doen
        except OSError:
            pass                     # doel bestaat nog niet
        shutil.copy2(bron, naar)
        return True

    for naam in WEB_SCRIPTS:
        if kopieer(os.path.join(HERE, naam), os.path.join(doel, naam)):
            n_s += 1
        else:
            print("  LET OP: %s ontbreekt en gaat niet mee naar de repo" % naam)
    for naam in WEB_STATE:
        if kopieer(os.path.join(STATE, naam), os.path.join(doel, "state", naam)):
            n_d += 1
    for naam in WEB_DOCS:
        kopieer(os.path.join(HERE, "netlify", naam), os.path.join(web, naam))
    return n_s, n_d


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(STATE, "dashboard.html"))
    ap.add_argument("--web", metavar="MAP",
                    help="schrijf ook een webversie: index.html plus data/dashboard.json")
    a = ap.parse_args()
    data = build()
    html = render(data)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    open(a.out, "w", encoding="utf-8").write(html)
    print("Dashboard: %s (%.0f KB)" % (a.out, len(html) / 1024.0))
    if a.web:
        os.makedirs(os.path.join(a.web, "data"), exist_ok=True)
        web = "<!doctype html>\n" + render_web(data)
        open(os.path.join(a.web, "index.html"), "w", encoding="utf-8").write(web)
        djson = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        open(os.path.join(a.web, "data", "dashboard.json"), "w", encoding="utf-8").write(djson)
        n_s, n_d = sync_scripts(a.web)
        print("Webversie:  %s/index.html (%.0f KB) + data/dashboard.json (%.0f KB)"
              % (a.web, len(web) / 1024.0, len(djson) / 1024.0))
        print("            scripts/ bijgewerkt: %d scripts, %d gegevensbestanden" % (n_s, n_d))
    print("  GW%s-38 | deadline %s | %d spelers | squad %d"
          % (data["gw"], data["deadline_nl"], len(data["db"]), len(data["squad"])))
    print("  Opta: %d spelers gekoppeld | promovendi omgerekend: %s"
          % (data["opta_meta"]["gekoppeld"], ", ".join(data["opta_meta"]["promovendi"])))

