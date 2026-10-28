
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
MAPPING = Path("data/spieler-zuordnung.json")

session = requests.Session()
session.headers.update({
    "User-Agent": "DartLions-HallOfFame/1.2",
    "Accept": "application/json"
})


def fetch(url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response.json()


def clean_name(value):
    value = unicodedata.normalize(
        "NFC", str(value or "")
    )
    return " ".join(value.split()).strip()


def name_key(value):
    return clean_name(value).casefold()


def safe_int(value):
    try:
        return int(value or 0)
    except (ValueError, TypeError):
        return 0


def is_competition(value):
    name = clean_name(value)

    return any(
        re.fullmatch(
            pattern, name, re.IGNORECASE
        )
        for pattern in [
            r"\d+\.\s*Competition\s*26",
            r"\d+\.\s*Steeldart Competition(?:\(1\))?"
        ]
    )


def new_profile(name):
    return {
        "name": name,
        "teilnahmen": 0,
        "maximums_180": 0,
        "maximums_171": 0,
        "bestes_leg": None,
        "highfinish": None,
        "highfinish_anzahl": 0
    }


# Eigene Spielerzuordnung laden.
mapping_data = json.loads(
    MAPPING.read_text(encoding="utf-8")
)

known_ids = {}
known_names = {}

for player in mapping_data.get("spieler", []):
    internal_id = player["id"]
    official_name = clean_name(player["name"])

    for uid in player.get("darthelfer_user_ids", []):
        uid = str(uid).strip()

        if uid in known_ids and known_ids[uid][0] != internal_id:
            raise ValueError(
                f"Darthelfer-ID mehrfach zugeordnet: {uid}"
            )

        known_ids[uid] = (
            internal_id, official_name
        )

    for alias in player.get("bekannte_namen", []):
        key = name_key(alias)

        if key in known_names and known_names[key][0] != internal_id:
            raise ValueError(
                f"Spielername mehrfach zugeordnet: {alias}"
            )

        known_names[key] = (
            internal_id, official_name
        )


def resolve_player(entry, stat):
    display_name = clean_name(entry.get("name"))
    user_id = (
        entry.get("userId")
        or stat.get("userid")
    )

    user_id = str(user_id).strip() if user_id else None

    by_id = known_ids.get(user_id)
    by_name = known_names.get(name_key(display_name))

    if by_id and by_name and by_id[0] != by_name[0]:
        raise ValueError(
            "Widersprüchliche Zuordnung für "
            + display_name
        )

    # Explizit hinterlegte Identität zuerst.
    if by_id or by_name:
        internal_id, official_name = by_id or by_name

        return (
            "mapped:" + internal_id,
            official_name
        )

    # Ansonsten die Darthelfer-Nutzerkennung.
    if user_id:
        return (
            "user:" + user_id,
            display_name
        )

    # Ohne stabile Kennung nicht automatisch
    # über mehrere Turniere zusammenführen.
    tournament_id = entry.get("tournamentId") or ""
    player_id = entry.get("id") or ""

    return (
        "unresolved:" + tournament_id + ":" + player_id,
        display_name
    )


def highfinish_scores(stat):
    scores = []
    unknown = 0

    for item in stat.get("highfinishes") or []:
        score = None
        quantity = 1

        if isinstance(item, (int, float)):
            score = int(item)

        elif isinstance(item, dict):
            for field in ("key", "score", "finish"):
                candidate = item.get(field)

                if isinstance(candidate, (int, float)):
                    score = int(candidate)
                    break

            if (
                "key" in item
                and isinstance(item.get("value"), (int, float))
            ):
                quantity = max(0, int(item["value"]))

        if score is None:
            unknown += 1
        elif 100 <= score <= 170:
            scores.extend([score] * quantity)
        else:
            unknown += 1

    return scores, unknown


operator = fetch(
    BASE + "/eventoperator/" + OPERATOR
)

closed_events = (
    operator.get("allTournamentsClosed") or []
)

competitions = {
    event["id"]: event
    for event in closed_events
    if event.get("id")
    and is_competition(event.get("bezeichnung"))
}

print("=== DART LIONS HALL OF FAME ===")
print("Gefundene Competitions:", len(competitions))

profiles = {}
history = []
errors = []
unmatched_records = []
unknown_highfinishes = 0

# Hilft uns, weitere mögliche Doppelprofile
# für eine spätere manuelle Prüfung zu entdecken.
name_to_profiles = defaultdict(set)

for index, event in enumerate(
    competitions.values(), start=1
):
    tournament_id = event["id"]
    tournament_name = clean_name(
        event.get("bezeichnung")
    )

    print(
        f"[{index}/{len(competitions)}] "
        f"{tournament_name}"
    )

    try:
        data = fetch(
            BASE + "/tournament/" + tournament_id
        )

        main = data.get("turnierMain") or {}
        wrapper = data.get("ranglisteWrapper") or {}

        if main.get("tournamentstatus") != "abgeschlossen":
            print("  Nicht abgeschlossen - übersprungen")
            continue

        entries = main.get(
            "tournamentPlayerEntities"
        ) or []

        statistics = wrapper.get(
            "playerStatistiks"
        ) or []

        players_by_id = {
            entry["id"]: entry
            for entry in entries
            if entry.get("id")
        }

        counted = set()
        unmatched = 0

        # Erst sammeln und zuordnen, dann werten:
        # Fehlerhafte Turniere liefern keine
        # unbemerkten Teilergebnisse.
        prepared = []

        for stat in statistics:
            entry = players_by_id.get(
                stat.get("playerid")
            )

            if not entry or not clean_name(entry.get("name")):
                unmatched += 1
                continue

            identity, official_name = resolve_player(
                entry, stat
            )

            prepared.append(
                (identity, official_name, stat)
            )

        date_string = main.get("beginnString")

        try:
            sort_date = datetime.strptime(
                date_string, "%d.%m.%Y"
            ).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            sort_date = ""

        for identity, official_name, stat in prepared:
            if identity not in profiles:
                profiles[identity] = new_profile(
                    official_name
                )

            player = profiles[identity]

            name_to_profiles[
                name_key(official_name)
            ].add(identity)

            if identity not in counted:
                player["teilnahmen"] += 1
                counted.add(identity)

            player["maximums_180"] += safe_int(
                stat.get("_180er")
            )

            player["maximums_171"] += safe_int(
                stat.get("_171er")
            )

            best_leg = safe_int(
                stat.get("bestLeg")
            )

            if best_leg > 0:
                current_best = player["bestes_leg"]

                if (
                    current_best is None
                    or best_leg < current_best
                ):
                    player["bestes_leg"] = best_leg

            finishes, unknown = highfinish_scores(stat)

            unknown_highfinishes += unknown

            player["highfinish_anzahl"] += len(
                finishes
            )

            if finishes:
                highest = max(finishes)
                current = player["highfinish"]

                if current is None or highest > current:
                    player["highfinish"] = highest

        history.append({
            "id": tournament_id,
            "name": tournament_name,
            "datum": date_string,
            "sort_datum": sort_date,
            "teilnehmer_mit_statistik": len(counted),
            "nicht_zugeordnete_statistiken": unmatched,
            "url": (
                "https://www.darthelfer.de/public/tournament/"
                + tournament_id
                + "?app=false"
            )
        })

        if unmatched:
            unmatched_records.append({
                "turnier": tournament_name,
                "anzahl": unmatched
            })

        print(
            "  Spieler:", len(counted),
            "| Ohne Zuordnung:", unmatched
        )

    except Exception as exc:
        errors.append({
            "turnier": tournament_name,
            "fehler": str(exc)[:250]
        })

        print("  FEHLER:", exc)

    time.sleep(0.5)


def ranking(field):
    return sorted(
        profiles.values(),
        key=lambda player: (
            -(
                player[field]
                if player[field] is not None
                else -1
            ),
            player["name"].casefold()
        )
    )


possible_duplicates = []

for name, identities in name_to_profiles.items():
    if len(identities) > 1:
        possible_duplicates.append({
            "name": name,
            "profile": len(identities)
        })

possible_duplicates.sort(
    key=lambda item: (
        -item["profile"],
        item["name"]
    )
)

history.sort(
    key=lambda item: (
        item["sort_datum"],
        item["name"]
    ),
    reverse=True
)

result = {
    "stand": datetime.now(
        timezone.utc
    ).isoformat(timespec="minutes"),
    "quelle": (
        BASE + "/eventoperator/" + OPERATOR
    ),
    "erkannte_competitions": len(competitions),
    "ausgewertete_competitions": len(history),
    "spielerprofile": len(profiles),
    "fehleranzahl": len(errors),
    "unbekannte_highfinish_formate": (
        unknown_highfinishes
    ),
    "moegliche_doppelprofile": possible_duplicates,
    "nicht_zugeordnete_statistiken": unmatched_records,
    "ranglisten": {
        "maximums_180": ranking("maximums_180"),
        "maximums_171": ranking("maximums_171"),
        "teilnahmen": ranking("teilnahmen"),
        "highfinishes": ranking("highfinish"),
        "low_darts": sorted(
            [
                p for p in profiles.values()
                if p["bestes_leg"] is not None
            ],
            key=lambda p: (
                p["bestes_leg"],
                p["name"].casefold()
            )
        )
    },
    "chronik": history,
    "fehler": errors
}

if not history:
    raise RuntimeError(
        "Keine abgeschlossenen Competitions ausgewertet."
    )

OUTPUT.parent.mkdir(
    parents=True,
    exist_ok=True
)

OUTPUT.write_text(
    json.dumps(
        result,
        ensure_ascii=False,
        indent=2
    ) + "\n",
    encoding="utf-8"
)

print("\n=== ERGEBNIS ===")
print("Competitions:", len(history))
print("Spielerprofile:", len(profiles))
print("Abruffehler:", len(errors))
print(
    "Unbekannte Highfinish-Werte:",
    unknown_highfinishes
)

print("\n=== TOP 10 - 180ER ===")
for player in ranking("maximums_180")[:10]:
    print(
        player["name"],
        player["maximums_180"]
    )

print("\n=== MÖGLICHE DOPPELPROFILE ===")
for item in possible_duplicates[:30]:
    print(
        item["name"],
        "| Profile:", item["profile"]
    )

print("\n=== NEUESTE COMPETITION ===")
if history:
    print(
        history[0]["datum"],
        history[0]["name"]
    )

print("\nDatei erstellt:", OUTPUT)
