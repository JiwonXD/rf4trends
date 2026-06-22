# auth.py — 회원 인증 (아이디/비밀번호, 세션 쿠키)
# 비밀번호는 bcrypt 해시로만 저장한다 (평문 저장 금지, D-15).

import os
import re

import bcrypt
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

# 세션 서명 키: 운영 시 환경변수로 덮어쓸 것. 바뀌면 기존 로그인 전부 풀림.
SECRET_KEY = os.environ.get("RF4_SECRET", "rf4-local-dev-key-change-me")
SESSION_MAX_AGE = 60 * 60 * 24 * 30   # 30일
COOKIE_NAME = "rf4_session"

# admin 계정명: 이 계정만 라벨 수집 기능을 쓸 수 있다 (학습 데이터 정답 보호).
ADMIN_USERNAME = os.environ.get("RF4_ADMIN", "admin")


def is_admin(username):
    return username == ADMIN_USERNAME

_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="rf4-session")

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")


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
    conn.commit()


def validate_username(username):
    """반환: 오류 메시지 또는 None(통과)."""
    if not USERNAME_RE.match(username):
        return "아이디는 영문/숫자/밑줄 3~20자여야 합니다."
    return None


def validate_password(password):
    if len(password) < 6:
        return "비밀번호는 6자 이상이어야 합니다."
    return None


def create_user(conn, username, password):
    """반환: (user_id, None) 또는 (None, 오류메시지)."""
    err = validate_username(username) or validate_password(password)
    if err:
        return None, err
    exists = conn.execute(
        "SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
    if exists:
        return None, "이미 사용 중인 아이디입니다."
    pw_hash = _hash_pw(password)
    cur = conn.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
        (username, pw_hash))
    conn.commit()
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


def make_session_token(user_id):
    return _serializer.dumps({"uid": user_id})


def read_session_token(token):
    """쿠키 토큰 → user_id 또는 None."""
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
        return data.get("uid")
    except (BadSignature, SignatureExpired):
        return None


def current_user(conn, request):
    """요청의 세션 쿠키에서 (user_id, username) 또는 None."""
    token = request.cookies.get(COOKIE_NAME)
    uid = read_session_token(token)
    if uid is None:
        return None
    row = conn.execute("SELECT id, username FROM users WHERE id = ?", (uid,)).fetchone()
    return (row[0], row[1]) if row else None
