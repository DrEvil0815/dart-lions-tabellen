#!/usr/bin/env python3
"""Holt die öffentlich abrufbare NuLiga-Tabelle und speichert sie für GitHub Pages."""
import json
from datetime import datetime, timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup

URL = 'https://bdv-dart.liga.nu/cgi-bin/WebObjects/nuLigaDARTDE.woa/wa/groupPage?championship=OFr+2026%2F27&group=211414'
OUT = Path('data/dart-lions-ii.json')

def parse(html):
    soup = BeautifulSoup(html, 'html.parser')
    tables = soup.select('table.result-set')
    table = next((t for t in tables if 'Rang' in t.get_text(' ', strip=True) and 'Dart Lions Gräfenberg II' in t.get_text(' ', strip=True)), None)
    if table is None:
        raise ValueError('NuLiga-Ligatabelle nicht gefunden; bestehende Daten bleiben unverändert.')
    result = []
    for tr in table.select('tr')[1:]:
        c = [td.get_text(' ', strip=True).replace('\xa0', ' ') for td in tr.find_all('td', recursive=False)]
        if len(c) < 11 or not c[1].isdigit():
            continue
        result.append({'platz': int(c[1]), 'team': c[2], 'begegnungen': c[3], 'siege': c[4], 'unentschieden': c[5], 'niederlagen': c[6], 'spiele': c[7], 'differenz': c[8], 'legs': c[9], 'punkte': c[10]})
    if len(result) < 6 or not any('Dart Lions Gräfenberg II' in x['team'] for x in result):
        raise ValueError('Tabelle unvollständig oder falsche Staffel; Daten nicht überschrieben.')
    return result

if __name__ == '__main__':
    res = requests.get(URL, timeout=35, headers={'User-Agent': 'Mozilla/5.0 (compatible; DartLionsStandings/1.0)'})
    res.raise_for_status()
    res.encoding = res.apparent_encoding or 'utf-8'
    rows = parse(res.text)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({'liga':'4. Bezirksliga Oberfranken F','team':'Dart Lions Gräfenberg II','stand': datetime.now(timezone.utc).isoformat(timespec='minutes'), 'quelle':URL,'rows':rows}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'{len(rows)} Teams gespeichert: {OUT}')
