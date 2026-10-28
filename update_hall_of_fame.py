
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
    "User-Agent": "DartLions-HallOfFame/1.3",
    "Accept": "application/json"
})


def fetch(url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response.json()


def clean(value):
    value = unicodedata.normalize(
        "NFC", str(value or "")
    )
    return " ".join(value.split())


def keyname(value):
    return clean(value).casefold()


def integer(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def regular(name):
    patterns = [
        r"\d+\.\s*Competition\s*26",
        r"\d+\.\s*Steeldart Competition(?:\(1\))?"
    ]

    return any(
        re.fullmatch(pattern, clean(name), re.I)
        for pattern in patterns
    )


# Spielerzuordnung laden
config = json.loads(
    MAPPING.read_text(encoding="utf-8")
)

known_names = {}
known_ids = {}

for item in config.get("spieler", []):
    target = (
        "mapped:" + item["id"],
        clean(item["name"])
    )

    for alias in item.get("bekannte_namen", []):
        key = keyname(alias)

        if (
            key in known_names
            and known_names[key] != target
        ):
            raise ValueError(
                "Mehrdeutiger Spielername: " + alias
            )

        known_names[key] = target

    for uid in item.get("darthelfer_user_ids", []):
        uid = str(uid).strip()

        if uid:
            if (
                uid in known_ids
                and known_ids[uid] != target
            ):
                raise ValueError(
                    "Mehrdeutige User-ID: " + uid
                )

            known_ids[uid] = target


def resolve(entry, stat=None):
    stat = stat or {}

    name = clean(entry.get("name"))
    uid = (
        entry.get("userId")
        or stat.get("userid")
    )

    uid = str(uid).strip() if uid else ""

    by_name = known_names.get(keyname(name))
    by_id = known_ids.get(uid)

    if (
        by_name
        and by_id
        and by_name[0] != by_id[0]
    ):
        raise ValueError(
            "Widersprüchliche Spielerzuordnung: "
            + name
        )

    if by_name or by_id:
        return by_name or by_id

    if uid:
        return "user:" + uid, name

    return (
        "unresolved:"
        + str(entry.get("tournamentId") or "")
        + ":"
        + str(entry.get("id") or ""),
        name
    )


def new_profile(name):
    return {
        "name": name,
        "teilnahmen": 0,
        "maximums_180": 0,
        "maximums_171": 0,
        "bestes_leg": None,
        "highfinish": None,
        "highfinish_anzahl": 0,
        "siege": 0,
        "zweite_plaetze": 0,
        "dritte_plaetze": 0,
        "podiumsplaetze": 0
    }


def highfinishes(stat):
    scores = []
    unknown = 0

    for item in stat.get("highfinishes") or []:
        score = None
        amount = 1

        if isinstance(item, (int, float)):
            score = int(item)

        elif isinstance(item, dict):
            for field in (
                "key", "score", "finish"
            ):
                value = item.get(field)

                if isinstance(value, (int, float)):
                    score = int(value)
                    break

            if (
                "key" in item
                and isinstance(
                    item.get("value"),
                    (int, float)
                )
            ):
                amount = max(
                    0, int(item["value"])
                )

        if (
            score is not None
            and 100 <= score <= 170
        ):
            scores.extend([score] * amount)
        else:
            unknown += 1

    return scores, unknown


# Finale und Platz 3 auswerten
def podium_from(data, main_entries):
    ko = data.get("turnierKO") or {}

    games = (
        ko.get("tournamentErgebnisEntities")
        or []
    )

    nodes = data.get("modelKO") or []

    ko_players = (
        ko.get("tournamentPlayerEntities")
        or []
    )

    by_ko = {
        p["id"]: p
        for p in ko_players
        if p.get("id")
    }

    by_main = {
        p["id"]: p
        for p in main_entries
        if p.get("id")
    }

    final_fields = {
        node.get("feld")
        for node in nodes
        if node.get("finale") is True
    }

    def outcome(game):
        a = game.get("anzahlSpieler1")
        b = game.get("anzahlSpieler2")

        player1 = by_ko.get(
            game.get("spieler1Id")
        )

        player2 = by_ko.get(
            game.get("spieler2Id")
        )

        if (
            not player1
            or not player2
            or type(a) is not int
            or type(b) is not int
            or a == b
        ):
            return None

        if a > b:
            winner, loser = player1, player2
        else:
            winner, loser = player2, player1

        def original(ko_player):
            group_id = ko_player.get(
                "playerIdAusGruppenphase"
            )

            return (
                by_main.get(group_id)
                or ko_player
            )

        return (
            resolve(original(winner)),
            resolve(original(loser))
        )

    finals = [
        outcome(game)
        for game in games
        if game.get("feld") in final_fields
    ]

    finals = [
        game for game in finals
        if game is not None
    ]

    if len(finals) != 1:
        return (
            None,
            "Finale nicht eindeutig: "
            + str(len(finals))
        )

    first, second = finals[0]

    third_games = [
        outcome(game)
        for game in games
        if str(
            game.get("feld") or ""
        ).lower() == "spielumplatzdrei"
    ]

    third_games = [
        game for game in third_games
        if game is not None
    ]

    if len(third_games) > 1:
        return (
            None,
            "Mehrere Spiele um Platz 3"
        )

    third = (
        third_games[0][0]
        if len(third_games) == 1
        else None
    )

    identities = [
        participant[0]
        for participant in (
            first, second, third
        )
        if participant is not None
    ]

    if len(identities) != len(set(identities)):
        return (
            None,
            "Spieler mehrfach auf Podium"
        )

    note = (
        None
        if third is not None
        else "Platz 3 nicht eindeutig"
    )

    return (first, second, third), note


# Alle abgeschlossenen Competitions finden
operator = fetch(
    BASE + "/eventoperator/" + OPERATOR
)

closed_events = (
    operator.get("allTournamentsClosed")
    or []
)

competitions = {
    event["id"]: event
    for event in closed_events
    if event.get("id")
    and regular(event.get("bezeichnung"))
}

print(
    "Abgeschlossene Competitions:",
    len(competitions)
)

profiles = {}
history = []
errors = []
missing = []
unresolved = []
unknown_highfinishes = 0

name_profiles = defaultdict(set)


# Alle Turniere auswerten
for index, event in enumerate(
    competitions.values(), 1
):
    tournament_id = event["id"]
    tournament_name = clean(
        event.get("bezeichnung")
    )

    print(
        f"[{index}/{len(competitions)}] "
        f"{tournament_name}"
    )

    try:
        data = fetch(
            BASE + "/tournament/"
            + tournament_id
        )

        main = data.get("turnierMain") or {}

        if (
            main.get("tournamentstatus")
            != "abgeschlossen"
        ):
            continue

        entries = (
            main.get("tournamentPlayerEntities")
            or []
        )

        by_id = {
            p["id"]: p
            for p in entries
            if p.get("id")
        }

        wrapper = (
            data.get("ranglisteWrapper")
            or {}
        )

        statistics = (
            wrapper.get("playerStatistiks")
            or []
        )

        prepared = []
        skipped = 0

        for stat in statistics:
            entry = by_id.get(
                stat.get("playerid")
            )

            if (
                not entry
                or not clean(entry.get("name"))
            ):
                skipped += 1
                continue

            identity, display = resolve(
                entry, stat
            )

            prepared.append(
                (identity, display, stat)
            )

        podium, podium_note = podium_from(
            data, entries
        )

        date_string = main.get(
            "beginnString"
        )

        try:
            sort_date = datetime.strptime(
                date_string,
                "%d.%m.%Y"
            ).strftime("%Y-%m-%d")

        except (ValueError, TypeError):
            sort_date = ""

        counted = set()

        # Spielerstatistiken zusammenzählen
        for identity, display, stat in prepared:
            player = profiles.setdefault(
                identity,
                new_profile(display)
            )

            name_profiles[
                keyname(display)
            ].add(identity)

            if identity not in counted:
                player["teilnahmen"] += 1
                counted.add(identity)

            player["maximums_180"] += integer(
                stat.get("_180er")
            )

            player["maximums_171"] += integer(
                stat.get("_171er")
            )

            leg = integer(
                stat.get("bestLeg")
            )

            if leg > 0:
                current = player["bestes_leg"]

                if (
                    current is None
                    or leg < current
                ):
                    player["bestes_leg"] = leg

            scores, unknown = highfinishes(
                stat
            )

            unknown_highfinishes += unknown

            player["highfinish_anzahl"] += len(
                scores
            )

            if scores:
                highest = max(scores)
                current = player["highfinish"]

                if (
                    current is None
                    or highest > current
                ):
                    player["highfinish"] = highest

        # Offizielle Platzierungen auswerten
        podium_names = {
            "sieger": None,
            "zweiter": None,
            "dritter": None
        }

        if podium:
            positions = [
                (
                    "sieger",
                    podium[0],
                    "siege"
                ),
                (
                    "zweiter",
                    podium[1],
                    "zweite_plaetze"
                ),
                (
                    "dritter",
                    podium[2],
                    "dritte_plaetze"
                )
            ]

            for position, participant, counter in positions:
                if participant is None:
                    continue

                identity, display = participant

                player = profiles.setdefault(
                    identity,
                    new_profile(display)
                )

                name_profiles[
                    keyname(display)
                ].add(identity)

                player[counter] += 1
                player["podiumsplaetze"] += 1

                podium_names[position] = (
                    player["name"]
                )

        if podium_note:
            missing.append({
                "turnier": tournament_name,
                "grund": podium_note
            })

        history.append({
            "id": tournament_id,
            "name": tournament_name,
            "datum": date_string,
            "sort_datum": sort_date,
            "teilnehmer_mit_statistik": (
                len(counted)
            ),
            "nicht_zugeordnete_statistiken": (
                skipped
            ),
            **podium_names,
            "platzierung_hinweis": (
                podium_note
            ),
            "url": (
                "https://www.darthelfer.de/"
                "public/tournament/"
                + tournament_id
                + "?app=false"
            )
        })

        if skipped:
            unresolved.append({
                "turnier": tournament_name,
                "anzahl": skipped
            })

        print(
            "  Spieler:", len(counted),
            "| Sieger:",
            podium_names["sieger"] or "OFFEN"
        )

    except Exception as exc:
        errors.append({
            "turnier": tournament_name,
            "fehler": str(exc)[:250]
        })

        print("  FEHLER:", exc)

    time.sleep(0.5)


# Bestenlisten erstellen
def ranking(field):
    return sorted(
        profiles.values(),
        key=lambda p: (
            -(
                p[field]
                if p[field] is not None
                else -1
            ),
            p["name"].casefold()
        )
    )


history.sort(
    key=lambda item: (
        item["sort_datum"],
        item["name"]
    ),
    reverse=True
)

duplicates = [
    {
        "name": name,
        "profile": len(identities)
    }
    for name, identities
    in name_profiles.items()
    if len(identities) > 1
]

duplicates.sort(
    key=lambda p: (
        -p["profile"],
        p["name"]
    )
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

    "finals_erkannt": sum(
        1
        for item in history
        if item["sieger"]
    ),

    "spielerprofile": len(profiles),
    "fehleranzahl": len(errors),

    "unbekannte_highfinish_formate": (
        unknown_highfinishes
    ),

    "moegliche_doppelprofile": duplicates,
    "nicht_zugeordnete_statistiken": unresolved,
    "offene_platzierungen": missing,

    "ranglisten": {
        "siege": ranking("siege"),

        "zweite_plaetze": ranking(
            "zweite_plaetze"
        ),

        "dritte_plaetze": ranking(
            "dritte_plaetze"
        ),

        "podiumsplaetze": ranking(
            "podiumsplaetze"
        ),

        "maximums_180": ranking(
            "maximums_180"
        ),

        "maximums_171": ranking(
            "maximums_171"
        ),

        "teilnahmen": ranking(
            "teilnahmen"
        ),

        "highfinishes": ranking(
            "highfinish"
        ),

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
        "Keine Competitions ausgewertet."
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


# Zusammenfassung
print("\n=== HALL OF FAME ===")

print(
    "Competitions:",
    len(history)
)

print(
    "Finals erkannt:",
    result["finals_erkannt"]
)

print(
    "Offene Platzierungen:",
    len(missing)
)

for item in missing:
    print(
        "PRUEFEN:",
        item["turnier"],
        "|",
        item["grund"]
    )

print(
    "Abruffehler:",
    len(errors)
)

print(
    "Mögliche Doppelprofile:",
    len(duplicates)
)

print("\n=== TOP 10 - SIEGE ===")

for player in ranking("siege")[:10]:
    print(
        player["name"],
        player["siege"]
    )

print(
    "\nDatei erstellt:",
    OUTPUT
)
