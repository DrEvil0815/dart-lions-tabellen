
import json
import os
import sys
from datetime import date
from pathlib import Path

import requests

TEAMS = {
    "i": "Dart Lions Gräfenberg",
    "ii": "Dart Lions Gräfenberg II",
    "iii": "Dart Lions Gräfenberg III",
}

matches = []

for team_id, team_name in TEAMS.items():
    file_path = Path(f"data/ergebnisse-{team_id}.json")

    with file_path.open(encoding="utf-8") as file:
        data = json.load(file)

    game = data.get("naechstes_spiel")

    if not game:
        continue

    match_date = game.get("datum_iso")
    home = game.get("heim", "").strip()
    opponent = game.get("gast", "").strip()

    # Nur Heimspiele unserer Mannschaften
    if home != team_name:
        continue

    if not match_date or not opponent:
        continue

    # Vergangene Termine nicht erneut übertragen
    if date.fromisoformat(match_date) < date.today():
        continue

    matches.append({
        "match_date": match_date,
        "team_name": team_name,
        "opponent": opponent,
    })

url = os.environ["SUPABASE_FANWALL_URL"].rstrip("/")
token = os.environ["FANWALL_SYNC_TOKEN"]

response = requests.post(
    f"{url}/functions/v1/fanwall-sync",
    headers={
        "Content-Type": "application/json",
        "x-fanwall-sync-token": token,
    },
    json={"matches": matches},
    timeout=30,
)

if not response.ok:
    print(f"Fan-Wall-Synchronisierung fehlgeschlagen: HTTP {response.status_code}")
    sys.exit(1)

print(f"Fan-Wall: {len(matches)} Heimspieltermine übertragen.")
