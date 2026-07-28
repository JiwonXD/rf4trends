# scoring 단위 검증 — 미끼 패밀리 정규화(bait_family, D-49) + 사전계산 스토어(D-53)
import sys, os as _os
sys.stdout.reconfigure(encoding="utf-8")  # 한국 Windows 콘솔(cp949)에서 em-dash 출력 크래시 방지
sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'rf4site'))

import sqlite3
import scoring
from scoring import bait_family

fails = []
def check(label, cond):
    print(('PASS' if cond else 'FAIL'), label)
    if not cond: fails.append(label)

CASES = [
    ("Active W-Stick7.0-05", "active w-stick -05"), ("Active W-Stick 2.5-05", "active w-stick -05"),
    ("AngryWalker S8-001", "angrywalker -001"), ("Funky Minnow F11-002", "funky minnow -002"),
    ("Balsa Crank 80F-003", "balsa crank -003"), ("Veikko 25g-011", "veikko -011"),
    ("Hijacker slim 7SP-002", "hijacker slim -002"), ("Nasty Worm 4.5-001", "nasty worm -001"),
    ("Nasty worm 7-001", "nasty worm -001"), ("Spiker #2 016", "spiker -016"),
    ("Hornet #3 005", "hornet -005"), ("연어 팝업 20", "연어 팝업"), ("굴 16", "굴"),
    ("꿀 반죽", "꿀 반죽"), ("Jiggmeister DC 1000", "jiggmeister dc"),
    ("Stor Fisk M25-600 #17", "stor fisk -#17"), ("Pilker №2-300 RD", "pilker №2 -rd"),
    ("Super Grub 4 CLR-B", "super grub -clr-b"), ("Icon Fat m-001", "icon fat -001"),
    ("Furry T01", "furry t01"), ("UL Popper-001", "ul popper-001"),
    ("Orig Walker-002", "orig walker-002"), ("지렁이", "지렁이"),
    ("핫 체리 수용성 20; 뉴트럴 25", "뉴트럴; 핫 체리 수용성"),
    ("뉴트럴 25; 핫 체리 수용성 20", "뉴트럴; 핫 체리 수용성"),
    ("파리; 파리", "파리; 파리"), ("Natural Squid - 23 - 07", "natural squid -07"),
]

for bait, expected in CASES:
    check(f"bait_family({bait!r}) == {expected!r}", bait_family(bait) == expected)


# 전 어종 사전계산 스토어 검증 (D-53)
_DB = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'rf4.db')
for f in (_DB, _DB + "-wal", _DB + "-shm"):
    if _os.path.exists(f): _os.remove(f)
sconn = sqlite3.connect(_DB)
sconn.executescript("""
CREATE TABLE catches (id INTEGER PRIMARY KEY, species TEXT, weight_g INT,
  waterbody TEXT, bait TEXT, player TEXT, caught_date TEXT,
  source TEXT DEFAULT 'weekly_record', first_seen TEXT);
CREATE TABLE species_master (species TEXT PRIMARY KEY, trophy_g INT, rare_trophy_g INT, added_at TEXT DEFAULT (datetime('now')));
INSERT INTO species_master (species,trophy_g,rare_trophy_g) VALUES ('검은 잉어',28000,40000);
INSERT INTO species_master (species,trophy_g,rare_trophy_g) VALUES ('잉어',10000,15000);
""")
for i in range(6):
    sconn.execute("INSERT INTO catches (species,weight_g,waterbody,bait,player,caught_date,first_seen) "
                  "VALUES ('검은 잉어',?,'곰 호수','크랜베리',?, '2026-06-12', datetime('now','-30 minute'))",
                  (42000 + i, f'r{i}'))
sconn.commit()

scoring._store = {}  # 테스트 시작 전 스토어 초기화(다른 테스트/모듈 상태 오염 방지)

# 1) refresh_scores 후 스토어가 전 어종 x 전 시간창으로 채워지고, dashboard가 즉석계산과 동일한 값을 반환
n = scoring.refresh_scores(sconn)
check("refresh_scores: 어종 수 반환", n == 2)
check("refresh_scores: 두 시간창 채워짐", set(scoring._store.keys()) == set(scoring.WINDOWS.keys()))
check("refresh_scores: 전 어종 채워짐", set(scoring._store["today"].keys()) == {"검은 잉어", "잉어"})

direct = scoring.score_species(sconn, "검은 잉어", "today")
dash = scoring.dashboard(sconn, ["검은 잉어"], "today")
check("dashboard 결과가 즉석계산과 동일(스토어 조회)", dash[0] == direct)

# 2) 스토어에 센티널 값을 심으면 dashboard가 그 값을 그대로 반환 — 스토어가 실제로 소비됨을 증명
sentinel = dict(scoring._store["today"]["검은 잉어"])
sentinel["score"] = 12345.0
scoring._store["today"]["검은 잉어"] = sentinel
dash_sentinel = scoring.dashboard(sconn, ["검은 잉어"], "today")
check("스토어 센티널 값이 그대로 소비됨", dash_sentinel[0]["score"] == 12345.0)

# 3) 스토어가 비어있으면(기동 직후 등) 즉석계산 폴백
scoring._store = {}
dash_fallback = scoring.dashboard(sconn, ["검은 잉어"], "today")
check("스토어 비어있을 때 즉석계산 폴백", dash_fallback[0] == direct)

# 4) top_active: 스토어가 비어있으면 빈 리스트(즉석계산 폴백 없음)
check("top_active: 스토어 비었을 때 빈 리스트", scoring.top_active("today") == [])

# 5) top_active: 비활성 제외, score 내림차순(동점은 n_total 내림차순)
scoring._store["today"] = {
    "A": {"species": "A", "state": scoring.STATE_ACTIVE, "score": 50.0, "n_total": 10},
    "B": {"species": "B", "state": scoring.STATE_STRONG, "score": 90.0, "n_total": 5},
    "C": {"species": "C", "state": scoring.STATE_INACTIVE, "score": 0.0, "n_total": 2},
    "D": {"species": "D", "state": scoring.STATE_POSSIBLE, "score": 50.0, "n_total": 20},
}
top = scoring.top_active("today", limit=5)
check("top_active: 비활성 제외", all(c["state"] != scoring.STATE_INACTIVE for c in top))
check("top_active: score 내림차순, 동점은 n_total 내림차순",
      [c["species"] for c in top] == ["B", "D", "A"])

# 6) top_active: limit 개수만큼만 자르고, 스토어를 건드리지 않는다(읽기 전용 계약)
scoring._store["today"] = {
    f"S{i}": {"species": f"S{i}", "state": scoring.STATE_ACTIVE, "score": float(i), "n_total": 1}
    for i in range(8)
}
before = {k: dict(v) for k, v in scoring._store["today"].items()}
check("top_active: limit 개수만큼만 반환", len(scoring.top_active("today")) == 5)
check("top_active: 점수 상위 5개(내림차순)",
      [c["species"] for c in scoring.top_active("today")] == ["S7", "S6", "S5", "S4", "S3"])
check("top_active: 스토어를 변경하지 않음", scoring._store["today"] == before)

scoring._store = {}  # 테스트 종료 후 스토어 초기화(오염 방지)
sconn.close()

print("="*40)
print("실패", len(fails), "건" if fails else "— 전체 통과")
sys.exit(1 if fails else 0)
