# 회원 인증 + 사용자별 선호 어종 분리 검증
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
INSERT INTO species_master (species,trophy_g,rare_trophy_g) VALUES ('검은 잉어',28000,40000),('타이멘',50000,80000);
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

# 아이디/닉네임 검증
import auth
check("validate_username 통과(특수문자 허용)", auth.validate_username("a_b-c.d@e") is None)
check("validate_username 거부(짧음)", auth.validate_username("ab") is not None)
check("validate_username 거부(공백)", auth.validate_username("ab c") is not None)
check("validate_username 거부(느낌표)", auth.validate_username("abc!") is not None)
check("validate_username 거부(한글)", auth.validate_username("한글아이디") is not None)
check("validate_nickname 통과(한글)", auth.validate_nickname("한글닉") is None)
check("validate_nickname 통과(한글2)", auth.validate_nickname("낚시왕") is None)
check("validate_nickname 거부(짧음)", auth.validate_nickname("ab") is not None)
check("validate_nickname 거부(공백)", auth.validate_nickname("닉 네임") is not None)
check("validate_nickname 거부(느낌표)", auth.validate_nickname("n!ck") is not None)

# 닉네임은 NULL로 시작(개인정보 보호, username 백필 안 함), 리더보드 노출 기본 True
conn = sqlite3.connect("rf4.db")
auth.init_db(conn)
uid, err = auth.create_user(conn, "nickuser1", "secret123")
check("create_user 성공", err is None)
profile = auth.get_profile(conn, uid)
check("get_profile nickname은 None(백필 안 됨)", profile["nickname"] is None)
check("get_profile leaderboard_visible 기본 True", profile["leaderboard_visible"] is True)

# 닉네임 등록/변경
uid2, _ = auth.create_user(conn, "nickuser2", "secret123")
ok, err = auth.change_nickname(conn, uid, "먼저닉")
check("uid 닉네임 최초 등록 성공", ok and err is None)
ok, err = auth.change_nickname(conn, uid2, "먼저닉")
check("닉네임 충돌 시 실패", not ok and err is not None)
ok, err = auth.change_nickname(conn, uid2, "새닉네임")
check("정상 닉네임 변경 성공", ok and err is None)
ok, err = auth.change_nickname(conn, uid2, "나!쁜닉")
check("잘못된 문자 닉네임 거부", not ok)

# 비밀번호 변경
ok, err = auth.change_password(conn, uid2, "wrongpw", "newpass123")
check("틀린 현재 비밀번호 거부", not ok)
ok, err = auth.change_password(conn, uid2, "secret123", "123")
check("짧은 새 비밀번호 거부", not ok)
ok, err = auth.change_password(conn, uid2, "secret123", "newpass123")
check("비밀번호 변경 성공", ok and err is None)
check("변경된 비밀번호로 로그인 가능", auth.verify_user(conn, "nickuser2", "newpass123") == uid2)
conn.close()

# 마이그레이션: nickname 없는 구 users 테이블에 init_db 적용
if os.path.exists("rf4_migrate.db"): os.remove("rf4_migrate.db")
mconn = sqlite3.connect("rf4_migrate.db")
mconn.executescript("""
CREATE TABLE users (
    id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO users (username, password_hash) VALUES ('olduser', 'hash');
""")
mconn.commit()
auth.init_db(mconn)
row = mconn.execute("SELECT nickname, leaderboard_visible FROM users WHERE username='olduser'").fetchone()
check("마이그레이션: nickname NULL 유지(백필 안 함)", row[0] is None)
check("마이그레이션: leaderboard_visible 기본값 1", row[1] == 1)
mconn.close()
os.remove("rf4_migrate.db")

print("="*40)
print("실패", len(fails), "건" if fails else "— 전체 통과")
