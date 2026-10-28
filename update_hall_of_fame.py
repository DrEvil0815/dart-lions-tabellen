
import json
import re
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "https://www.darthelfer.de/api/public"
OPERATOR = "7a088d28-63ee-49ac-a1fc-fe726056ddbe"
OUTPUT = Path("data/hall-of-fame.json")

session = requests.Session()
session.headers.update({
    "User-Agent": "DartLions-HallOfFame/1.0",
    "Accept": "application/json"
})


def fetch(url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response.json()


def normalized_name(value):
    value = unicodedata.normalize("NFC", str(value or ""))
    return " ".join(value.split()).strip()


def is_competition(name):
    name = normalized_name(name)

    return bool(
        re.fullmatch(
            r"\d+\.\s*Competition\s*26",
            name,
            flags=re.IGNORECASE
        )
        or re.fullmatch(
            r"\d+\.\s*Steeldart Competition(?:\(1\))?",
            name,
            flags=re.IGNORECASE
        )
    )


def safe_int(value):
    try:
        return int(value or 0)
    except (ValueError, TypeError):
        return 0


operator = fetch(BASE + "/eventoperator/" + OPERATOR)

# Nur abgeschlossene, reguläre Competitions.
events = operator.get("allTournamentsClosed") or []

competitions = {
    event["id"]: event
    for event in events
    if event.get("id")
    and is_competition(event.get("bezeichnung", ""))
}

print("Abgeschlossene Competitions:", len(competitions))

players = {}
history = []
errors = []

for index, event in enumerate(competitions.values(), 1):
    tournament_id = event["id"]
    name = normalized_name(event.get("bezeichnung"))

    print(f"[{index}/{len(competitions)}] {name}")

    try:
        data = fetch(
            BASE + "/tournament/" + tournament_id
        )

        main = data.get("turnierMain") or {}
        wrapper = data.get("ranglisteWrapper") or {}

        if main.get("tournamentstatus") != "abgeschlossen":
            print("  Noch nicht abgeschlossen - übersprungen")
            continue

        entries = main.get("tournamentPlayerEntities") or []
        statistics = wrapper.get("playerStatistiks") or []

        # Spieler-ID aus dem Turnier mit Namen verbinden.
        by_player_id = {}

        for entry in entries:
            player_id = entry.get("id")
            display_name = normalized_name(entry.get("name"))

            if player_id and display_name:
                by_player_id[player_id] = entry

        counted = set()
        processed = 0

        for stat in statistics:
            entry = by_player_id.get(stat.get("playerid"))

            if not entry:
                continue

            display_name = normalized_name(entry.get("name"))

            if not display_name:
                continue

            # Übergreifende Nutzerkennung, soweit vorhanden.
            user_id = entry.get("userId") or stat.get("userid")

            # Ohne Nutzerkennung keine ungesicherte
            # Zusammenführung gleichnamiger Spieler.
            identity = (
                "user:" + str(user_id)
                if user_id
                else "name:" + display_name.casefold()
            )

            if identity not in players:
                players[identity] = {
                    "name": display_name,
                    "teilnahmen": 0,
                    "maximums_180": 0,
                    "maximums_171": 0,
                    "bestes_leg": None,
                    "highfinish": None,
                    "highfinish_anzahl": 0
                }

            player = players[identity]

            if identity not in counted:
                player["teilnahmen"] += 1
                counted.add(identity)

            player["maximums_180"] += safe_int(
                stat.get("_180er")
            )

            player["maximums_171"] += safe_int(
                stat.get("_171er")
            )

            best_leg = safe_int(stat.get("bestLeg"))

            if best_leg > 0:
                if (
                    player["bestes_leg"] is None
                    or best_leg < player["bestes_leg"]
                ):
                    player["bestes_leg"] = best_leg

            # Highfinishes aus den Spielerstatistiken.
            for finish in stat.get("highfinishes") or []:
                score = None

                if isinstance(finish, (int, float)):
                    score = int(finish)

                elif isinstance(finish, dict):
                    for field in ("key", "value", "score"):
                        candidate = finish.get(field)

                        if isinstance(candidate, (int, float)):
                            score = int(candidate)
                            break

                if score is not None and 100 <= score <= 170:
                    player["highfinish_anzahl"] += 1

                    if (
                        player["highfinish"] is None
                        or score > player["highfinish"]
                    ):
                        player["highfinish"] = score

            processed += 1

        history.append({
            "id": tournament_id,
            "name": name,
            "datum": main.get("beginnString"),
            "teilnehmer_mit_statistik": len(counted),
            "url": (
                "https://www.darthelfer.de/public/tournament/"
                + tournament_id
                + "?app=false"
            )
        })

        print("  Ausgewertete Spieler:", processed)

    except Exception as exc:
        errors.append({
            "turnier": name,
            "fehler": str(exc)[:250]
        })
        print("  Fehler:", str(exc)[:200])

    # Die öffentliche Schnittstelle schonend abrufen.
    time.sleep(0.5)


def leaderboard(field, reverse=True):
    result = list(players.values())

    result.sort(
        key=lambda p: (
            p[field] is not None,
            p[field] if p[field] is not None else -1
        ),
        reverse=reverse
    )

    return result


history.sort(
    key=lambda item: item.get("datum") or "",
    reverse=True
)

result = {
    "stand": datetime.now(timezone.utc).isoformat(
        timespec="minutes"
    ),
    "quelle": BASE + "/eventoperator/" + OPERATOR,
    "ausgewertete_competitions": len(history),
    "erkannte_competitions": len(competitions),
    "fehleranzahl": len(errors),
    "ranglisten": {
        "maximums_180": leaderboard("maximums_180"),
        "maximums_171": leaderboard("maximums_171"),
        "teilnahmen": leaderboard("teilnahmen"),
        "highfinishes": leaderboard("highfinish"),
        "low_darts": sorted(
            [
                p for p in players.values()
                if p["bestes_leg"] is not None
            ],
            key=lambda p: p["bestes_leg"]
        )
    },
    "chronik": history,
    "fehler": errors
}

if not history:
    raise RuntimeError(
        "Keine abgeschlossenen Competitions ausgewertet."
    )

OUTPUT.parent.mkdir(parents=True, exist_ok=True)

OUTPUT.write_text(
    json.dumps(
        result,
        ensure_ascii=False,
        indent=2
    ) + "\n",
    encoding="utf-8"
)

print("\n=== HALL OF FAME ===")
print("Ausgewertete Competitions:", len(history))
print("Spielerprofile:", len(players))
print("Fehler:", len(errors))
print("Datei:", OUTPUT)

print("\nTOP 10 - 180er:")
for player in leaderboard("maximums_180")[:10]:
    print(
        player["name"],
        player["maximums_180"]
    )
