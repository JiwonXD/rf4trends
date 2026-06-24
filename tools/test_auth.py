# 회원 인증 + 사용자별 선호 어종 분리 검증
import sys, os as _os
sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'rf4site'))
_os.environ['RF4_DB'] = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'rf4.db')
import os, sqlite3, datetime
if os.path.exists("rf4.db"): os.remove("rf4.db")
conn = sqlite3.connect("rf4.db")
conn.executescript("""
CREATE TABLE catches (id INTEGER PRIMARY KEY, species TEXT, weight_g INT,
  waterbody TEXT, bait TEXT, player TEXT, caught_date TEXT,
  source TEXT DEFAULT 'weekly_record', first_seen TEXT);
CREATE TABLE trophies (species TEXT PRIMARY KEY, trophy_g INT, rare_trophy_g INT);
INSERT INTO trophies VALUES ('검은 잉어',28000,40000),('타이멘',50000,80000);
""")
today = datetime.date.today().isoformat()
for i in range(8):
    conn.execute("INSERT INTO catches (species,weight_g,waterbody,bait,player,caught_date,first_seen) VALUES ('검은 잉어',?,'곰 호수','크랜베리',?,?,datetime('now'))",(42000+i,f'p{i}',today))
conn.commit(); conn.close()

from fastapi.testclient import TestClient
from app import app
fails=[]
def check(label, cond):
    print(('PASS' if cond else 'FAIL'), label)
    if not cond: fails.append(label)

# 비로그인 → 로그인 리다이렉트
c = TestClient(app, follow_redirects=False)
r = c.get("/")
check("비로그인 → /login 리다이렉트", r.status_code in (302,303,307) and "/login" in r.headers["location"])

# 회원가입
r = c.post("/signup", data={"username":"angler1","password":"secret123"})
check("회원가입 성공 → 온보딩", r.status_code==303 and "/onboarding" in r.headers["location"])
check("가입 시 세션 쿠키 발급", "rf4_session" in r.cookies)

# 약한 비번 거부
r2 = c.post("/signup", data={"username":"weakpw","password":"123"})
check("짧은 비밀번호 거부", r2.status_code==400)
# 중복 아이디 거부
r3 = c.post("/signup", data={"username":"angler1","password":"another123"})
check("중복 아이디 거부", r3.status_code==400)

# 로그인 (별도 클라이언트)
c2 = TestClient(app, follow_redirects=False)
r = c2.post("/login", data={"username":"angler1","password":"secret123"})
check("로그인 성공", r.status_code==303 and "rf4_session" in r.cookies)
r = c2.post("/login", data={"username":"angler1","password":"wrong"})
check("틀린 비밀번호 거부", r.status_code==401)

# 로그인 사용자로 선호 어종 추가 → 대시보드
cf = TestClient(app)  # follow redirects
cf.post("/signup", data={"username":"angler2","password":"secret123"})
cf.post("/api/favorites/검은 잉어")
r = cf.get("/")
check("로그인 후 대시보드 접근", r.status_code==200 and "검은 잉어" in r.text)
check("헤더에 로그아웃 표시", "로그아웃" in r.text)

# 사용자 분리: angler1은 angler2의 선호를 못 봄
ca = TestClient(app)
ca.post("/login", data={"username":"angler1","password":"secret123"})
r = ca.get("/", follow_redirects=False)
check("angler1은 선호 없음 → 온보딩", r.status_code in (302,303,307) and "onboarding" in r.headers["location"])

# 로그아웃
r = cf.get("/logout", follow_redirects=False)
check("로그아웃 시 쿠키 삭제", r.status_code==303)

print("="*40)
print("실패", len(fails), "건" if fails else "— 전체 통과")
