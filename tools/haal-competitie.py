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
AGENDA = os.path.join(os.path.dirname(UIT), 'rbc.ics')

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


RBC_PAGINA = 'https://www.hollandsevelden.nl/clubs/r/rbc/'


LOGO_MAP = os.path.join(os.path.dirname(UIT), 'logos')
LOGOS = {}


def haal_logos(soep):
    """Clublogo's één keer ophalen en zelf bewaren: dan staan ze er ook
    zonder bereik, en vragen we de bron er niet elk uur om."""
    os.makedirs(LOGO_MAP, exist_ok=True)
    for cel in soep.select('table.match td.club'):
        naam, img = clubnaam(cel), cel.find('img', src=True)
        if not naam or not img or naam in LOGOS:
            continue
        slug = re.sub(r'[^a-z0-9]+', '-', naam.lower()).strip('-')
        pad = os.path.join(LOGO_MAP, slug + '.webp')
        if not os.path.exists(pad):
            try:
                url = img['src'] if img['src'].startswith('http') else 'https://www.hollandsevelden.nl' + img['src']
                verzoek = urllib.request.Request(url, headers=KOP)
                import certifi
                ctx = ssl.create_default_context(cafile=certifi.where())
            except ImportError:
                ctx = ssl.create_default_context()
            try:
                with urllib.request.urlopen(verzoek, timeout=30, context=ctx) as r, open(pad, 'wb') as f:
                    f.write(r.read())
            except Exception as fout:
                print(f'logo {naam} niet gehaald: {fout}', file=sys.stderr)
                continue
        LOGOS[naam] = 'logos/' + slug + '.webp'


THUIS_AFTRAP = '19:00'


def thuis_tijd_regels(seizoen, nieuws):
    verzet = {}
    for n in nieuws or []:
        m = re.search(r'RBC\s*[-–]\s*(.+?)\s+naar\s+(\d{1,2})[:.](\d\d)', n.get('titel', ''), re.I)
        if m:
            verzet[naam_slug(m.group(1)).split('-')[-1]] = f'{int(m.group(2)):02d}:{m.group(3)}'
    for d in seizoen:
        if not d['thuis'].strip().upper().startswith('RBC') or 'thuis_doelpunten' in d:
            continue
        tegen = naam_slug(d['uit']).split('-')[-1]
        bron = d.get('tijd') or ''
        d['tijd'] = verzet.pop(tegen, None) or (bron if bron >= THUIS_AFTRAP else THUIS_AFTRAP)


def lees_seizoen():
    """Alle wedstrijden van RBC dit seizoen, gespeeld en nog te spelen.
    De competitiepagina toont alleen de huidige ronde; de clubpagina het
    hele seizoen, en daar komt 'volgende wedstrijd' vandaan."""
    soep = BeautifulSoup(haal(RBC_PAGINA), 'html.parser')
    haal_logos(soep)
    duels = []
    for tabel in soep.select('table.match'):
        if 'RBC' not in schoon(tabel.find('caption')) and 'RBC' not in schoon(tabel.find('thead')):
            continue
        for tr in tabel.select('tbody tr'):
            datum, clubs, uitslag = tr.select_one('td.date'), tr.select('td.club'), tr.select_one('td.result')
            d = re.match(r'^(\d\d)-(\d\d)-(\d{4})$', schoon(datum))
            if not d or len(clubs) != 2:
                continue
            duel = {'datum': f'{d.group(3)}-{d.group(2)}-{d.group(1)}',
                    'thuis': clubnaam(clubs[0]), 'uit': clubnaam(clubs[1])}
            laatste = schoon(uitslag)
            u = re.match(r'^(\d+)\s*-\s*(\d+)$', laatste)
            t = re.match(r'^(\d{1,2})[.:](\d\d)', laatste)
            if u:
                duel['thuis_doelpunten'], duel['uit_doelpunten'] = int(u.group(1)), int(u.group(2))
            elif t:
                duel['tijd'] = f'{int(t.group(1)):02d}:{t.group(2)}'
            duels.append(duel)
    return sorted(duels, key=lambda x: x['datum'])


def schrijf_agenda(duels):
    """Het seizoen als agenda om op te abonneren: wie hem één keer
    toevoegt, krijgt verzette aftraptijden vanzelf mee. Alles vast
    (ook DTSTAMP), zodat het bestand alleen verandert als er echt
    iets verandert en de bot niet elk uur commit."""
    def tekst(s):
        return s.replace('\\', '\\\\').replace(',', '\\,').replace(';', '\\;')
    r = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//rbcroosendaal.com//seizoen//NL',
         'CALSCALE:GREGORIAN', 'METHOD:PUBLISH', 'X-WR-CALNAME:RBC Roosendaal',
         'X-WR-TIMEZONE:Europe/Amsterdam', 'REFRESH-INTERVAL;VALUE=DURATION:PT6H',
         'X-PUBLISHED-TTL:PT6H',
         'BEGIN:VTIMEZONE', 'TZID:Europe/Amsterdam',
         'BEGIN:DAYLIGHT', 'TZOFFSETFROM:+0100', 'TZOFFSETTO:+0200', 'TZNAME:CEST',
         'DTSTART:19700329T020000', 'RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU', 'END:DAYLIGHT',
         'BEGIN:STANDARD', 'TZOFFSETFROM:+0200', 'TZOFFSETTO:+0100', 'TZNAME:CET',
         'DTSTART:19701025T030000', 'RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU', 'END:STANDARD',
         'END:VTIMEZONE']
    for d in duels:
        dag = d['datum'].replace('-', '')
        naam = f"{d['thuis']} - {d['uit']}"
        if 'thuis_doelpunten' in d:
            naam += f" {d['thuis_doelpunten']}-{d['uit_doelpunten']}"
        thuis = d['thuis'].strip().upper().startswith('RBC')
        r += ['BEGIN:VEVENT',
              f"UID:{dag}-{re.sub(r'[^a-z0-9]+', '-', naam.lower().split(' - ')[0])}@rbcroosendaal.com",
              'DTSTAMP:20260101T000000Z',
              f'SUMMARY:{tekst(naam)}']
        if d.get('tijd'):
            u, m = d['tijd'].split(':')
            eind = f'{(int(u) + 2) % 24:02d}{m}00'
            r += [f'DTSTART;TZID=Europe/Amsterdam:{dag}T{u}{m}00',
                  f'DTEND;TZID=Europe/Amsterdam:{dag}T{eind}']
        else:
            r += [f'DTSTART;VALUE=DATE:{dag}']
        r += [f"LOCATION:{tekst('Atik Stadion, Roosendaal' if thuis else 'Uit bij ' + d['thuis'])}",
              'DESCRIPTION:Derde Divisie B · rbcroosendaal.com']
        if d.get('tijd') and 'thuis_doelpunten' not in d:
            # Twee uur voor de aftrap een seintje, net op tijd om te vertrekken.
            # (Een alarm hoort na alle eigenschappen van de afspraak.)
            r += ['BEGIN:VALARM', 'ACTION:DISPLAY', f"DESCRIPTION:{tekst(naam)} begint over 2 uur",
                  'TRIGGER:-PT2H', 'END:VALARM']
        r += ['END:VEVENT']
    r.append('END:VCALENDAR')
    with open(AGENDA, 'w', encoding='utf-8', newline='') as f:
        f.write('\r\n'.join(r) + '\r\n')


CLUB = 'https://www.rbcvoetbal.nl'
TEAM_PAGINA = CLUB + '/SVS/team/1'
DATA = os.path.join(os.path.dirname(UIT), 'data.json')
SPELERS_MAP = os.path.join(os.path.dirname(UIT), 'players')


def naam_slug(naam):
    import unicodedata
    plat = unicodedata.normalize('NFD', naam).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', '-', plat.lower()).strip('-')


def bewaar_foto(url, pad):
    """Clubportret ophalen en klein maken: 3:4, bovenkant blijft staan
    (daar zit het hoofd), zo'n 50 KB in plaats van 600."""
    from io import BytesIO
    from PIL import Image
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    with urllib.request.urlopen(urllib.request.Request(url, headers=KOP), timeout=30, context=ctx) as r:
        beeld = Image.open(BytesIO(r.read())).convert('RGB')
    b, h = beeld.size
    doel_h = round(b * 4 / 3)
    if h > doel_h:
        beeld = beeld.crop((0, 0, b, doel_h))
    beeld.thumbnail((480, 640))
    beeld.save(pad, 'JPEG', quality=80, optimize=True, progressive=True)


NIEUWS_MAP = os.path.join(os.path.dirname(UIT), 'nieuws')
HELD = os.path.join(os.path.dirname(UIT), 'held.webp')


def open_beeld(url):
    from io import BytesIO
    from PIL import Image
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    if url.startswith('http'):
        with urllib.request.urlopen(urllib.request.Request(url, headers=KOP), timeout=30, context=ctx) as r:
            return Image.open(BytesIO(r.read())).convert('RGB')
    return Image.open(url).convert('RGB')


def bewaar_nieuwsbeelden(nieuws):
    """Nieuwsfoto's één keer ophalen en als kleine WebP bewaren: sneller
    dan de grote JPEG's van de club, en ze werken ook zonder bereik.
    Het beeld van het bovenste bericht wordt held.webp, zodat de browser
    het al kan laden voordat de rest van de pagina er is."""
    os.makedirs(NIEUWS_MAP, exist_ok=True)
    gebruikt = set()
    for n in nieuws:
        bron = n.get('beeld_bron') or (n.get('beeld') if str(n.get('beeld', '')).startswith('http') else '')
        if not bron:
            n['beeld'] = ''
            continue
        n['beeld_bron'] = bron
        naam = naam_slug(os.path.splitext(bron.rsplit('/', 1)[-1])[0])[:60] + '.webp'
        pad = os.path.join(NIEUWS_MAP, naam)
        if not os.path.exists(pad):
            try:
                beeld = open_beeld(bron)
                beeld.thumbnail((1200, 1200))
                beeld.save(pad, 'WEBP', quality=72, method=6)
            except Exception as fout:
                print(f'nieuwsbeeld niet gehaald: {fout}', file=sys.stderr)
                n['beeld'] = ''
                continue
        n['beeld'] = 'nieuws/' + naam
        gebruikt.add(naam)
    for oud in os.listdir(NIEUWS_MAP):
        if oud not in gebruikt:
            os.remove(os.path.join(NIEUWS_MAP, oud))
    # Openingsbeeld: foto van het bovenste bericht, anders het stadion.
    eerste = nieuws[0].get('beeld') if nieuws else ''
    bron = os.path.join(os.path.dirname(UIT), eerste) if eerste else os.path.join(os.path.dirname(UIT), 'atik.png')
    try:
        from io import BytesIO
        origineel = open_beeld(bron)
        # Twee maten: telefoon en groot scherm. Alleen schrijven als het
        # beeld echt anders is, anders commit de bot elk uur een plaatje.
        for pad, maat, kwaliteit in ((HELD, 1400, 52), (HELD.replace('.webp', '-800.webp'), 800, 60)):
            beeld = origineel.copy(); beeld.thumbnail((maat, maat))
            buf = BytesIO(); beeld.save(buf, 'WEBP', quality=kwaliteit, method=6)
            if not os.path.exists(pad) or open(pad, 'rb').read() != buf.getvalue():
                open(pad, 'wb').write(buf.getvalue())
    except Exception as fout:
        print(f'openingsbeeld niet gemaakt: {fout}', file=sys.stderr)


OG = os.path.join(os.path.dirname(UIT), 'og.jpg')
FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts')
DAGEN = ['maandag', 'dinsdag', 'woensdag', 'donderdag', 'vrijdag', 'zaterdag', 'zondag']
MAANDEN = ['januari', 'februari', 'maart', 'april', 'mei', 'juni', 'juli', 'augustus', 'september', 'oktober', 'november', 'december']


def maak_voorvertoning(seizoen):
    """Het plaatje dat WhatsApp, Facebook en X tonen als iemand de link
    deelt: net na een wedstrijd de uitslag, anders de volgende wedstrijd."""
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    from io import BytesIO
    from datetime import date
    try:
        from zoneinfo import ZoneInfo
        vandaag = datetime.now(ZoneInfo('Europe/Amsterdam')).date()
    except Exception:
        vandaag = datetime.now(timezone.utc).date()
    als_datum = lambda d: date.fromisoformat(d['datum'])
    gespeeld = [d for d in seizoen if 'thuis_doelpunten' in d]
    komend = [d for d in seizoen if 'thuis_doelpunten' not in d and als_datum(d) >= vandaag]
    laatste = gespeeld[-1] if gespeeld else None
    if laatste and (vandaag - als_datum(laatste)).days <= 2:
        duel, soort = laatste, 'uitslag'
    elif komend:
        duel, soort = komend[0], 'volgende'
    else:
        return
    basis = os.path.dirname(UIT)
    # Alleen opnieuw tekenen als de inhoud verandert. Twee computers maken
    # van hetzelfde plaatje net andere bytes; zonder deze sleutel zou de
    # bot het plaatje steeds "verbeteren".
    sleutel = json.dumps([soort, duel, vandaag.isoformat() if soort == 'volgende' and als_datum(duel) == vandaag else ''], sort_keys=True)
    sleutel_pad = OG + '.sleutel'
    if os.path.exists(OG) and os.path.exists(sleutel_pad) and open(sleutel_pad).read() == sleutel:
        return
    kop = lambda n, w='Black': (lambda f: (f.set_variation_by_name(w), f)[1])(ImageFont.truetype(os.path.join(FONTS, 'BigShouldersDisplay.ttf'), n))
    B, H = 1200, 630
    doek = Image.open(os.path.join(basis, 'atik.png')).convert('RGB')
    s_ = max(B / doek.width, H / doek.height)
    doek = doek.resize((round(doek.width * s_), round(doek.height * s_)))
    doek = doek.crop(((doek.width - B) // 2, (doek.height - H) // 2, (doek.width - B) // 2 + B, (doek.height - H) // 2 + H))
    doek = doek.filter(ImageFilter.GaussianBlur(2))
    donker = Image.new('RGB', (B, H), (22, 18, 15))
    doek = Image.blend(doek, donker, .72)
    t = ImageDraw.Draw(doek)
    t.rectangle((0, 0, B, 10), fill=(255, 106, 19)); t.rectangle((0, H - 10, B, H), fill=(255, 106, 19))

    def logo(club, maat):
        pad = os.path.join(basis, 'rbc.png') if club.upper().startswith('RBC') else None
        if not pad:
            for n, p in (LOGOS or {}).items():
                if n == club:
                    pad = os.path.join(basis, p)
        if not pad or not os.path.exists(pad):
            return None
        im = Image.open(pad).convert('RGBA')
        f = maat / max(im.size)
        return im.resize((round(im.width * f), round(im.height * f)), Image.LANCZOS)

    for club, cx in ((duel['thuis'], 870), (duel['uit'], 1070)):
        im = logo(club, 170)
        if im:
            doek.paste(im, (cx - im.width // 2, 230 - im.height // 2), im)

    dt = als_datum(duel)
    if soort == 'uitslag':
        wij = duel['thuis_doelpunten'] if duel['thuis'].upper().startswith('RBC') else duel['uit_doelpunten']
        zij = duel['uit_doelpunten'] if duel['thuis'].upper().startswith('RBC') else duel['thuis_doelpunten']
        label = 'GEWONNEN!' if wij > zij else ('GELIJKSPEL' if wij == zij else 'UITSLAG')
        groot = f"{duel['thuis_doelpunten']} – {duel['uit_doelpunten']}"
        onder = f"{duel['thuis']} – {duel['uit']}  ·  {DAGEN[dt.weekday()]} {dt.day} {MAANDEN[dt.month - 1]}"
    else:
        label = 'VANDAAG!' if dt == vandaag else 'VOLGENDE WEDSTRIJD'
        groot = f"{duel['thuis']} – {duel['uit']}"
        plek = 'Atik Stadion' if duel['thuis'].upper().startswith('RBC') else f"uit bij {duel['thuis']}"
        onder = f"{DAGEN[dt.weekday()]} {dt.day} {MAANDEN[dt.month - 1]}  ·  {duel.get('tijd') or ''}  ·  {plek}"
    t.text((70, 90), label, font=kop(64), fill=(255, 106, 19))
    maat = 150
    while maat > 60 and t.textlength(groot.upper(), font=kop(maat)) > (720 if soort == 'volgende' else 700):
        maat -= 4
    t.text((66, 170), groot.upper(), font=kop(maat), fill=(255, 255, 255))
    t.text((70, 400), onder, font=ImageFont.truetype(os.path.join(FONTS, 'Barlow-SemiBold.ttf'), 36), fill=(207, 197, 184))
    crest = Image.open(os.path.join(basis, 'rbc.png')).convert('RGBA')
    crest = crest.resize((round(crest.width * 96 / crest.height), 96), Image.LANCZOS)
    doek.paste(crest, (70, 500), crest)
    t.text((70 + crest.width + 22, 548), 'RBCROOSENDAAL.COM', font=kop(44), fill=(255, 106, 19), anchor='lm')
    doek.save(OG, 'JPEG', quality=84, optimize=True, progressive=True)
    open(sleutel_pad, 'w').write(sleutel)


def lees_team(oud_team):
    """De teampagina van de club: wie er in de selectie zit met welk
    rugnummer en officiële foto, de staf, de trainingstijden, en per
    wedstrijd de scheidsrechter en het verslag."""
    soep = BeautifulSoup(haal(TEAM_PAGINA), 'html.parser')
    spelers = []
    for blok in soep.select('div.player'):
        m = re.match(r'^(.+?)\s*\((\d{1,2})\)$', schoon(blok.find('p')))
        if not m:
            continue
        img = blok.find('img', src=True)
        foto = img['src'] if img and 'category=members' in img['src'] else ''
        spelers.append({'naam': m.group(1), 'nummer': int(m.group(2)), 'foto_bron': foto})

    info, staf, training = soep.select_one('div.info'), [], []
    for p in (info.find_all('p') if info else []):
        kop = schoon(p.find('b'))
        rest = re.sub(r'\s+', ' ', p.get_text(' ', strip=True))[len(kop):].strip()
        if kop in ('Trainer', 'Assistent Trainer', 'Teammanager', 'Verzorger', 'Keeperstrainer', 'Fysiotherapeut'):
            staf.append({'rol': kop, 'namen': [n.strip() for n in rest.split(',') if n.strip()]})
        elif kop == 'Training':
            training = re.findall(r'(\w+dag):\s*([\d:]+\s*-\s*[\d:]+)', rest)
            training = [{'dag': d, 'tijd': t.replace(' ', '')} for d, t in training]

    wedstrijden = {}
    for tr in soep.select('table tr'):
        cellen = tr.find_all('td')
        if len(cellen) != 5:
            continue
        d = re.match(r'^(\d\d)-(\d\d)-(\d{4})$', schoon(cellen[0]))
        if not d:
            continue
        link = cellen[4].find('a', href=True)
        wedstrijden[f'{d.group(3)}-{d.group(2)}-{d.group(1)}'] = {
            'scheidsrechter': schoon(cellen[3]),
            'verslag': ('https:' + link['href'] if link['href'].startswith('//') else link['href']) if link else ''
        }
    return {'spelers': spelers, 'staf': staf or (oud_team or {}).get('staf', []),
            'training': training or (oud_team or {}).get('training', []), 'wedstrijden': wedstrijden}


def werk_selectie_bij(team):
    """De teampagina van de club is leidend voor wie er in de selectie
    zit en met welk nummer. Leeftijd, positie en nationaliteit die we al
    hadden blijven staan. Een halve pagina (storing) negeren we."""
    spelers = team.get('spelers') or []
    if len(spelers) < 15:
        return False
    with open(DATA, encoding='utf-8') as f:
        data = json.load(f)
    oud = data.get('squad', [])
    per_nr = {p['number']: p for p in oud}
    per_naam = {naam_slug(p['name']): p for p in oud}
    bronnen = data.setdefault('photo_sources', {})
    nieuw = []
    for s in sorted(spelers, key=lambda x: x['nummer']):
        # Eerst op naam (nummers wisselen weleens), dan op nummer met een
        # achternaam die klopt ("Amr" op de clubsite, "Amir" elders).
        p = per_naam.get(naam_slug(s['naam']))
        if not p:
            k = per_nr.get(s['nummer'])
            if k and naam_slug(k['name']).split('-')[-1] == naam_slug(s['naam']).split('-')[-1]:
                p = k
        p = dict(p) if p else {'name': s['naam'], 'nationality': None, 'photo': None}
        p['number'] = s['nummer']
        if s['foto_bron']:
            pad = f"players/{naam_slug(p['name'])}.jpg"
            if bronnen.get(p['name']) != s['foto_bron'] or not os.path.exists(os.path.join(os.path.dirname(UIT), pad)):
                try:
                    os.makedirs(SPELERS_MAP, exist_ok=True)
                    bewaar_foto(s['foto_bron'], os.path.join(os.path.dirname(UIT), pad))
                    bronnen[p['name']] = s['foto_bron']
                except Exception as fout:
                    print(f"foto {p['name']} niet gehaald: {fout}", file=sys.stderr)
            if bronnen.get(p['name']) == s['foto_bron']:
                p['photo'] = pad
        nieuw.append(p)
    if nieuw == oud:
        return False
    data['squad'] = nieuw
    data['squad_bijgewerkt'] = datetime.now(timezone.utc).date().isoformat()
    data['squad_bron'] = 'rbcvoetbal.nl'
    data['photos'] = {p['name']: p['photo'] for p in nieuw if p.get('photo')}
    with open(DATA, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write('\n')
    print(f'selectie bijgewerkt: {len(nieuw)} spelers')
    return True


def lees_nieuws(oud_nieuws):
    """Nieuws van de club zelf. X laat een tijdlijn alleen nog aan
    ingelogde bezoekers zien, dus dat werkt niet voor een supporter
    op de tribune. De clubsite wel, en daar staan de wedstrijd-
    verslagen met de doelpuntenmakers in.

    Van berichten die we al hadden halen we de tekst niet opnieuw op:
    schelen verzoeken, en de site is van de club zelf."""
    try:
        html = haal(CLUB + '/nieuws')
    except Exception:
        return oud_nieuws or []
    soep = BeautifulSoup(html, 'html.parser')

    bekend = {n['url']: n for n in (oud_nieuws or [])}
    uit, gezien = [], set()
    for a in soep.find_all('a', href=True):
        href = a['href']
        if '/nieuws/' not in href.lower():
            continue
        # De site gebruikt protocol-relatieve links: //www.rbcvoetbal.nl/...
        if href.startswith('//'):
            url = 'https:' + href
        elif href.startswith('http'):
            url = href
        else:
            url = CLUB + '/' + href.lstrip('/')
        if url in gezien:
            continue
        tekst = schoon(a)
        m = re.match(r'(\d{2}-\d{2}-\d{4})\s*:\s*(.+)', tekst)
        if not m:
            continue
        gezien.add(url)
        uit.append({'datum': m.group(1), 'titel': m.group(2).strip(), 'url': url})
        if len(uit) >= 8:
            break

    for n in uit:
        oud = bekend.get(n['url'])
        if oud and oud.get('tekst') and 'beeld' in oud:
            n['tekst'], n['beeld'] = oud['tekst'], oud['beeld']
            if oud.get('beeld_bron'):
                n['beeld_bron'] = oud['beeld_bron']
            continue
        try:
            art = BeautifulSoup(haal(n['url']), 'html.parser')
            # De foto bij het bericht staat op de beeldbank van de club;
            # de kopfoto van de site zelf slaan we over.
            n['beeld'] = ''
            for img in art.find_all('img', src=True):
                if '/uploads/images/news/' in img['src']:
                    n['beeld'] = ('https:' + img['src']) if img['src'].startswith('//') else img['src']
                    break
            for weg in art(['script', 'style', 'nav', 'header', 'footer']):
                weg.decompose()
            heel = re.sub(r'\s+', ' ', art.get_text(' ', strip=True))
            # De titel staat ook in <title> en in het menu; de laatste
            # keer is die boven het artikel zelf.
            i = heel.rfind(n['titel'])
            if i >= 0:
                heel = heel[i + len(n['titel']):]
            heel = re.sub(r'^\s*\d{2}-\d{2}-\d{4}\s*', '', heel).strip()
            n['tekst'] = heel[:600].rsplit(' ', 1)[0]
        except Exception:
            n.setdefault('tekst', '')
            n.setdefault('beeld', '')
    return uit


def main():
    html = haal(BRON)
    soep = BeautifulSoup(html, 'html.parser')

    stand, zones = lees_stand(soep)
    periodes = lees_periodes(soep)
    rondes = lees_rondes(soep)
    uitslagen, programma = lees_duels(soep)

    oud_bestand = {}
    if os.path.exists(UIT):
        try:
            with open(UIT, encoding='utf-8') as f:
                oud_bestand = json.load(f)
        except Exception:
            oud_bestand = {}
    nieuws = lees_nieuws(oud_bestand.get('nieuws'))
    bewaar_nieuwsbeelden(nieuws)
    try:
        team = lees_team(oud_bestand.get('team'))
        werk_selectie_bij(team)
        team.pop('spelers', None)
    except Exception as fout:
        print(f'teampagina niet gelezen: {fout}', file=sys.stderr)
        team = oud_bestand.get('team', {})
    # De clubpagina mag wegvallen zonder dat de stand mislukt:
    # dan houden we het seizoen van de vorige keer.
    try:
        seizoen = lees_seizoen() or oud_bestand.get('rbc_seizoen', [])
        # Na het fluitsignaal toont de bron de uitslag in plaats van de
        # aftrap; de tijd van eerder bewaren we, voor de agenda.
        tijden = {(d['datum'], d['thuis']): d['tijd']
                  for d in oud_bestand.get('rbc_seizoen', []) if d.get('tijd')}
        for d in seizoen:
            if not d.get('tijd') and (d['datum'], d['thuis']) in tijden:
                d['tijd'] = tijden[(d['datum'], d['thuis'])]
    except Exception as fout:
        print(f'seizoen niet gelezen: {fout}', file=sys.stderr)
        seizoen = oud_bestand.get('rbc_seizoen', [])

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
        'nieuws': nieuws,
        'rbc_seizoen': seizoen,
        'logos': LOGOS or oud_bestand.get('logos', {}),
        'team': team,
        'tickets': 'https://sales.ticketing.cm.com/ticketing2627/nl-nl/cc75e33c-0235-4b2b-af30-c19971ddebd3',
    }

    # Aftrap thuis: de bronnen zetten er vaak de standaardtijd van de bond
    # (14:00, 18:00) bij, maar RBC speelt thuis om 19:00, en 19:30 zodra
    # dat is aangekondigd. Verzet de club een wedstrijd, dan staat dat in
    # het nieuws ("RBC - Zwaluwen naar 19:30 uur") en gaat dat voor.
    thuis_tijd_regels(seizoen, nieuws)
    data['rbc_seizoen'] = seizoen
    schrijf_agenda(seizoen)
    try:
        maak_voorvertoning(seizoen)
    except Exception as fout:
        print(f'voorvertoning niet gemaakt: {fout}', file=sys.stderr)

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
          f'{len(nieuws)} nieuwsberichten, {len(seizoen)} RBC-duels, periode {huidige}, ronde {gespeeld}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
