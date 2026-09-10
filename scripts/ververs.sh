#!/bin/bash
# Ververst alle data en bouwt het dashboard opnieuw.
#   ./ververs.sh          normaal
#   ./ververs.sh --zichtbaar   met een zichtbaar Chrome-venster, om mee te kijken
set -e
cd "$(dirname "$0")"
echo "1/10  Copilot ophalen…"
node copilot_ophalen.js "$@"
echo "2/10  koppelen aan FPL-id's…"
python3 copilot_verwerk.py
echo "3/10  bookmakersodds ophalen…"
node odds_ophalen.js "$@" || echo "     LET OP: odds mislukt — dashboard valt terug op het eigen model"
echo "4/10  doelpuntverwachtingen afleiden…"
python3 odds_verwerk.py || echo "     LET OP: odds niet verwerkt"
echo "5/10 competitiestand…"
python3 stand.py || echo "     LET OP: stand mislukt"
echo "6/10 vorm per club, inclusief de voorbereiding…"
python3 vorm.py || echo "     LET OP: vorm mislukt"
echo "7/10 league-archief bijwerken…"
python3 archief.py || echo "     LET OP: archief mislukt"
echo "8/10 dashboard bouwen…"
python3 dashboard.py
echo "9/10 Europees schema en rotatiedruk…"
python3 rotatie.py || echo "     LET OP: rotatie mislukt"
echo "10/10 nieuws en geruchten ophalen…"
python3 nieuws_ophalen.py || echo "     LET OP: nieuws mislukt"
python3 dashboard.py
