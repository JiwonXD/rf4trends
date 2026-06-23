# 화면 정의서 v1.0 시나리오 검증
import os, sqlite3, datetime
if os.path.exists("rf4.db"): os.remove("rf4.db")

conn = sqlite3.connect("rf4.db")
conn.executescript("""
CREATE TABLE catches (id INTEGER PRIMARY KEY, species TEXT, weight_g INT,
  waterbody TEXT, bait TEXT, player TEXT, caught_date TEXT,
  source TEXT DEFAULT 'weekly_record', first_seen TEXT);
CREATE TABLE trophies (species TEXT PRIMARY KEY, trophy_g INT, rare_trophy_g INT);
INSERT INTO trophies VALUES ('검은 잉어',28000,40000),('타이멘',50000,80000),
  ('무지개 송어',10000,13000),('붕어',1800,2900);
""")
today = datetime.date.today().isoformat()
yest = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
rows = []
# 강한 활성: 검은 잉어 — 트로피 8건, 같은 미끼 7건
for i in range(8):
    rows.append(('검은 잉어', 42000+i*100, '곰 호수',
                 '크랜베리 팝업 26' if i < 7 else '옥수수', f'p{i}', today))
# 활성: 타이멘 — 트로피 2 + 일반 5, 같은 루어
for i in range(2): rows.append(('타이멘', 55000+i*100, '퉁구스카', 'Squirrel 60', f't{i}', yest))
for i in range(5): rows.append(('타이멘', 38000+i*100, '퉁구스카', 'Squirrel 60', f's{i}', today))
# 불명: 무지개 송어 — 7건 전부 다른 미끼
for i in range(7): rows.append(('무지개 송어', 11000+i*100, '쿠오리', f'미끼{i}', f'r{i}', today))
# 비활성: 붕어 — 기록 2건뿐(표본 부족)
for i in range(2): rows.append(('붕어', 1900+i*50, '모기 호수', '반죽', f'b{i}', today))
conn.executemany("INSERT INTO catches (species,weight_g,waterbody,bait,player,caught_date,first_seen) VALUES (?,?,?,?,?,?,datetime('now'))", rows)
conn.commit(); conn.close()

from fastapi.testclient import TestClient
from app import app
c = TestClient(app)
# 인증 추가됨: 테스트용 계정 생성 후 로그인된 클라이언트 사용
c.post("/signup", data={"username":"tester","password":"secret123"})
fails = []
def check(label, cond):
    print(('PASS' if cond else 'FAIL'), label)
    if not cond: fails.append(label)

r = c.get("/", follow_redirects=False)
check("선호 0개 → 온보딩 리다이렉트", r.status_code in (302,303,307) and "/onboarding" in r.headers["location"])

r = c.get("/onboarding")
check("온보딩에 어종 목록 표시", r.status_code == 200 and "검은 잉어" in r.text and "대시보드 보기" in r.text)

for sp in ["검은 잉어", "타이멘", "무지개 송어", "붕어"]:
    r = c.post(f"/api/favorites/{sp}")
    assert r.status_code == 200

r = c.get("/")
t = r.text
check("대시보드 200", r.status_code == 200)
check("강한 활성 분류", "강한 활성" in t)
check("활성 분류", ">활성<" in t.replace("강한 활성",""))
check("불명 분류", "불명" in t)
check("비활성 분류 + 표본 부족", "표본 부족" in t)
check("정렬: 검은 잉어가 타이멘보다 위", t.index("검은 잉어") < t.index("타이멘"))
check("정렬: 비활성(붕어)이 최하단", t.index("붕어") > t.index("무지개 송어"))
check("대표 미끼 표기", "크랜베리 팝업 26" in t)
check("불명도 미끼 분산 표기", "분산" in t)

r = c.get("/species/검은 잉어")
t = r.text
check("어종 상세 200", r.status_code == 200)
check("트로피 기준선 표기", "28.0 kg" in t and "40.0 kg" in t)
check("미끼 순위 블록", "미끼 순위" in t and "크랜베리 팝업 26" in t)
check("장소/트로피 기록 블록", "장소 분포" in t and "최근 트로피 기록" in t)
check("레어 뱃지", "레어" in t)

r = c.get("/species/검은 잉어?trophy_only=1")
check("트로피만 토글", r.status_code == 200 and "트로피이상만" in r.text)

r = c.get("/species/없는어종", follow_redirects=False)
check("없는 어종 → 대시보드 리다이렉트", r.status_code in (302,307))

r = c.get("/?window=today")
check("오늘 창 동작", r.status_code == 200)

r = c.delete("/api/favorites/붕어")
r = c.get("/")
check("선호 해제 반영", "붕어" not in r.text)

# 미끼 일관성: 카드 consistency == 상세 1등 미끼 비율 (분모 일치, 미끼 15종 초과 시에도)
import scoring as _sc
_conn2 = sqlite3.connect("rf4.db")
_c = _sc.score_species(_conn2, "검은 잉어", "today")
_d = _sc.species_detail(_conn2, "검은 잉어", "today")
_conn2.close()
if _d["baits"]:
    check("카드 일관성 == 상세 1등미끼 비율", _c["consistency"] == _d["baits"][0]["share"])

print("="*40)
print("실패", len(fails), "건" if fails else "— 전체 통과")
