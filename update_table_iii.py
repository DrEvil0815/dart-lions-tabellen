
from pathlib import Path
from datetime import datetime, timezone
import json
import requests
from bs4 import BeautifulSoup

URL = (
    "https://bdv-dart.liga.nu/cgi-bin/WebObjects/"
    "nuLigaDARTDE.woa/wa/groupPage?"
    "championship=OFr+2026%2F27&group=211415"
)

TEAM = "Dart Lions Gräfenberg III"
OUT = Path("data/dart-lions-iii.json")

response = requests.get(
    URL,
    timeout=35,
    headers={"User-Agent": "Mozilla/5.0"}
)
response.raise_for_status()
response.encoding = response.apparent_encoding or "utf-8"

soup = BeautifulSoup(response.text, "html.parser")

table = next(
    (
        t for t in soup.select("table.result-set")
        if "Rang" in t.get_text(" ", strip=True)
        and TEAM in t.get_text(" ", strip=True)
    ),
    None
)

if table is None:
    raise ValueError("Ligatabelle von Dart Lions III nicht gefunden")

rows = []

for tr in table.select("tr")[1:]:
    c = [
        td.get_text(" ", strip=True).replace("\xa0", " ")
        for td in tr.find_all("td", recursive=False)
    ]

    if len(c) < 11 or not c[1].isdigit():
        continue

    rows.append({
        "platz": int(c[1]),
        "team": c[2],
        "begegnungen": c[3],
        "siege": c[4],
        "unentschieden": c[5],
        "niederlagen": c[6],
        "spiele": c[7],
        "differenz": c[8],
        "legs": c[9],
        "punkte": c[10]
    })

if len(rows) < 6 or not any(
    TEAM in r["team"] for r in rows
):
    raise ValueError("Tabelle unvollständig oder falsche Staffel")

OUT.parent.mkdir(parents=True, exist_ok=True)

OUT.write_text(
    json.dumps({
        "liga": "Oberfranken 2026/27 – Staffel 211415",
        "team": TEAM,
        "stand": datetime.now(timezone.utc).isoformat(
            timespec="minutes"
        ),
        "quelle": URL,
        "rows": rows
    }, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8"
)

print(f"{len(rows)} Mannschaften gespeichert: {OUT}")
