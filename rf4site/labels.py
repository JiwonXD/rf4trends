# labels.py — 라벨 수집 (향후 추천 모델 학습/검증 데이터)
# 라벨을 찍는 순간의 활성도 지표 스냅샷을 함께 저장한다.
# 이렇게 해두면 7일 뒤 원본 catches가 정리돼도 (입력 지표 → 라벨) 학습쌍이 남는다.
# 같은 어종을 여러 번 라벨하면 매번 새 행으로 쌓는다 (시간에 따른 판정 변화도 데이터, D-18).

VALID_LABELS = {"강한 활성", "활성", "가능성", "비활성"}

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
    conn.commit()


def add_label(conn, user_id, species, label, card, source="user"):
    """라벨 + 활성도 스냅샷 저장. card는 scoring.score_species() 반환 dict에
    scoring.ratio_stats() 결과와 window가 병합된 것.
    source: 'admin' 또는 'user' (작성자 권한, 사후 정제용).
    반환: (True, None) 또는 (False, 오류메시지)."""
    if label not in VALID_LABELS:
        return False, "알 수 없는 라벨입니다."
    conn.execute("""
        INSERT INTO labels (user_id, species, label, window,
            n_rare, n_trophy, n_normal, n_total, consistency,
            top_bait, top_waterbody, score,
            trophy_ratio_max, trophy_ratio_min, trophy_ratio_avg,
            rare_ratio_max, rare_ratio_min, rare_ratio_avg,
            hours_since_reset, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, species, label, card.get("window", ""),
          card["n_rare"], card["n_trophy"], card["n_normal"], card["n_total"],
          card["consistency"], card["top_bait"], card["top_waterbody"],
          card["score"],
          card.get("trophy_ratio_max"), card.get("trophy_ratio_min"),
          card.get("trophy_ratio_avg"), card.get("rare_ratio_max"),
          card.get("rare_ratio_min"), card.get("rare_ratio_avg"),
          card.get("hours_since_reset"), source))
    conn.commit()
    return True, None


def export_csv(conn, path):
    """수집된 라벨 전체를 CSV로 내보낸다 (학습 데이터 추출용).
    앱에서 자동 호출하지 않는 수동 유틸 — 모델 학습 시 직접 호출해 데이터셋을 뽑는다."""
    import csv
    cols = ["species", "label", "window", "n_rare", "n_trophy", "n_normal",
            "n_total", "consistency", "top_bait", "top_waterbody", "score",
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
