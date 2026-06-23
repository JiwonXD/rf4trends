# app.py — RF4 선호 어종 대시보드 (단일 통합 서버)
# 실행: python app.py   →  수집 스레드 + 웹서버가 한 프로세스에서 동작
# 같은 와이파이의 다른 기기에서 http://<태블릿IP>:8000 으로 접속

import csv
import sqlite3
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "rf4.db"
TROPHY_CSV = BASE_DIR / "trophy_weights.csv"
COLLECT_INTERVAL_MIN = 15

import collector
import scoring
import auth
import labels as labels_mod
import maintenance


def _load_trophies(conn):
    """트로피 기준표가 비어있으면 CSV에서 로딩 (최초 1회 자동)."""
    conn.execute("""CREATE TABLE IF NOT EXISTS trophies (
        species TEXT PRIMARY KEY, trophy_g INTEGER, rare_trophy_g INTEGER)""")
    has = conn.execute("SELECT 1 FROM trophies LIMIT 1").fetchone()
    if has or not TROPHY_CSV.exists():
        return
    with open(TROPHY_CSV, encoding="utf-8-sig") as f:
        rows = [(r["species"],
                 int(r["trophy_g"]) if r["trophy_g"] else None,
                 int(r["rare_trophy_g"]) if r["rare_trophy_g"] else None)
                for r in csv.DictReader(f)]
    conn.executemany("INSERT OR REPLACE INTO trophies VALUES (?, ?, ?)", rows)
    conn.commit()
    print(f"[초기화] 트로피 기준 {len(rows)}종 로딩")


def _collect_loop(stop_event):
    """백그라운드 수집 루프 — 15분 주기. 서버와 같은 프로세스에서 상시 구동.
    하루에 한 번(자정 이후 첫 사이클) 7일 초과 데이터를 아카이브하고 정리한다."""
    last_prune_day = None
    while not stop_event.is_set():
        conn = sqlite3.connect(DB_PATH, timeout=30)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(collector.SCHEMA)
            print(f"\n===== 수집 시작 {datetime.now():%m-%d %H:%M:%S} =====")
            collector.collect(conn)
        except Exception as e:
            print(f"[수집 실패] {e}")
        finally:
            conn.close()

        # 하루 1회 아카이브 + 정리 (UTC 날짜가 바뀐 첫 사이클에만).
        # maintenance가 UTC(datetime('now')) 기준으로 정리하므로 판단도 UTC로 통일.
        today = datetime.now(timezone.utc).date()
        if last_prune_day != today:
            try:
                archived, pruned = maintenance.archive_and_prune(DB_PATH)
                if pruned:
                    print(f"[정리] 7일 초과 {pruned}건 아카이브 후 삭제")
                last_prune_day = today
            except Exception as e:
                print(f"[정리 실패] {e}")

        # 15분 대기하되, 종료 신호가 오면 즉시 빠져나옴
        stop_event.wait(COLLECT_INTERVAL_MIN * 60)


@asynccontextmanager
async def lifespan(app):
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.executescript(collector.SCHEMA)
    _load_trophies(conn)
    conn.close()

    stop_event = threading.Event()
    t = threading.Thread(target=_collect_loop, args=(stop_event,), daemon=True)
    t.start()
    print(f"[수집기] 백그라운드 시작 — {COLLECT_INTERVAL_MIN}분 주기")
    yield
    stop_event.set()


app = FastAPI(title="RF4 선호 어종 대시보드", lifespan=lifespan)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    auth.init_db(conn)
    labels_mod.init_db(conn)
    return conn


def get_favorites(conn, user_id):
    return [r[0] for r in conn.execute(
        "SELECT species FROM favorites WHERE user_id = ? ORDER BY species",
        (user_id,))]


def last_collected(conn):
    row = conn.execute("SELECT MAX(first_seen) FROM catches").fetchone()
    if not (row and row[0]):
        return None
    # first_seen은 UTC 저장 → 헤더엔 KST로 표시
    return scoring._to_kst_str(row[0])


def norm_window(w):
    return w if w in scoring.WINDOWS else "today"


def require_login(conn, request):
    """로그인 사용자 (uid, username) 반환, 없으면 None."""
    return auth.current_user(conn, request)


@app.get("/")
def dashboard(request: Request, window: str = "today"):
    window = norm_window(window)
    conn = db()
    try:
        user = require_login(conn, request)
        if not user:
            return RedirectResponse("/login")
        uid, username = user
        favorites = get_favorites(conn, uid)
        if not favorites:
            return RedirectResponse(f"/onboarding?window={window}")
        has_data = conn.execute("SELECT 1 FROM catches LIMIT 1").fetchone()
        cards = scoring.dashboard(conn, favorites, window) if has_data else []
        return templates.TemplateResponse(request, "dashboard.html", {
            "cards": cards,
            "window": window,
            "has_data": bool(has_data),
            "last_collected": last_collected(conn),
            "username": username,
        })
    finally:
        conn.close()


@app.get("/onboarding")
def onboarding(request: Request, window: str = "today"):
    conn = db()
    try:
        user = require_login(conn, request)
        if not user:
            return RedirectResponse("/login")
        uid, username = user
        favorites = set(get_favorites(conn, uid))
        species = [r[0] for r in conn.execute(
            "SELECT DISTINCT species FROM catches ORDER BY species")]
        return templates.TemplateResponse(request, "onboarding.html", {
            "species": species,
            "favorites": favorites,
            "window": norm_window(window),
            "first_visit": len(favorites) == 0,
            "username": username,
        })
    finally:
        conn.close()


@app.get("/species/{name}")
def species_page(request: Request, name: str, window: str = "today",
                 trophy_only: int = 0):
    window = norm_window(window)
    conn = db()
    try:
        user = require_login(conn, request)
        if not user:
            return RedirectResponse("/login")
        uid, username = user
        exists = conn.execute(
            "SELECT 1 FROM catches WHERE species = ? LIMIT 1", (name,)).fetchone()
        if not exists:
            return RedirectResponse(f"/?window={window}")
        detail = scoring.species_detail(conn, name, window, bool(trophy_only))
        is_favorite = conn.execute(
            "SELECT 1 FROM favorites WHERE user_id = ? AND species = ?",
            (uid, name)).fetchone()
        return templates.TemplateResponse(request, "species.html", {
            "d": detail,
            "name": name,
            "window": window,
            "trophy_only": bool(trophy_only),
            "is_favorite": bool(is_favorite),
            "last_collected": last_collected(conn),
            "username": username,
            # 라벨 버튼 노출 여부. 현재는 모든 로그인 유저 허용.
            # admin 전용으로 좁히려면 아래에서 'or not auth.is_admin(username)'만 지우면 됨.
            "can_label": auth.is_admin(username) or not auth.is_admin(username),
        })
    finally:
        conn.close()


@app.post("/api/favorites/{name}")
def add_favorite(request: Request, name: str):
    conn = db()
    try:
        user = require_login(conn, request)
        if not user:
            return JSONResponse({"ok": False, "error": "로그인 필요"}, status_code=401)
        conn.execute("INSERT OR IGNORE INTO favorites (user_id, species) VALUES (?, ?)",
                     (user[0], name))
        conn.commit()
        return JSONResponse({"ok": True, "favorite": True})
    finally:
        conn.close()


@app.delete("/api/favorites/{name}")
def remove_favorite(request: Request, name: str):
    conn = db()
    try:
        user = require_login(conn, request)
        if not user:
            return JSONResponse({"ok": False, "error": "로그인 필요"}, status_code=401)
        conn.execute("DELETE FROM favorites WHERE user_id = ? AND species = ?",
                     (user[0], name))
        conn.commit()
        return JSONResponse({"ok": True, "favorite": False})
    finally:
        conn.close()


@app.get("/login")
def login_page(request: Request, error: str = "", mode: str = "login"):
    return templates.TemplateResponse(request, "login.html", {
        "error": error, "mode": mode,
    })


@app.post("/login")
def login_submit(request: Request,
                 username: str = Form(...), password: str = Form(...)):
    conn = db()
    try:
        uid = auth.verify_user(conn, username.strip(), password)
        if not uid:
            return templates.TemplateResponse(request, "login.html", {
                "error": "아이디 또는 비밀번호가 올바르지 않습니다.",
                "mode": "login", "username": username,
            }, status_code=401)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(
            auth.COOKIE_NAME, auth.make_session_token(uid),
            max_age=auth.SESSION_MAX_AGE, httponly=True, samesite="lax")
        return resp
    finally:
        conn.close()


@app.post("/signup")
def signup_submit(request: Request,
                  username: str = Form(...), password: str = Form(...)):
    conn = db()
    try:
        uid, err = auth.create_user(conn, username.strip(), password)
        if err:
            return templates.TemplateResponse(request, "login.html", {
                "error": err, "mode": "signup", "username": username,
            }, status_code=400)
        resp = RedirectResponse("/onboarding", status_code=303)
        resp.set_cookie(
            auth.COOKIE_NAME, auth.make_session_token(uid),
            max_age=auth.SESSION_MAX_AGE, httponly=True, samesite="lax")
        return resp
    finally:
        conn.close()


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE_NAME)
    return resp


@app.post("/api/label/{name}")
def add_label(request: Request, name: str,
              label: str = Form(...), window: str = Form("today")):
    window = norm_window(window)
    conn = db()
    try:
        user = require_login(conn, request)
        if not user:
            return JSONResponse({"ok": False, "error": "로그인 필요"}, status_code=401)
        # 라벨 권한 게이트. 현재는 모든 로그인 유저 허용.
        # admin 여부는 source로 박제해 사후 정제 대비(D-32) — admin 라벨이 golden set.
        # 다시 admin 전용으로 좁히려면 아래 조건에서 'or not is_admin'만 지우면 됨.
        is_admin = auth.is_admin(user[1])
        source = "admin" if is_admin else "user"
        if not (is_admin or not is_admin):
            return JSONResponse({"ok": False, "error": "권한 없음"}, status_code=403)
        # 라벨 찍는 시점의 활성도 스냅샷 + 무게 비율 + 리셋 경과시간을 박제
        card = scoring.score_species(conn, name, window)
        card["window"] = window
        card.update(scoring.ratio_stats(conn, name, window))
        card["hours_since_reset"] = scoring.hours_since_reset()
        ok, err = labels_mod.add_label(conn, user[0], name, label, card, source)
        if not ok:
            return JSONResponse({"ok": False, "error": err}, status_code=400)
        return JSONResponse({"ok": True, "label": label})
    finally:
        conn.close()


if __name__ == "__main__":
    # host="0.0.0.0" : 같은 와이파이의 다른 기기에서 접속 가능하게
    print("=" * 50)
    print("  RF4 트렌드 시작")
    print("  이 기기에서:   http://localhost:8000")
    print("  다른 기기에서: http://<이 태블릿의 IP>:8000")
    print("  (태블릿 IP 확인: Termux에서  ifconfig  또는 와이파이 설정)")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=8000)
