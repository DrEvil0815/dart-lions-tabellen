
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
import json
import re
import requests
from bs4 import BeautifulSoup

URL = (
    "https://bdv-dart.liga.nu/cgi-bin/WebObjects/"
    "nuLigaDARTDE.woa/wa/teamPortrait?"
    "teamtable=1528157&championship=OFr+2026%2F27&group=211415"
)

TEAM = "Dart Lions Gräfenberg III"
OUTPUT = Path("data/ergebnisse-iii.json")

response = requests.get(
    URL,
    timeout=40,
    headers={"User-Agent": "Mozilla/5.0"}
)
response.raise_for_status()
response.encoding = response.apparent_encoding or "utf-8"

soup = BeautifulSoup(response.text, "html.parser")
matches = []

for table in soup.select("table.result-set"):
    header = table.find("tr")
    if not header:
        continue

    headings = header.get_text(" ", strip=True)
    if not all(x in headings for x in (
        "Heimmannschaft", "Gastmannschaft", "Spiele"
    )):
        continue

    for tr in table.find_all("tr")[1:]:
        cells = tr.find_all("td", recursive=False)
        if len(cells) < 8:
            continue

        date_text = cells[1].get_text(" ", strip=True)
        home = cells[5].get_text(" ", strip=True)
        away = cells[6].get_text(" ", strip=True)
        score = cells[7].get_text(
            " ", strip=True
        ).replace(" ", "")

        if TEAM not in (home, away):
            continue
        if not re.fullmatch(r"\d+:\d+", score):
            continue

        try:
            match_date = datetime.strptime(
                date_text, "%d.%m.%Y"
            )
        except ValueError:
            continue

        link = cells[7].find("a", href=True)

        matches.append({
            "datum": date_text,
            "datum_iso": match_date.date().isoformat(),
            "heim": home,
            "gast": away,
            "ergebnis": score,
            "spielbericht": (
                urljoin(URL, link["href"]) if link else None
            )
        })

matches.sort(
    key=lambda match: match["datum_iso"],
    reverse=True
)

if not matches:
    raise RuntimeError(
        "Keine abgeschlossenen Spiele gefunden."
    )

OUTPUT.parent.mkdir(parents=True, exist_ok=True)

OUTPUT.write_text(
    json.dumps({
        "team": TEAM,
        "liga": "4. Bezirksliga Oberfranken H",
        "saison": "2026/27",
        "stand": datetime.now(timezone.utc).isoformat(
            timespec="minutes"
        ),
        "quelle": URL,
        "spiele": matches
    }, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8"
)

print(
    f"{len(matches)} abgeschlossene Spiele "
    f"nach {OUTPUT} geschrieben"
)
