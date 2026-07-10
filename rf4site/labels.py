# labels.py — 라벨 수집 (향후 추천 모델 학습/검증 데이터)
# 라벨을 찍는 순간의 활성도 지표 스냅샷을 함께 저장한다.
# 이렇게 해두면 7일 뒤 원본 catches가 정리돼도 (입력 지표 → 라벨) 학습쌍이 남는다.
# 같은 어종을 여러 번 라벨하면 매번 새 행으로 쌓는다 (시간에 따른 판정 변화도 데이터, D-18).

VALID_LABELS = {"강한 활성", "활성", "탐색", "비활성"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS labels (
    id            INTEGER PRIMARY KEY,
    user_id       INTEGER NOT NULL,
    species       TEXT NOT NULL,
    label         TEXT NOT NULL,
    window        TEXT NOT NULL,
    -- 라벨 시점의 활성도 지표 스냅샷 (학습 피처)
    n_rare        INTEGER,
    n_trophy      INTEGER,
    n_normal      INTEGER,
    n_total       INTEGER,
    consistency   INTEGER,
    family_consistency INTEGER,
    top_bait      TEXT,
    top_waterbody TEXT,
    score         REAL,
    -- 무게 비율 통계 (전체 기록 모집단, 학습 피처)
    trophy_ratio_max  REAL,
    trophy_ratio_min  REAL,
    trophy_ratio_avg  REAL,
    rare_ratio_max    REAL,
    rare_ratio_min    REAL,
    rare_ratio_avg    REAL,
    hours_since_reset REAL,
    source        TEXT,    -- 'admin' 또는 'user' (라벨 작성자 권한, 사후 정제용 D-32)
    labeled_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_labels_user ON labels(user_id, species);
"""


def init_db(conn):
    conn.executescript(SCHEMA)
    # 기존 테이블에 source 컬럼이 없으면 추가 (CREATE TABLE IF NOT EXISTS는 컬럼을 안 더함)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(labels)").fetchall()]
    if "source" not in cols:
        conn.execute("ALTER TABLE labels ADD COLUMN source TEXT")
    if "family_consistency" not in cols:
        conn.execute("ALTER TABLE labels ADD COLUMN family_consistency INTEGER")
    conn.commit()


def add_label(conn, user_id, species, label, card, source="user"):
    """라벨 + 활성도 스냅샷 저장. card는 scoring.score_species_at()(수역 단위, D-40)
    반환 dict에 scoring.ratio_stats() 결과와 window·hours_since_reset가 병합된 것.
    source: 'admin' 또는 'user' (작성자 권한, 사후 정제용).
    반환: (True, None) 또는 (False, 오류메시지)."""
    if label not in VALID_LABELS:
        return False, "알 수 없는 라벨입니다."
    conn.execute("""
        INSERT INTO labels (user_id, species, label, window,
            n_rare, n_trophy, n_normal, n_total, consistency, family_consistency,
            top_bait, top_waterbody, score,
            trophy_ratio_max, trophy_ratio_min, trophy_ratio_avg,
            rare_ratio_max, rare_ratio_min, rare_ratio_avg,
            hours_since_reset, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, species, label, card.get("window", ""),
          card["n_rare"], card["n_trophy"], card["n_normal"], card["n_total"],
          card["consistency"], card["family_consistency"],
          card["top_bait"], card["top_waterbody"],
          card["score"],
          card.get("trophy_ratio_max"), card.get("trophy_ratio_min"),
          card.get("trophy_ratio_avg"), card.get("rare_ratio_max"),
          card.get("rare_ratio_min"), card.get("rare_ratio_avg"),
          card.get("hours_since_reset"), source))
    conn.commit()
    return True, None


def weekly_ranking(conn, since_utc, admin_username):
    """이번 주(since_utc 이후) 제보 랭킹 — 순위 계산의 단일 소스. 닉네임을 등록했고
    (개인정보 보호 — 미등록 유저는 아이디가 노출될 수 있어 제외) leaderboard_visible=1이며
    admin이 아닌 유저만 대상.
    정렬: 제보수 DESC → 그 주 마지막 제보 시각(MAX(labeled_at)) ASC(같은 수면 먼저 도달한 쪽이 위)
    → nickname ASC(최종 결정성).
    반환: [{"user_id","nickname","count","last_at"}, ...] (순서 = 순위, 인덱스+1이 곧 rank)."""
    rows = conn.execute("""
        SELECT l.user_id, u.nickname, COUNT(*) AS cnt, MAX(l.labeled_at) AS last_at
        FROM labels l
        JOIN users u ON u.id = l.user_id
        WHERE l.labeled_at >= ? AND u.leaderboard_visible = 1 AND u.username <> ?
          AND u.nickname IS NOT NULL AND u.nickname <> ''
        GROUP BY l.user_id
        ORDER BY cnt DESC, last_at ASC, u.nickname ASC
    """, (since_utc, admin_username)).fetchall()
    return [{"user_id": r[0], "nickname": r[1], "count": r[2], "last_at": r[3]} for r in rows]


def top_reported_species(conn, since_utc, admin_username, limit=5):
    """이번 주(since_utc 이후) 어종별 제보 횟수 상위 N. admin 라벨 제외(골든셋 대량 제보로
    순위가 왜곡되는 것 방지) — 유저별 리더보드가 아니라 어종 집계이므로 비공개 유저는
    제외하지 않는다. 반환: [{"species","count"}, ...] count DESC, 동점은 species ASC."""
    rows = conn.execute("""
        SELECT l.species, COUNT(*) cnt
        FROM labels l
        JOIN users u ON u.id = l.user_id
        WHERE l.labeled_at >= ? AND u.username <> ?
        GROUP BY l.species
        ORDER BY cnt DESC, l.species ASC
        LIMIT ?
    """, (since_utc, admin_username, limit)).fetchall()
    return [{"species": r[0], "count": r[1]} for r in rows]


def my_activity(conn, user_id, since_utc):
    """해당 유저의 since_utc 이후 제보 횟수 (리더보드 자격과 무관하게 항상 계산)."""
    row = conn.execute(
        "SELECT COUNT(*) FROM labels WHERE user_id = ? AND labeled_at >= ?",
        (user_id, since_utc)).fetchone()
    return row[0] if row else 0


def my_rank(conn, user_id, since_utc, admin_username):
    """본인의 이번 주 순위. weekly_ranking과 100% 일치하도록 그 리스트에서 인덱스를 찾는다
    (순위 계산 로직 중복 금지). 리스트에 없으면(자격 미달: 닉네임 미등록/비공개/admin) rank=None.
    반환: {"count": n, "rank": int|None} 또는 count가 0이면 None."""
    count = my_activity(conn, user_id, since_utc)
    if count == 0:
        return None
    ranking = weekly_ranking(conn, since_utc, admin_username)
    idx = next((i for i, r in enumerate(ranking) if r["user_id"] == user_id), None)
    return {"count": count, "rank": idx + 1 if idx is not None else None}


def export_csv(conn, path):
    """수집된 라벨 전체를 CSV로 내보낸다 (학습 데이터 추출용).
    앱에서 자동 호출하지 않는 수동 유틸 — 모델 학습 시 직접 호출해 데이터셋을 뽑는다."""
    import csv
    cols = ["species", "label", "window", "n_rare", "n_trophy", "n_normal",
            "n_total", "consistency", "family_consistency", "top_bait", "top_waterbody", "score",
            "trophy_ratio_max", "trophy_ratio_min", "trophy_ratio_avg",
            "rare_ratio_max", "rare_ratio_min", "rare_ratio_avg",
            "hours_since_reset", "source", "labeled_at"]
    rows = conn.execute(
        f"SELECT {', '.join(cols)} FROM labels ORDER BY labeled_at").fetchall()
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)
    return len(rows)
