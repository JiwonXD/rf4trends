"""화면 표시 전용 번역. 저장·필터·점수 계산은 한국어 원본을 유지한다."""
import csv
import re
from pathlib import Path

BASE_DIR = Path(__file__).parent
LANGUAGES = {'ko': '한국어', 'en': 'English', 'ru': 'Русский', 'zh-CN': '简体中文'}
COOKIE_NAME = 'rf4_language'


def _read_table(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    table = {}
    for row in rows:
        for name in [row['ko']] + row.get('aliases', '').split('|'):
            if name:
                table[name.replace('\xa0', ' ')] = row
    return table


def language(request):
    value = request.cookies.get(COOKIE_NAME, 'ko')
    return value if value in LANGUAGES else 'ko'


UI = _read_table(BASE_DIR / 'ui_translations.csv') if (BASE_DIR / 'ui_translations.csv').exists() else {}
NAMES = {kind: _read_table(BASE_DIR / 'translations' / (kind + '.csv'))
         for kind in ('species', 'waterbodies', 'baits')
         if (BASE_DIR / 'translations' / (kind + '.csv')).exists()}


def translate(text, lang):
    if not text or lang == 'ko':
        return text
    return UI.get(text, {}).get(lang.replace('-', '_')) or text


def translate_name(kind, name, lang):
    if not name or lang == 'ko':
        return name
    table = NAMES.get(kind, {})
    lookup = name.replace('\xa0', ' ')
    column = lang.replace('-', '_')
    if lookup in table:
        return table[lookup].get(column) or name
    if kind == 'baits':
        if ';' in name:
            return '; '.join(translate_name(kind, part.strip(), lang) for part in name.split(';'))
        match = re.fullmatch(r'(.+?)(\s+\d+(?:\.\d+)?(?:\s*(?:mm|g))?)', lookup)
        if match and match[1] in table:
            return (table[match[1]].get(column) or match[1]) + match[2]
    return name


def context(request):
    lang = language(request)
    return {
        'lang': lang, 'languages': LANGUAGES,
        't': lambda text: translate(text, lang),
        'tr_name': lambda kind, name: translate_name(kind, name, lang),
        'ui_strings': {key: translate(key, lang) for key in UI},
    }


def detail_names(detail, lang):
    return {
        'baits': {r['bait']: translate_name('baits', r['bait'], lang)
                  for r in detail['records'] if r['bait']},
        'waterbodies': {name: translate_name('waterbodies', name, lang)
                       for name in set(detail['habitat_waterbodies']) |
                       {r['waterbody'] for r in detail['records']}},
    }
