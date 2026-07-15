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
import auth
fails=[]
def check(label, cond):
    print(('PASS' if cond else 'FAIL'), label)
    if not cond: fails.append(label)

# 비로그인 → 로그인 리다이렉트 (secure 쿠키(D-52)는 https에서만 전송되므로 base_url을 https로)
c = TestClient(app, follow_redirects=False, base_url="https://testserver")
r = c.get("/")
check("비로그인 → /login 리다이렉트", r.status_code in (302,303,307) and "/login" in r.headers["location"])

# 회원가입
r = c.post("/signup", data={"username":"angler1","password":"secret123","nickname":"angler1_nick"})
check("회원가입 성공 → 온보딩", r.status_code==303 and "/onboarding" in r.headers["location"])
check("가입 시 세션 쿠키 발급", "rf4_session" in r.cookies)

# 약한 비번 거부
r2 = c.post("/signup", data={"username":"weakpw","password":"123","nickname":"weakpw_nick"})
check("짧은 비밀번호 거부", r2.status_code==400)
# 중복 아이디 거부
r3 = c.post("/signup", data={"username":"angler1","password":"another123","nickname":"angler1_dup_nick"})
check("중복 아이디 거부", r3.status_code==400)

# 닉네임 누락/형식/중복 검증 (가입 시 필수, D-52)
r4 = c.post("/signup", data={"username":"nonick1","password":"secret123"})
check("닉네임 누락 시 422", r4.status_code==422)
r5 = c.post("/signup", data={"username":"badnick1","password":"secret123","nickname":"n!ck"})
check("형식 위반 닉네임 400", r5.status_code==400)
r6 = c.post("/signup", data={"username":"dupnick_b","password":"secret123","nickname":"angler1_nick"})
check("중복 닉네임 400", r6.status_code==400)
r7 = c.post("/signup", data={"username":"freshuser1","password":"secret123","nickname":"freshnick1"})
check("가입 성공", r7.status_code==303)
_conn_fresh = sqlite3.connect("rf4.db")
_uid_fresh = _conn_fresh.execute("SELECT id FROM users WHERE username='freshuser1'").fetchone()[0]
check("가입 직후 get_profile에 닉네임 반영", auth.get_profile(_conn_fresh, _uid_fresh)["nickname"]=="freshnick1")
_conn_fresh.close()

# 로그인 (별도 클라이언트)
c2 = TestClient(app, follow_redirects=False, base_url="https://testserver")
r = c2.post("/login", data={"username":"angler1","password":"secret123"})
check("로그인 성공", r.status_code==303 and "rf4_session" in r.cookies)
check("로그인 세션 쿠키에 Secure 속성(D-52)", "secure" in r.headers.get("set-cookie","").lower())
r = c2.post("/login", data={"username":"angler1","password":"wrong"})
check("틀린 비밀번호 거부", r.status_code==401)

# 로그인 사용자로 선호 어종 추가 → 대시보드
cf = TestClient(app, base_url="https://testserver")  # follow redirects
cf.post("/signup", data={"username":"angler2","password":"secret123","nickname":"angler2_nick"})
cf.post("/api/favorites/검은 잉어")
r = cf.get("/")
check("로그인 후 대시보드 접근", r.status_code==200 and "검은 잉어" in r.text)
check("헤더에 로그아웃 표시", "로그아웃" in r.text)

# 사용자 분리: angler1은 angler2의 선호를 못 봄
ca = TestClient(app, base_url="https://testserver")
ca.post("/login", data={"username":"angler1","password":"secret123"})
r = ca.get("/", follow_redirects=False)
check("angler1은 선호 없음 → 온보딩", r.status_code in (302,303,307) and "onboarding" in r.headers["location"])

# 로그아웃 (POST 전용, D-52)
r = cf.get("/logout", follow_redirects=False)
check("GET /logout 405(POST 전용)", r.status_code==405)
r = cf.post("/logout", follow_redirects=False)
check("로그아웃 시 쿠키 삭제", r.status_code==303)

# 세션 버저닝 라우트 레벨: 비번 변경 시 현재 기기는 로그인 유지, 옛 쿠키 쓰는 별도 요청은 풀림 (D-52)
cpw = TestClient(app, base_url="https://testserver", follow_redirects=False)
cpw.post("/signup", data={"username":"pwuser","password":"secret123","nickname":"pwuser_nick"})
old_cookie = cpw.cookies.get("rf4_session")
r = cpw.post("/me/password", data={"current_password":"secret123","new_password":"newpass456"})
check("비번 변경 성공 → /me 리다이렉트", r.status_code==303 and r.headers["location"]=="/me")
new_cookie = r.cookies.get("rf4_session")
check("비번 변경 시 세션 쿠키 재발급(기존과 다름)", new_cookie is not None and new_cookie != old_cookie)
r = cpw.get("/", follow_redirects=False)
check("같은 클라이언트는 재발급 쿠키로 로그인 유지", r.status_code in (302,303,307) and "/login" not in r.headers["location"])

cold = TestClient(app, base_url="https://testserver", follow_redirects=False)
cold.cookies.set("rf4_session", old_cookie)
r = cold.get("/", follow_redirects=False)
check("옛 쿠키를 쓰는 별도 요청은 /login 리다이렉트", r.status_code in (302,303,307) and "/login" in r.headers["location"])

# 아이디/닉네임 검증
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

# 닉네임은 가입 시 필수 등록(D-52), 리더보드 노출 기본 True
conn = sqlite3.connect("rf4.db")
auth.init_db(conn)
uid, err = auth.create_user(conn, "nickuser1", "secret123", "nickuser1_nick")
check("create_user 성공", err is None)
profile = auth.get_profile(conn, uid)
check("get_profile에 가입 시 닉네임 반영", profile["nickname"] == "nickuser1_nick")
check("get_profile leaderboard_visible 기본 True", profile["leaderboard_visible"] is True)

# 닉네임 등록/변경
uid2, _ = auth.create_user(conn, "nickuser2", "secret123", "nickuser2_nick")
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

# 세션 버저닝 단위 테스트: 비번 변경 시 옛 토큰 무효화, 구 포맷 토큰도 무효 (D-52)
class _FakeReq:
    def __init__(self, token):
        self.cookies = {auth.COOKIE_NAME: token} if token else {}

uid3, _ = auth.create_user(conn, "sessver_user", "secret123", "sessver_nick")
old_token = auth.session_token_for(conn, uid3)
check("옛 토큰 발급 직후엔 유효", auth.current_user(conn, _FakeReq(old_token)) == (uid3, "sessver_user"))
ok, err = auth.change_password(conn, uid3, "secret123", "newpass789")
check("세션버전 검증용 비밀번호 변경 성공", ok and err is None)
check("비번 변경 후 옛 토큰으로 current_user → None", auth.current_user(conn, _FakeReq(old_token)) is None)
new_token = auth.session_token_for(conn, uid3)
check("재발급한 새 토큰은 유효", auth.current_user(conn, _FakeReq(new_token)) == (uid3, "sessver_user"))

old_format_token = auth._serializer.dumps({"uid": uid3})
check("구 포맷 토큰({'uid'}만 서명) → current_user None", auth.current_user(conn, _FakeReq(old_format_token)) is None)

conn.close()

# 마이그레이션: nickname/session_version 없는 구 users 테이블에 init_db 적용
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
row = mconn.execute("SELECT nickname, leaderboard_visible, session_version FROM users WHERE username='olduser'").fetchone()
check("마이그레이션: nickname NULL 유지(백필 안 함)", row[0] is None)
check("마이그레이션: leaderboard_visible 기본값 1", row[1] == 1)
check("마이그레이션: session_version 컬럼 자동 추가 + 기본값 0", row[2] == 0)
mconn.close()
os.remove("rf4_migrate.db")

print("="*40)
print("실패", len(fails), "건" if fails else "— 전체 통과")
