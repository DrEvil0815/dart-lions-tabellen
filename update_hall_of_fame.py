
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
HISTORY = Path("data/historische-ergebnisse.json")

session = requests.Session()
session.headers.update({
    "User-Agent": "DartLions-HallOfFame/1.4",
    "Accept": "application/json"
})


def fetch(url):
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response.json()


def clean(value):
    return " ".join(
        unicodedata.normalize(
            "NFC", str(value or "")
        ).split()
    )


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


# ----------------------------------------
# FESTE SPIELERZUORDNUNG
# ----------------------------------------

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
        k = keyname(alias)

        if k in known_names and known_names[k] != target:
            raise ValueError(
                "Mehrdeutiger Spielername: " + alias
            )

        known_names[k] = target

    for uid in item.get("darthelfer_user_ids", []):
        uid = str(uid).strip()

        if not uid:
            continue

        if uid in known_ids and known_ids[uid] != target:
            raise ValueError(
                "Mehrdeutige User-ID: " + uid
            )

        known_ids[uid] = target


def resolve(entry, stat=None):
    stat = stat or {}

    name = clean(entry.get("name"))
    uid = entry.get("userId") or stat.get("userid")
    uid = str(uid).strip() if uid else ""

    by_name = known_names.get(keyname(name))
    by_id = known_ids.get(uid)

    if (
        by_name
        and by_id
        and by_name[0] != by_id[0]
    ):
        raise ValueError(
            "Widersprüchliche Spielerzuordnung: " + name
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


# ----------------------------------------
# BESTLEISTUNGEN
# ----------------------------------------

def highfinishes(stat):
    scores = []
    unknown = 0

    for item in stat.get("highfinishes") or []:
        score = None
        amount = 1

        if isinstance(item, (int, float)):
            score = int(item)

        elif isinstance(item, dict):
            for field in ("key", "score", "finish"):
                value = item.get(field)

                if isinstance(value, (int, float)):
                    score = int(value)
                    break

            if (
                "key" in item
                and isinstance(
                    item.get("value"), (int, float)
                )
            ):
                amount = max(0, int(item["value"]))

        if score is not None and 100 <= score <= 170:
            scores.extend([score] * amount)
        else:
            unknown += 1

    return scores, unknown


# ----------------------------------------
# AUTOMATISCHE PODIUMSERKENNUNG
# ----------------------------------------

def podium_from(data, main_entries):
    ko = data.get("turnierKO") or {}
    games = ko.get(
        "tournamentErgebnisEntities"
    ) or []
    nodes = data.get("modelKO") or []

    ko_players = ko.get(
        "tournamentPlayerEntities"
    ) or []

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
        n.get("feld")
        for n in nodes
        if n.get("finale") is True
    }

    def outcome(game):
        a = game.get("anzahlSpieler1")
        b = game.get("anzahlSpieler2")

        p1 = by_ko.get(game.get("spieler1Id"))
        p2 = by_ko.get(game.get("spieler2Id"))

        if (
            not p1 or not p2
            or type(a) is not int
            or type(b) is not int
            or a == b
        ):
            return None

        winner, loser = (
            (p1, p2) if a > b else (p2, p1)
        )

        def original(p):
            return (
                by_main.get(
                    p.get("playerIdAusGruppenphase")
                )
                or p
            )

        return (
            resolve(original(winner)),
            resolve(original(loser))
        )

    finals = [
        outcome(g)
        for g in games
        if g.get("feld") in final_fields
    ]
    finals = [x for x in finals if x is not None]

    if len(finals) != 1:
        return None, (
            "Finale nicht eindeutig: " + str(len(finals))
        )

    first, second = finals[0]

    third_games = [
        outcome(g)
        for g in games
        if str(g.get("feld") or "").lower()
        == "spielumplatzdrei"
    ]
    third_games = [
        x for x in third_games if x is not None
    ]

    if len(third_games) > 1:
        return None, "Mehrere Spiele um Platz 3"

    third = (
        third_games[0][0]
        if len(third_games) == 1
        else None
    )

    ids = [
        x[0] for x in (first, second, third)
        if x is not None
    ]

    if len(ids) != len(set(ids)):
        return None, "Spieler mehrfach auf Podium"

    note = (
        None if third is not None
        else "Platz 3 nicht eindeutig"
    )

    return (first, second, third), note


# ----------------------------------------
# COMPETITIONS ABRUFEN
# ----------------------------------------

operator = fetch(
    BASE + "/eventoperator/" + OPERATOR
)

closed = operator.get(
    "allTournamentsClosed"
) or []

competitions = {
    event["id"]: event
    for event in closed
    if event.get("id")
    and regular(event.get("bezeichnung"))
}

profiles = {}
chronicle = []
errors = []
unresolved = []
unknown_highfinishes = 0
name_profiles = defaultdict(set)

print("=== DART LIONS HALL OF FAME ===")
print("Competitions gefunden:", len(competitions))


for index, event in enumerate(
    competitions.values(), start=1
):
    tid = event["id"]
    tournament_name = clean(
        event.get("bezeichnung")
    )

    print(
        f"[{index}/{len(competitions)}] "
        f"{tournament_name}"
    )

    try:
        data = fetch(BASE + "/tournament/" + tid)
        main = data.get("turnierMain") or {}

        if main.get("tournamentstatus") != "abgeschlossen":
            continue

        entries = main.get(
            "tournamentPlayerEntities"
        ) or []

        by_id = {
            p["id"]: p
            for p in entries
            if p.get("id")
        }

        wrapper = data.get("ranglisteWrapper") or {}
        statistics = wrapper.get(
            "playerStatistiks"
        ) or []

        prepared = []
        skipped = 0

        for stat in statistics:
            entry = by_id.get(
                stat.get("playerid")
            )

            if not entry or not clean(entry.get("name")):
                skipped += 1
                continue

            identity, display = resolve(entry, stat)
            prepared.append(
                (identity, display, stat)
            )

        podium, podium_note = podium_from(
            data, entries
        )

        date_string = main.get("beginnString")

        try:
            sort_date = datetime.strptime(
                date_string, "%d.%m.%Y"
            ).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            sort_date = ""

        counted = set()

        for identity, display, stat in prepared:
            player = profiles.setdefault(
                identity, new_profile(display)
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

            leg = integer(stat.get("bestLeg"))

            if leg > 0:
                old = player["bestes_leg"]

                if old is None or leg < old:
                    player["bestes_leg"] = leg

            scores, unknown = highfinishes(stat)
            unknown_highfinishes += unknown

            player["highfinish_anzahl"] += len(
                scores
            )

            if scores:
                best = max(scores)
                old = player["highfinish"]

                if old is None or best > old:
                    player["highfinish"] = best

        podium_names = {
            "sieger": None,
            "zweiter": None,
            "dritter": None
        }

        if podium:
            for label, person, field in [
                ("sieger", podium[0], "siege"),
                ("zweiter", podium[1], "zweite_plaetze"),
                ("dritter", podium[2], "dritte_plaetze")
            ]:
                if person is None:
                    continue

                identity, display = person

                player = profiles.setdefault(
                    identity, new_profile(display)
                )

                name_profiles[
                    keyname(display)
                ].add(identity)

                player[field] += 1
                player["podiumsplaetze"] += 1
                podium_names[label] = player["name"]

        chronicle.append({
            "id": tid,
            "name": tournament_name,
            "datum": date_string,
            "sort_datum": sort_date,
            "teilnehmer_mit_statistik": len(counted),
            "nicht_zugeordnete_statistiken": skipped,
            **podium_names,
            "platzierung_hinweis": podium_note,
            "url": (
                "https://www.darthelfer.de/"
                "public/tournament/"
                + tid + "?app=false"
            )
        })

        if skipped:
            unresolved.append({
                "turnier": tournament_name,
                "anzahl": skipped
            })

        print(
            "  Sieger:",
            podium_names["sieger"] or "OFFEN"
        )

    except Exception as exc:
        errors.append({
            "turnier": tournament_name,
            "fehler": str(exc)[:250]
        })
        print("  FEHLER:", exc)

    time.sleep(0.5)


# ----------------------------------------
# HISTORISCHE ERGEBNISSE ERGÄNZEN
# ----------------------------------------

historical_data = json.loads(
    HISTORY.read_text(encoding="utf-8")
)

historical_added = 0


def historical_identity(display_name):
    """Historische Namen bestehenden Profilen zuordnen."""
    target_name = keyname(display_name)

    # Zuerst feste Spielerzuordnung verwenden.
    mapped = known_names.get(target_name)

    if mapped:
        return mapped

    # Nur eindeutig identische Namen verbinden.
    matches = [
        (identity, player["name"])
        for identity, player in profiles.items()
        if keyname(player["name"]) == target_name
    ]

    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        raise ValueError(
            "Historischer Name mehrdeutig: "
            + display_name
        )

    # Falls ein Spieler noch keine anderen Daten hat.
    return (
        "historical:" + target_name,
        clean(display_name)
    )


for historical in historical_data.get("turniere", []):
    tid = historical.get("id")

    matching = [
        item for item in chronicle
        if item["id"] == tid
    ]

    if len(matching) != 1:
        raise ValueError(
            "Historisches Turnier nicht eindeutig: "
            + str(tid)
        )

    item = matching[0]
    positions = historical.get("platzierungen", {})

    for position, label, field in [
        ("1", "sieger", "siege"),
        ("2", "zweiter", "zweite_plaetze"),
        ("3", "dritter", "dritte_plaetze")
    ]:
        display_name = positions.get(position)

        if not display_name:
            continue

        identity, official_name = historical_identity(
            display_name
        )

        existing = item.get(label)

        if existing:
            if keyname(existing) != keyname(official_name):
                raise ValueError(
                    "Historisches Ergebnis widerspricht "
                    "automatischer Auswertung: "
                    + item["name"] + ", Platz " + position
                )

            # Bereits gezählt: nicht doppelt addieren.
            continue

        player = profiles.setdefault(
            identity,
            new_profile(official_name)
        )

        name_profiles[
            keyname(official_name)
        ].add(identity)

        player[field] += 1
        player["podiumsplaetze"] += 1

        item[label] = player["name"]
        historical_added += 1

        print(
            "HISTORISCH:",
            item["name"],
            "| Platz", position,
            "|", player["name"]
        )

    if all(
        item.get(k)
        for k in ("sieger", "zweiter", "dritter")
    ):
        item["platzierung_hinweis"] = None

    item["historisch_ergaenzt"] = True

    if historical.get("teilnehmer"):
        item["historische_teilnehmerzahl"] = integer(
            historical["teilnehmer"]
        )


# ----------------------------------------
# RANGLISTEN UND AUSGABE
# ----------------------------------------

def ranking(field):
    return sorted(
        profiles.values(),
        key=lambda p: (
            -(p[field] if p[field] is not None else -1),
            p["name"].casefold()
        )
    )


chronicle.sort(
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
    for name, identities in name_profiles.items()
    if len(identities) > 1
]

duplicates.sort(
    key=lambda p: (
        -p["profile"],
        p["name"]
    )
)

missing = [
    {
        "turnier": item["name"],
        "grund": item["platzierung_hinweis"]
    }
    for item in chronicle
    if item.get("platzierung_hinweis")
]

result = {
    "stand": datetime.now(
        timezone.utc
    ).isoformat(timespec="minutes"),

    "quelle": BASE + "/eventoperator/" + OPERATOR,

    "erkannte_competitions": len(competitions),
    "ausgewertete_competitions": len(chronicle),

    "finals_erkannt": sum(
        1 for item in chronicle if item["sieger"]
    ),

    "historisch_ergaenzte_platzierungen": historical_added,

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
        "zweite_plaetze": ranking("zweite_plaetze"),
        "dritte_plaetze": ranking("dritte_plaetze"),
        "podiumsplaetze": ranking("podiumsplaetze"),
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

    "chronik": chronicle,
    "fehler": errors
}

if not chronicle:
    raise RuntimeError(
        "Keine Competitions ausgewertet."
    )

if errors:
    raise RuntimeError(
        "Darthelfer-Abruffehler: "
        + str(errors)
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
print("Competitions:", len(chronicle))
print("Sieger erfasst:", result["finals_erkannt"])
print("Historische Ergänzungen:", historical_added)
print("Spielerprofile:", len(profiles))
print("Offene Platzierungen:", len(missing))
print("Doppelprofile:", len(duplicates))

print("\n=== TOP 10 - TURNIERSIEGE ===")
for player in ranking("siege")[:10]:
    print(
        player["name"],
        player["siege"]
    )

print("\nDatei gespeichert:", OUTPUT)
