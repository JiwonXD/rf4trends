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
check("온보딩엔 어종 관리 버튼 없음(헤더 컨텍스트 전환)", "/onboarding?window=" not in r.text)
_hdr = r.text.split("</header>")[0]
check("온보딩 헤더 안에 대시보드 보기(id=go) 렌더", 'id="go"' in _hdr and "대시보드 보기" in _hdr)
check("선호 0종이면 헤더 대시보드 보기 비활성", "opacity:.4; pointer-events:none;" in _hdr)
check("하단 고정 바 제거 + 카운트는 검색창 아래", "position:fixed" not in r.text
      and r.text.index('id="q"') < r.text.index('id="count"'))
from urllib.parse import quote as _quote
check("온보딩 목록: 어종명이 상세 링크로 렌더", f"/species/{_quote('검은 잉어')}?window=" in r.text)

for sp in ["검은 잉어", "타이멘", "무지개 송어", "붕어"]:
    r = c.post(f"/api/favorites/{sp}")
    assert r.status_code == 200

r = c.get("/onboarding")
_hdr = r.text.split("</header>")[0]
check("선호 등록 후 헤더 대시보드 보기 활성", 'id="go"' in _hdr and "opacity:.4" not in _hdr)

r = c.get("/")
t = r.text
check("대시보드 200", r.status_code == 200)
check("대시보드엔 어종 관리 버튼 표시(헤더 컨텍스트 전환)", "어종 관리" in t)
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
check("어종 상세엔 어종 관리 버튼 표시(헤더 컨텍스트 전환)", "어종 관리" in t)
check("즐겨찾기인 어종은 ★ 즐겨찾기 버튼", "★ 즐겨찾기" in t)
check("트로피 기준선 표기", "28.0 kg" in t and "40.0 kg" in t)
check("미끼/장소/트로피 블록 제목", "미끼 순위" in t and "장소 분포" in t and "최근 트로피 기록" in t)
check("제보 유도 안내 문구 렌더", "주간 기록을 보고 느껴지는 활성도를 제보해주시면 판정 향상에 도움이 됩니다" in t)
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
check("즐겨찾기 아닌 어종은 ☆ 즐겨찾기 버튼", "☆ 즐겨찾기" in r.text)

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
# 안내 문구의 "마이페이지"는 <a> 태그로 감싸여 있어 원문 그대로는 텍스트가 연속되지 않는다 —
# 링크 뒤에 이어지는 부분(고유 문자열)으로 존재 여부를 확인한다.
check("닉네임 있는 사용자에겐 리더보드 안내 문구 미표시", "에서 닉네임을 설정하면 리더보드에 등록됩니다" not in r.text)

# 즐겨찾기 TOP / 이번 주 최다 제보 패널 렌더 확인 (현재 favorites: 검은 잉어, 타이멘, 무지개 송어)
check("즐겨찾기 TOP 어종 패널 렌더", "즐겨찾기 TOP 어종" in r.text and "검은 잉어" in r.text.split("즐겨찾기 TOP 어종")[1])
check("이번 주 최다 제보 어종 패널 렌더", "이번 주 최다 제보 어종" in r.text)

# 지금 뜨는 어종 패널 렌더 확인 (D-54) — 스토어에 직접 카드를 심어야 나온다(폴백 없음)
# 즐겨찾기(검은 잉어 등)와 겹치지 않는 이름을 써서, 이 스텁이 cards(즐겨찾기 카드) 렌더에 섞이지 않게 함
_sc._store.setdefault("today", {})["급상승어"] = {
    "species": "급상승어", "state": _sc.STATE_STRONG, "score": 88.0, "n_total": 8}
r = c.get("/")
from urllib.parse import quote
_panel = r.text.split("지금 뜨는 어종")[1] if "지금 뜨는 어종" in r.text else ""
check("지금 뜨는 어종 패널 렌더", "지금 뜨는 어종" in r.text and "급상승어" in _panel)
check("지금 뜨는 어종 패널: 활성도 점수·상세 링크 렌더",
      "88.0" in _panel and f"/species/{quote('급상승어')}?window=today" in _panel)
_sc._store = {}  # 이후 검증은 스토어 없이(폴백 경로로) 렌더된다 — 스토어 오염 방지

# 닉네임 미설정 사용자 대시보드 안내 — 양방향 검증(위 tester_nick 검증과 짝)
r = c.post("/signup", data={"username":"nonick_user","password":"secret123","nickname":"nonick_temp"})
assert r.status_code in (200, 302, 303, 307), f"가입 실패: {r.status_code}"
_conn6 = sqlite3.connect("rf4.db")
_conn6.execute("UPDATE users SET nickname=NULL WHERE username='nonick_user'")
_conn6.commit(); _conn6.close()
r = c.post("/api/favorites/검은 잉어")
assert r.status_code == 200, f"즐겨찾기 추가 실패: {r.status_code}"
r = c.get("/")
check("닉네임 없는 사용자에겐 리더보드 안내 문구 표시", "에서 닉네임을 설정하면 리더보드에 등록됩니다" in r.text)

# 언어 선택은 표시만 바꾸며 저장 데이터와 필터 키는 한국어 원본을 유지한다.
import i18n as _i18n
import copy as _copy
r = c.get("/login")
check("기본 한국어 + 로그인 언어 메뉴", '<html lang="ko">' in r.text and 'action="/language"' in r.text)
for _lang in ("en", "ru", "zh-CN"):
    r = c.post("/language", data={"language": _lang, "next": "/species/검은 잉어?window=6h"}, follow_redirects=False)
    check(f"{_lang} 선택 경로·시간창 유지", r.status_code == 303 and r.headers["location"].endswith("?window=6h"))
    check(f"{_lang} 쿠키 보안·기간", "Secure" in r.headers["set-cookie"] and "HttpOnly" in r.headers["set-cookie"] and "Max-Age=31536000" in r.headers["set-cookie"])
    for _path in ("/", "/onboarding", "/species/검은 잉어", "/me", "/login"):
        r = c.get(_path)
        check(f"{_lang} {_path} 번역 렌더", r.status_code == 200 and f'<html lang="{_lang}">' in r.text and _i18n.translate("RF4 트렌드", _lang) in r.text)
    r = c.get("/onboarding")
    check(f"{_lang} 원문·번역 검색 키", f'data-search="검은 잉어 {_i18n.translate_name("species", "검은 잉어", _lang)}"' in r.text)
    r = c.get("/species/검은 잉어")
    check(f"{_lang} 상세 API 원문 어종 키", 'encodeURIComponent("검은 잉어")' in r.text and 'data-label="강한 활성"' in r.text)
    check(f"{_lang} 상세 번역 이름 전달", _i18n.translate_name("waterbodies", "곰 호수", _lang) in r.text)
    r = c.post("/api/favorites/타이멘")
    r = c.post("/api/label/검은 잉어", data={"label": "활성", "window": "today", "waterbody": "곰 호수"})
    check(f"{_lang} 원문 라벨 저장", r.status_code == 200 and r.json()["label"] == "활성")
_conn_lang = sqlite3.connect("rf4.db")
check("번역명이 favorites에 저장되지 않음", _conn_lang.execute("SELECT COUNT(*) FROM favorites WHERE species NOT IN (SELECT species FROM species_master)").fetchone()[0] == 0)
check("번역명이 labels에 저장되지 않음", _conn_lang.execute("SELECT COUNT(*) FROM labels WHERE species NOT IN (SELECT species FROM species_master) OR label NOT IN ('강한 활성','활성','탐색','비활성')").fetchone()[0] == 0)
_conn_lang.close()
r = c.post('/signup', data={'username':'x','password':'short','nickname':'x'})
check("가입 오류 후 언어 전환 GET 대상 유지", 'value="/login?mode=signup"' in r.text)
for _next in ("https://example.com", "//example.com", "/\\example.com", "/%2fexample.com", "/%5cexample.com", "/%0aexample.com"):
    r = c.post("/language", data={"language": "en", "next": _next}, follow_redirects=False)
    check(f"외부 리다이렉트 차단 {_next!r}", r.headers["location"] == "/")
r = c.post("/language", data={"language": "fr"}, follow_redirects=False)
check("미지원 언어 거부", r.status_code == 400 and not r.cookies)
_anon = TestClient(app, base_url="https://testserver")
_anon.cookies.set(_i18n.COOKIE_NAME, "invalid")
check("잘못된 언어 쿠키 한국어 폴백", '<html lang="ko">' in _anon.get('/login').text)
_anon.post("/language", data={"language": "en"})
check("API 오류 번역", _anon.post('/api/favorites/붕어').json()['error'] == _i18n.translate('로그인 필요', 'en'))
check("로그인 오류 번역", _i18n.translate('아이디 또는 비밀번호가 올바르지 않습니다.', 'en') in _anon.post('/login', data={'username':'absent','password':'wrong'}).text)
_conn_cache = sqlite3.connect("rf4.db")
_sc._store.setdefault("today", {})["검은 잉어"] = _sc.score_species(_conn_cache, "검은 잉어", "today")
_conn_cache.close()
_cache_before = _copy.deepcopy(_sc._store)
c.get('/')
check("번역 렌더가 scoring 캐시를 변경하지 않음", _sc._store == _cache_before)
_sc._store = {}
check("알 수 없는 미끼 색상·크기 보존", _i18n.translate_name('baits', 'Unknown 25g-004', 'en') == 'Unknown 25g-004')
check("크기·미끼 조합 번역", _i18n.translate_name('baits', '옥수수; 옥수수 20', 'en') == _i18n.translate_name('baits', '옥수수', 'en') + '; ' + _i18n.translate_name('baits', '옥수수', 'en') + ' 20')

check("달콤한 옥수수와 아미노 구분", _i18n.translate_name('baits', '달콤한 옥수수', 'en') == 'Sweet Corn' and _i18n.translate_name('baits', '아미노', 'en') == 'Amino')
check("야생 자두 러시아어 크기 유지", _i18n.translate_name('baits', '야생 자두 15', 'ru') == 'Дикая слива 15')
print("="*40)
print("실패", len(fails), "건" if fails else "— 전체 통과")
sys.exit(1 if fails else 0)
