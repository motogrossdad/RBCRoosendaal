#!/usr/bin/env python3
"""
Haalt de stand, de periodestanden en het programma van Derde Divisie B op
en schrijft ze naar competitie.json.

Waarom niet in de app zelf: op de tribune is het bereik slecht en de
publieke CORS-proxies zijn traag en wisselvallig. Eén keer per uur hier
ophalen en het resultaat meecommitten betekent dat het bord meteen vol
staat, ook zonder bereik.

Draait via .github/workflows/competitie.yml, of met de hand:
    python3 tools/haal-competitie.py
"""

import json
import os
import re
import sys
import ssl
import urllib.request
from datetime import datetime, timezone

from bs4 import BeautifulSoup

SEIZOEN = os.environ.get('SEIZOEN', '2026-2027')
BRON = f'https://www.hollandsevelden.nl/competities/{SEIZOEN}/landelijk/derde-divisie-b/'
UIT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'competitie.json')

# Netjes: wie we zijn en waarvoor. robots.txt van hollandsevelden staat
# dit toe (Allow: /, alleen /cookies/ is dicht).
KOP = {
    # Alleen latin-1 in een header, dus geen liggend streepje hier.
    'User-Agent': 'RBCRoosendaal.com supportersbord (+https://rbcroosendaal.com); 1x per uur',
    'Accept-Language': 'nl-NL,nl;q=0.9',
}


def haal(url):
    # Een python.org-installatie op een Mac heeft vaak geen wortelcertificaten;
    # certifi lost dat op als het er is. Op de bouwmachine staat het al goed.
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        context = ssl.create_default_context()
    verzoek = urllib.request.Request(url, headers=KOP)
    with urllib.request.urlopen(verzoek, timeout=30, context=context) as antwoord:
        return antwoord.read().decode('utf-8', 'replace')


def schoon(el):
    return re.sub(r'\s+', ' ', el.get_text(' ', strip=True)).strip() if el else ''


def clubnaam(cel):
    """De clubcel bevat een logo en een link; we willen alleen de naam."""
    link = cel.find('a')
    return schoon(link) if link else schoon(cel)


def getal(tekst, standaard=0):
    m = re.search(r'-?\d+', tekst or '')
    return int(m.group()) if m else standaard


def lees_stand(soep):
    tabel = soep.select_one('table.league')
    if not tabel:
        raise SystemExit('stand: tabel niet gevonden — is de opmaak van de bron veranderd?')

    # De zones staan in de class van de tabel: promote-1 playoff-4 relegate-2
    klassen = ' '.join(tabel.get('class', []))
    zones = {
        'promotie': getal(re.search(r'promote-(\d+)', klassen).group(1)) if re.search(r'promote-(\d+)', klassen) else 0,
        'nacompetitie': getal(re.search(r'playoff-(\d+)', klassen).group(1)) if re.search(r'playoff-(\d+)', klassen) else 0,
        'degradatie': getal(re.search(r'relegate-(\d+)', klassen).group(1)) if re.search(r'relegate-(\d+)', klassen) else 0,
    }

    rijen = []
    for tr in tabel.select('tbody tr'):
        pos = schoon(tr.find('th'))
        cellen = tr.find_all('td')
        if len(cellen) < 7:
            continue
        vorm = []
        for img in tr.select('td.form img'):
            bestand = (img.get('src') or '').rsplit('/', 1)[-1][:1]
            if bestand in ('w', 'g', 'v'):
                vorm.append({'uitslag': bestand, 'wat': img.get('title', '')})
        rijen.append({
            'pos': getal(pos),
            'club': clubnaam(cellen[0]),
            'wed': getal(schoon(cellen[1])),
            'wgv': schoon(cellen[2]),
            'pnt': getal(schoon(cellen[3])),
            'dv': getal(schoon(cellen[4])),
            'dt': getal(schoon(cellen[5])),
            'ds': getal(schoon(cellen[6])),
            'vorm': vorm[-5:],
        })
    if not rijen:
        raise SystemExit('stand: geen rijen gevonden')
    return rijen, zones


def lees_periodes(soep):
    periodes = []
    for tabel in soep.select('table.league-sm'):
        titel = schoon(tabel.find('caption')) or schoon(tabel.find('h3'))
        titel = re.sub(r'^(\d)\s*e\s*periode$', r'\1e periode', titel, flags=re.I)
        if 'periode' not in titel.lower():
            continue
        rijen = []
        for tr in tabel.select('tbody tr'):
            cellen = tr.find_all('td')
            if len(cellen) < 4:
                continue
            rijen.append({
                'pos': getal(schoon(tr.find('th'))),
                'club': clubnaam(cellen[0]),
                'wed': getal(schoon(cellen[1])),
                'pnt': getal(schoon(cellen[2])),
                'ds': getal(schoon(cellen[3])),
            })
        if rijen:
            periodes.append({'naam': titel, 'rijen': rijen})
    return periodes


def lees_rondes(soep):
    """Hoeveel speelrondes elke periode telt. Staat in de p/d-regeling."""
    tekst = re.sub(r'\s+', ' ', soep.get_text(' ', strip=True))
    return [int(n) for n in re.findall(r'(\d+)e Periode (\d+) speelrondes', tekst)[0:0]] or \
           [int(n) for _, n in re.findall(r'(\d+)e Periode (\d+) speelrondes', tekst)]


def lees_duels(soep):
    """De bron zet gespeeld en nog te spelen door elkaar in dezelfde
    tabellen; de laatste kolom is dan of een uitslag (2 - 2) of een
    aanvangstijd (14.30 uur). Daarop splitsen we ze."""
    gespeeld, komt = [], []
    for tabel in soep.select('table.match, table[class*="match"]'):
        datum = re.sub(r'^wedstrijden op\s*', '', schoon(tabel.find('caption')), flags=re.I)
        for tr in tabel.select('tbody tr'):
            cellen = tr.find_all('td')
            if len(cellen) < 4:
                continue
            duel = {'datum': datum, 'thuis': clubnaam(cellen[0]), 'uit': clubnaam(cellen[2])}
            laatste = schoon(cellen[3])
            uitslag = re.match(r'^(\d+)\s*-\s*(\d+)$', laatste)
            if uitslag:
                duel['thuis_doelpunten'] = int(uitslag.group(1))
                duel['uit_doelpunten'] = int(uitslag.group(2))
                gespeeld.append(duel)
            else:
                duel['tijd'] = re.sub(r'\s*uur$', '', laatste).replace('.', ':')
                komt.append(duel)
    return gespeeld, komt


def main():
    html = haal(BRON)
    soep = BeautifulSoup(html, 'html.parser')

    stand, zones = lees_stand(soep)
    periodes = lees_periodes(soep)
    rondes = lees_rondes(soep)
    uitslagen, programma = lees_duels(soep)

    # In welke periode zitten we? De eerste periode die nog niet vol is.
    gespeeld = max((r['wed'] for r in stand), default=0)
    huidige, gehad = 1, 0
    for i, aantal in enumerate(rondes or [], start=1):
        if gespeeld < gehad + aantal:
            huidige = i
            break
        gehad += aantal
        huidige = min(i + 1, len(rondes))
    resterend = (gehad + rondes[huidige - 1] - gespeeld) if rondes else None

    data = {
        'bijgewerkt': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        'bron': BRON,
        'competitie': 'Derde Divisie B',
        'seizoen': SEIZOEN.replace('-', '/'),
        'zones': zones,
        'periode_rondes': rondes,
        'periode_nu': huidige,
        'periode_resterend': resterend,
        'speelronde': gespeeld,
        'stand': stand,
        'periodes': periodes,
        'programma': programma[:40],
        'uitslagen': uitslagen[-40:],
    }

    oud = None
    if os.path.exists(UIT):
        try:
            with open(UIT, encoding='utf-8') as f:
                oud = json.load(f)
        except Exception:
            oud = None

    # Alleen de tijdstempel verschilt? Dan niets committen.
    if oud:
        a = {k: v for k, v in oud.items() if k != 'bijgewerkt'}
        b = {k: v for k, v in data.items() if k != 'bijgewerkt'}
        if a == b:
            print('geen wijziging')
            return 0

    with open(UIT, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write('\n')
    print(f'geschreven: {len(stand)} clubs, {len(periodes)} periodes, '
          f'{len(programma)} te spelen, {len(uitslagen)} gespeeld, '
          f'periode {huidige}, ronde {gespeeld}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
