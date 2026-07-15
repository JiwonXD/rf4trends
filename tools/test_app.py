# 화면 시나리오 검증 — 온보딩/대시보드/어종 상세 (시간창·교차필터·모델·어종 마스터 포함)
import sys, os as _os
sys.stdout.reconfigure(encoding="utf-8")  # 한국 Windows 콘솔(cp949)에서 em-dash 출력 크래시 방지
sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'rf4site'))
_os.environ['RF4_DB'] = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'rf4.db')
import os, sqlite3, datetime
if os.path.exists("rf4.db"): os.remove("rf4.db")

conn = sqlite3.connect("rf4.db")
conn.executescript("""
CREATE TABLE catches (id INTEGER PRIMARY KEY, species TEXT, weight_g INT,
  waterbody TEXT, bait TEXT, player TEXT, caught_date TEXT,
  source TEXT DEFAULT 'weekly_record', first_seen TEXT);
CREATE TABLE species_master (species TEXT PRIMARY KEY, trophy_g INT, rare_trophy_g INT, added_at TEXT DEFAULT (datetime('now')));
INSERT INTO species_master (species,trophy_g,rare_trophy_g) VALUES ('검은 잉어',28000,40000),('타이멘',50000,80000),
  ('무지개 송어',10000,13000),('붕어',1800,2900);
CREATE TABLE species_waterbodies (species TEXT NOT NULL, waterbody TEXT NOT NULL, UNIQUE (species, waterbody));
INSERT INTO species_waterbodies VALUES ('검은 잉어','곰 호수'),('검은 잉어','기록없는 호수');
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
# 탐색(구 불명): 무지개 송어 — 7건 전부 다른 미끼
for i in range(7): rows.append(('무지개 송어', 11000+i*100, '쿠오리', f'미끼{i}', f'r{i}', today))
# 비활성: 붕어 — 기록 2건뿐(표본 부족)
for i in range(2): rows.append(('붕어', 1900+i*50, '모기 호수', '반죽', f'b{i}', today))
conn.executemany("INSERT INTO catches (species,weight_g,waterbody,bait,player,caught_date,first_seen) VALUES (?,?,?,?,?,?,datetime('now'))", rows)
conn.commit(); conn.close()

# 활성도 분류는 RandomForest 모델(D-43)이 맡는다 — 학습된 가중치는 임의 합성
# 데이터에 대해 결정적이지 않으므로, 파이프라인(피처 조립→상태/점수 변환) 자체를
# 검증하기 위해 model.predict_proba를 어종별 고정 확률로 스텁한다.
# (모델 자체의 정확도는 tools/train_model.py의 교차검증으로 별도 확인.)
import model as _model_mod
_real_predict_proba = _model_mod.predict_proba   # 아래 실제 모델 적재 점검용으로 보관
_FAKE_PROBS = {
    '검은 잉어': [0, 0, 0, 1],     # 강한 활성
    '타이멘': [0, 0, 1, 0],        # 활성
    '무지개 송어': [0, 1, 0, 0],   # 탐색(구 불명)
}
_model_mod.predict_proba = lambda features: _FAKE_PROBS.get(
    features.get('species'), [0.25, 0.25, 0.25, 0.25])

from fastapi.testclient import TestClient
from app import app
c = TestClient(app, base_url="https://testserver")  # secure 쿠키(D-52)는 https에서만 전송됨
# 인증 추가됨: 테스트용 계정 생성 후 로그인된 클라이언트 사용
c.post("/signup", data={"username":"tester","password":"secret123","nickname":"tester0"})
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
check("탐색 분류", "탐색" in t)
check("비활성 분류 + 표본 부족", "표본 부족" in t)
check("정렬: 검은 잉어가 타이멘보다 위", t.index("검은 잉어") < t.index("타이멘"))
check("정렬: 비활성(붕어)이 최하단", t.index("붕어") > t.index("무지개 송어"))
check("대표 미끼 표기", "크랜베리 팝업 26" in t)
check("탐색도 미끼 분산 표기", "분산" in t)

r = c.get("/species/검은 잉어")
t = r.text
check("어종 상세 200", r.status_code == 200)
check("트로피 기준선 표기", "28.0 kg" in t and "40.0 kg" in t)
check("미끼/장소/트로피 블록 제목", "미끼 순위" in t and "장소 분포" in t and "최근 트로피 기록" in t)
# 교차 필터링: 서버가 원본 records와 수역별 점수를 JSON으로 넘긴다(집계는 JS)
check("RECORDS 데이터 전달", "const RECORDS = [" in t and '"tier"' in t and '"waterbody"' in t)
check("WATER_SCORES 전달", "const WATER_SCORES = {" in t)
# 서식 수역 맵 전달 — 기록 0건 수역('기록없는 호수')도 장소 분포에 표시돼야 함
check("HABITAT_WBS 전달(0건 수역 포함)", "const HABITAT_WBS = [" in t and "기록없는 호수" in t)
check("트로피 토글 버튼", 'id="trophy-toggle"' in t)

# 마스터 어종은 기록 없어도 온보딩에 표시 + 상세 200 (D-46)
_conn_ghost = sqlite3.connect("rf4.db")
_conn_ghost.execute("INSERT OR IGNORE INTO species_master (species,trophy_g,rare_trophy_g) VALUES ('유령어',500,800)")
_conn_ghost.commit(); _conn_ghost.close()
r = c.get("/onboarding")
check("마스터 어종은 기록 없어도 온보딩에 표시", "유령어" in r.text)
r = c.get("/species/유령어", follow_redirects=False)
check("기록 없는 마스터 어종 상세 200", r.status_code == 200)

r = c.get("/species/없는어종", follow_redirects=False)
check("없는 어종 → 대시보드 리다이렉트", r.status_code in (302,307))

r = c.get("/?window=today")
check("오늘 창 동작", r.status_code == 200)

r = c.delete("/api/favorites/붕어")
r = c.get("/")
check("선호 해제 반영", "붕어" not in r.text)

# 미끼 일관성: 카드 consistency == 상세 1등 미끼 비율 (분모 일치, 미끼 15종 초과 시에도)
# D-29 분모 회귀 검증 — JS renderBaits와 같은 계산
import scoring as _sc
_conn2 = sqlite3.connect("rf4.db")
_c = _sc.score_species(_conn2, "검은 잉어", "today")
_d = _sc.species_detail(_conn2, "검은 잉어", "today")
_conn2.close()
_wb = [r for r in _d["records"] if r["bait"]]
_counts = {}
for _r in _wb: _counts[_r["bait"]] = _counts.get(_r["bait"], 0) + 1
if _counts:
    _top_share = round(max(_counts.values()) * 100 / len(_wb))
    check("카드 일관성 == 상세 records 1등미끼 비율", _c["consistency"] == _top_share)

# 시간창 필터: first_seen 공백구분자 포맷(collector 저장 포맷)으로 정확히 거름
# (6h/24h 탭이 다른 데이터를 보여주는지 — 탭 전환 무반응 버그 회귀방지)
import datetime as _dt
_conn3 = sqlite3.connect("rf4.db")
_conn3.execute("DELETE FROM catches WHERE species='타임테스트'")
_conn3.execute("INSERT OR IGNORE INTO species_master (species,trophy_g,rare_trophy_g) VALUES ('타임테스트',5000,9000)")
_now = _dt.datetime.now(_dt.timezone.utc)
_recent = (_now - _dt.timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S')   # 6h 안
_old = (_now - _dt.timedelta(hours=12)).strftime('%Y-%m-%d %H:%M:%S')     # 6h 밖
for _i in range(3):
    _conn3.execute("INSERT INTO catches(species,weight_g,waterbody,bait,player,first_seen) VALUES('타임테스트',1500,'곰 호수','미끼A',?,?)", (f'tt_r{_i}', _recent))
for _i in range(5):
    _conn3.execute("INSERT INTO catches(species,weight_g,waterbody,bait,player,first_seen) VALUES('타임테스트',1500,'곰 호수','미끼A',?,?)", (f'tt_o{_i}', _old))
_conn3.commit()
_t6 = _sc.score_species(_conn3, "타임테스트", "6h")
_t24 = _sc.score_species(_conn3, "타임테스트", "today")
_conn3.close()
check("시간창 6h 필터 정확(3건)", _t6["n_total"] == 3)
check("시간창 24h 필터 정확(전체 8건)", _t24["n_total"] == 8)
check("6h ≠ 24h (탭 전환 시 데이터 바뀜)", _t6["n_total"] != _t24["n_total"])

# 실제 모델 아티팩트(rf4site/model_data.json) 적재·추론 점검 — 위 테스트는 전부
# 스텁이라 실제 모델 파일이 깨져 있어도 못 잡는다. 임의 피처로 한 번 직접 호출.
_real_features = {
    "n_rare": 1, "n_trophy": 3, "n_normal": 4, "n_total": 8, "consistency": 70,
    "trophy_ratio_max": 1.4, "trophy_ratio_min": 0.6, "trophy_ratio_avg": 0.9,
    "rare_ratio_max": None, "rare_ratio_min": None, "rare_ratio_avg": None,
    "hours_since_reset": 50.0, "species": "검은 잉어", "window": "today",
    "top_waterbody": "곰 호수",
}
_probs = _real_predict_proba(_real_features)
check("실제 모델: 확률 4개 반환", len(_probs) == 4)
check("실제 모델: 확률 합 1", abs(sum(_probs) - 1.0) < 1e-6)
check("실제 모델: expected_value 0~100 범위", 0 <= _model_mod.expected_value(_probs) <= 100)

# 마이페이지 + 리더보드 aside 통합 검증 (end-to-end)
r = c.get("/me")
check("GET /me 200 + 닉네임 폼 렌더", r.status_code == 200 and "닉네임" in r.text and "tester" in r.text)

r = c.post("/me/nickname", data={"nickname": "tester_nick"}, follow_redirects=False)
check("POST /me/nickname 리다이렉트", r.status_code == 303 and r.headers["location"] == "/me")
check("POST /me/nickname 성공 플래시 쿠키 발급", "rf4_flash" in r.cookies)
import auth as _auth_mod
_conn4 = sqlite3.connect("rf4.db")
_uid_tester = _conn4.execute("SELECT id FROM users WHERE username='tester'").fetchone()[0]
_profile = _auth_mod.get_profile(_conn4, _uid_tester)
check("닉네임 실제 변경 확인(get_profile)", _profile["nickname"] == "tester_nick")
_conn4.close()
r = c.get("/me")
check("GET /me에 변경된 닉네임 반영", "tester_nick" in r.text)
check("플래시 메시지 1회 표시(D-52)", "닉네임을 변경했습니다" in r.text)
r = c.get("/me")
check("플래시 메시지는 1회성 — 다음 GET엔 사라짐", "닉네임을 변경했습니다" not in r.text)

r = c.post("/me/visibility", data={"visible": "0"}, follow_redirects=False)
check("POST /me/visibility(끄기) 리다이렉트", r.status_code == 303)
_conn5 = sqlite3.connect("rf4.db")
check("visibility 끄기 반영", _auth_mod.get_profile(_conn5, _uid_tester)["leaderboard_visible"] is False)
_conn5.close()
r = c.post("/me/visibility", data={"visible": "1"}, follow_redirects=False)
_conn5 = sqlite3.connect("rf4.db")
check("visibility 켜기 반영", _auth_mod.get_profile(_conn5, _uid_tester)["leaderboard_visible"] is True)
_conn5.close()

# 닉네임 등록한 유저가 라벨 제보 후 대시보드 aside 리더보드가 오류 없이 렌더되는지
r = c.post("/api/label/검은 잉어", data={"label": "강한 활성", "window": "today", "waterbody": "곰 호수"})
check("리더보드용 라벨 제보 성공", r.status_code == 200)
r = c.get("/")
check("대시보드(aside 포함) 정상 렌더", r.status_code == 200)
check("aside 리더보드에 닉네임 표시(block aside 캡처 동작 확인)", "tester_nick" in r.text and "이번 주 리더보드" in r.text)

# 즐겨찾기 TOP / 이번 주 최다 제보 패널 렌더 확인 (현재 favorites: 검은 잉어, 타이멘, 무지개 송어)
check("즐겨찾기 TOP 어종 패널 렌더", "즐겨찾기 TOP 어종" in r.text and "검은 잉어" in r.text.split("즐겨찾기 TOP 어종")[1])
check("이번 주 최다 제보 어종 패널 렌더", "이번 주 최다 제보 어종" in r.text)

print("="*40)
print("실패", len(fails), "건" if fails else "— 전체 통과")
