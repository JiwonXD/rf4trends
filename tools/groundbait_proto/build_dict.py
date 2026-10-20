"""ingredients_raw.csv → dictionary.js (밑밥 조합식 번역 프로토타입용 사전).

개체 = 이름 하나(제조사 무시). 크기·으깨짐은 사전 항목이 아니라 인식 시 파싱하는 속성.
cat: base / feed / sinking / popup / pellet / attractant  (툴팁의 역할 라벨로 후보를 좁히는 데 씀)
각 언어 열은 '|'로 구분된 별칭 목록, 첫 번째가 출력용 대표 표기.
"""
import csv, json, re, sys
from collections import OrderedDict

SRC = "ingredients_raw.csv"
OUT = "dictionary.js"

def cat_of(r):
    if r["note"] == "삭제됨":
        return None
    t, ty = r["tab"], r["type"]
    if t == "밑밥":
        return {"기본": "base", "첨가물": "feed", "음식": "feed", "유인제": "attractant"}[ty]
    if t == "보일리":
        return {"싱킹 보일리": "sinking", "팝업 보일리": "popup", "펠릿": "pellet"}.get(ty)  # 딥·인공옥수수·지그리그폼·견과류 제외
    if t == "천연미끼":
        return "feed"  # 실제 라벨 미확인 — 일단 사료 첨가제로 취급
    return None

SIZE = re.compile(r"\s*\d+(?:/\d+)*\s*$")   # 원본의 '7/4/3' 크기 표기 제거
def base(s):
    return SIZE.sub("", s or "").strip()

# 원본 오류 수정 (스샷·게임 표기로 확인된 것)
FIXES = [
    (lambda r: r["tab"] == "보일리" and r["type"] == "팝업 보일리" and r["ko"] == "신선한 라임", "ko", "신선한 라임 팝업"),
    (lambda r: r["type"] == "유인제" and r["ko"] == "아미노산" and r["en"] == "Allspice", "en", "Amino acid"),
    (lambda r: r["type"] == "유인제" and r["ko"] == "올스파이스", "en", "Allspice"),
    (lambda r: r["type"] == "유인제" and r["ko"] == "스파이시 마늘", "en", "Spicy Garlic"),
    (lambda r: r["ko"] == "달콤한 옥수수 팝업", "en", "Sweet Corn Pop-Ups"),
    (lambda r: r["ko"] == "아미노 팝업", "en", "Amino Pop-Ups"),
    (lambda r: r["ko"] == "달콤한 옥수수 팝업", "ru", "Сладкая кукуруза Рор-Ups"),
    (lambda r: r["ko"] == "야생 자두 팝업", "ru", "Дикая слива Рор-Ups"),
    (lambda r: r["ko"] == "달콤한 옥수수" and r["type"] == "싱킹 보일리", "ru", "Сладкая кукуруза"),
    (lambda r: r["ko"] == "야생 자두" and r["type"] == "싱킹 보일리", "ru", "Дикая слива"),
    (lambda r: r["en"].startswith("lack Krill"), "en", "Black Krill Feed Pellets 5"),
    (lambda r: r["ru"] == "Краби мидии", "ru", "Краб и мидии"),
    (lambda r: r["ru"] == "Жгучий 6", "ko", "핫 사료 펠릿 6"),  # 게임 한국어 표기는 '핫 사료 펠릿'
]
# 스샷으로 확인된 게임 실표기 (앞에 두면 대표 표기가 됨)
ALIASES = [
    ("다진 지렁이", "feed", "ru", "Рубленый червь", True),
    ("블랙 커런트 사료 펠릿", "feed", "cn", "黑加仑", True),
    ("블랙 커런트 팝업", "popup", "cn", "黑加仑 PopUp", True),
    ("블랙 커런트", "attractant", "cn", "黑加仑", True),
    ("블랙 커런트", "sinking", "cn", "黑加仑", False),
]

LABELS = {
    "section":    {"ko": "재료",       "ru": "СОСТАВ",              "cn": "类型",          "en": ""},
    "end":        {"ko": "상태",       "ru": "СОСТОЯНИЕ",           "cn": "情况",          "en": ""},
    "base":       {"ko": "베이스",     "ru": "Основа",              "cn": "基础料",        "en": ""},
    "feed":       {"ko": "사료 첨가제", "ru": "Добавка в прикорму",  "cn": "诱饵添加剂",     "en": ""},
    "sinking":    {"ko": "싱킹 보일리", "ru": "Тонущие бойлы",       "cn": "下沉香煮成饵",   "en": ""},
    "popup":      {"ko": "팝업 보일리", "ru": "",                    "cn": "Pop-up 香煮成饵", "en": ""},
    "pellet":     {"ko": "",           "ru": "",                    "cn": "",              "en": ""},
    "attractant": {"ko": "유인제",     "ru": "Ароматизатор",        "cn": "香料",          "en": ""},
    "crushed":    {"ko": "으깨짐",     "ru": "дробл",               "cn": "碎的",          "en": ""},
}

rows = list(csv.DictReader(open(SRC, encoding="utf-8-sig")))
for cond, col, val in FIXES:
    hit = [r for r in rows if cond(r)]
    if len(hit) != 1:
        print("fix 대상 %d건: %s=%s" % (len(hit), col, val), file=sys.stderr)
    for r in hit:
        r[col] = val

ents = OrderedDict()
for r in rows:
    c = cat_of(r)
    if not c:
        continue
    k = (c, base(r["ko"]))
    e = ents.setdefault(k, {"cat": c, "ko": [], "en": [], "ru": [], "cn": []})
    for L in ("ko", "en", "ru", "cn"):
        v = base(r[L])
        if v and v not in e[L]:
            e[L].append(v)

for ko, cat, L, val, first in ALIASES:
    e = ents.get((cat, ko))
    if not e:
        print("alias 대상 없음", ko, cat, file=sys.stderr); continue
    if val in e[L]:
        e[L].remove(val)
    e[L] = [val] + e[L] if first else e[L] + [val]

out = [{"id": i, **e} for i, e in enumerate(ents.values(), 1)]
with open(OUT, "w", encoding="utf-8") as f:
    f.write("window.RF4_DICT = " + json.dumps(out, ensure_ascii=False) + ";\n")
    f.write("window.RF4_LABELS = " + json.dumps(LABELS, ensure_ascii=False) + ";\n")
from collections import Counter
print(len(out), dict(Counter(e["cat"] for e in out)))
