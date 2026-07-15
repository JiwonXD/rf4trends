# auth.py — 회원 인증 (아이디/비밀번호, 세션 쿠키)
# 비밀번호는 bcrypt 해시로만 저장한다 (평문 저장 금지, D-15).

import os
import re
import sqlite3

import bcrypt
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

# 세션 서명 키: 운영 시 환경변수로 덮어쓸 것. 바뀌면 기존 로그인 전부 풀림.
SECRET_KEY = os.environ.get("RF4_SECRET", "rf4-local-dev-key-change-me")
if "RF4_SECRET" not in os.environ:
    print("[경고] RF4_SECRET 미설정 — 공개된 기본 키로 세션을 서명합니다. 운영에선 반드시 설정할 것.")
SESSION_MAX_AGE = 60 * 60 * 24 * 30   # 30일
COOKIE_NAME = "rf4_session"
# 로컬 http 브라우저 확인이 필요하면 임시로 False — 운영(HTTPS 터널)은 항상 True (스캐폴딩, D-52)
COOKIE_SECURE = True

# admin 계정명: 이 계정만 라벨 수집 기능을 쓸 수 있다 (학습 데이터 정답 보호).
ADMIN_USERNAME = os.environ.get("RF4_ADMIN", "admin")


def is_admin(username):
    return username == ADMIN_USERNAME

_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="rf4-session")

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{3,20}$")
NICKNAME_RE = re.compile(r"^[A-Za-z0-9가-힣_.@-]{3,20}$")


def _hash_pw(password):
    # bcrypt는 72바이트 초과를 거부하므로 안전하게 자른다 (6자 이상 정책이라 실질 영향 없음)
    pw = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")


def _verify_pw(password, pw_hash):
    pw = password.encode("utf-8")[:72]
    try:
        return bcrypt.checkpw(pw, pw_hash.encode("utf-8"))
    except ValueError:
        return False


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    nickname      TEXT,
    leaderboard_visible INTEGER NOT NULL DEFAULT 1,
    session_version INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS favorites (
    user_id  INTEGER NOT NULL REFERENCES users(id),
    species  TEXT NOT NULL,
    PRIMARY KEY (user_id, species)
);
"""


def init_db(conn):
    conn.executescript(SCHEMA)
    # 기존 테이블에 nickname/leaderboard_visible 컬럼이 없으면 추가 (CREATE TABLE IF NOT EXISTS는 컬럼을 안 더함)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "nickname" not in cols:
        # 개인정보 보호: username으로 백필하지 않는다 — 기존 유저는 nickname NULL(리더보드 미노출),
        # 직접 등록해야 노출된다. SQLite UNIQUE INDEX는 NULL 여러 개를 위반으로 안 봄.
        conn.execute("ALTER TABLE users ADD COLUMN nickname TEXT")
    if "leaderboard_visible" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN leaderboard_visible INTEGER NOT NULL DEFAULT 1")
    if "session_version" not in cols:
        # 비밀번호 변경 시 이 값을 올려 기존 발급 세션 토큰을 전부 무효화한다(D-52). 기존 유저는 0부터.
        conn.execute("ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_nickname ON users(nickname)")
    conn.commit()


def validate_username(username):
    """반환: 오류 메시지 또는 None(통과)."""
    if not USERNAME_RE.match(username):
        return "아이디는 영문/숫자와 _ - @ . 3~20자여야 합니다."
    return None


def validate_nickname(nickname):
    """반환: 오류 메시지 또는 None(통과)."""
    if not NICKNAME_RE.match(nickname):
        return "닉네임은 한글/영문/숫자와 _ - @ . 3~20자여야 합니다."
    return None


def validate_password(password):
    if len(password) < 6:
        return "비밀번호는 6자 이상이어야 합니다."
    return None


def create_user(conn, username, password, nickname):
    """반환: (user_id, None) 또는 (None, 오류메시지).
    닉네임은 가입 시 필수(D-52) — 기존에 닉네임 없이 가입한 유저(가입 당시 이 필드 미도입)는
    예외로 남아 nickname NULL 상태를 유지한다(개인정보 보호, 백필 안 함)."""
    err = validate_username(username) or validate_password(password) or validate_nickname(nickname)
    if err:
        return None, err
    exists = conn.execute(
        "SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
    if exists:
        return None, "이미 사용 중인 아이디입니다."
    dup_nick = conn.execute(
        "SELECT 1 FROM users WHERE nickname = ?", (nickname,)).fetchone()
    if dup_nick:
        return None, "이미 사용 중인 닉네임입니다."
    pw_hash = _hash_pw(password)
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, nickname) VALUES (?, ?, ?)",
            (username, pw_hash, nickname))
        conn.commit()
    except sqlite3.IntegrityError:
        # 검사(사전 SELECT)와 저장(INSERT) 사이의 경쟁 상태 대비 — 유니크 인덱스가 최종 방어선
        return None, "이미 사용 중인 아이디 또는 닉네임입니다."
    return cur.lastrowid, None


def verify_user(conn, username, password):
    """반환: user_id 또는 None."""
    row = conn.execute(
        "SELECT id, password_hash FROM users WHERE username = ?",
        (username,)).fetchone()
    if not row:
        return None
    if not _verify_pw(password, row[1]):
        return None
    return row[0]


def make_session_token(user_id, version):
    return _serializer.dumps({"uid": user_id, "v": version})


def session_token_for(conn, user_id):
    """현재 session_version을 조회해 토큰을 발급한다.
    로그인·가입·비밀번호 변경 후 재발급 시 이걸 쓴다(항상 DB의 최신 버전으로 서명)."""
    row = conn.execute(
        "SELECT session_version FROM users WHERE id = ?", (user_id,)).fetchone()
    version = row[0] if row else 0
    return make_session_token(user_id, version)


def read_session_token(token):
    """쿠키 토큰 → (user_id, session_version) 또는 None.
    'v' 필드가 없는 구 포맷 토큰(세션 버저닝 도입 전 발급, D-52)은 자동 무효 처리한다."""
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    uid = data.get("uid")
    if uid is None or "v" not in data:
        return None
    return uid, data["v"]


def current_user(conn, request):
    """요청의 세션 쿠키에서 (user_id, username) 또는 None.
    쿠키의 세션 버전이 DB의 session_version과 다르면(비밀번호 변경 등으로 무효화된
    다른 기기 세션) None을 반환한다(D-52)."""
    token = request.cookies.get(COOKIE_NAME)
    parsed = read_session_token(token)
    if parsed is None:
        return None
    uid, version = parsed
    row = conn.execute(
        "SELECT id, username, session_version FROM users WHERE id = ?", (uid,)).fetchone()
    if not row or row[2] != version:
        return None
    return (row[0], row[1])


def change_nickname(conn, user_id, nickname):
    """반환: (True, None) 또는 (False, 오류메시지)."""
    err = validate_nickname(nickname)
    if err:
        return False, err
    dup = conn.execute(
        "SELECT 1 FROM users WHERE nickname = ? AND id <> ?", (nickname, user_id)).fetchone()
    if dup:
        return False, "이미 사용 중인 닉네임입니다."
    try:
        conn.execute("UPDATE users SET nickname = ? WHERE id = ?", (nickname, user_id))
        conn.commit()
    except sqlite3.IntegrityError:
        # 검사(dup SELECT)와 저장(UPDATE) 사이의 경쟁 상태 대비 — 유니크 인덱스가 최종 방어선
        return False, "이미 사용 중인 닉네임입니다."
    return True, None


def change_password(conn, user_id, current_pw, new_pw):
    """반환: (True, None) 또는 (False, 오류메시지).
    성공 시 session_version을 올려 다른 기기의 기존 세션을 모두 무효화한다(D-52)."""
    row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row or not _verify_pw(current_pw, row[0]):
        return False, "현재 비밀번호가 올바르지 않습니다."
    err = validate_password(new_pw)
    if err:
        return False, err
    conn.execute(
        "UPDATE users SET password_hash = ?, session_version = session_version + 1 WHERE id = ?",
        (_hash_pw(new_pw), user_id))
    conn.commit()
    return True, None


def set_leaderboard_visible(conn, user_id, visible):
    conn.execute("UPDATE users SET leaderboard_visible = ? WHERE id = ?",
                 (1 if visible else 0, user_id))
    conn.commit()


def get_profile(conn, user_id):
    """마이페이지 렌더용: {"username", "nickname", "leaderboard_visible"} 또는 None."""
    row = conn.execute(
        "SELECT username, nickname, leaderboard_visible FROM users WHERE id = ?",
        (user_id,)).fetchone()
    if not row:
        return None
    return {"username": row[0], "nickname": row[1], "leaderboard_visible": bool(row[2])}
