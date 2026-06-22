# maintenance.py — 데이터 보존 정리
# 운영 rf4.db는 first_seen 7일 이내만 유지(추천은 최대 72h만 쓰므로 충분).
# 7일 초과 catches는 삭제 전 archive.db로 복사 (향후 추천 모델 학습/검증 데이터, D-19).
# users/favorites/labels는 절대 건드리지 않는다.

import sqlite3
from pathlib import Path

RETAIN_DAYS = 7

ARCHIVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS catches (
    id          INTEGER PRIMARY KEY,
    species     TEXT, weight_g INTEGER, waterbody TEXT, bait TEXT,
    player      TEXT, caught_date TEXT, source TEXT, first_seen TEXT
);
CREATE INDEX IF NOT EXISTS idx_arch_species ON catches(species, first_seen);
"""


def archive_and_prune(db_path, archive_path=None):
    """7일 초과 catches를 archive.db로 옮기고 운영 DB에서 삭제.
    반환: (아카이브된 건수, 삭제된 catches 건수)."""
    db_path = Path(db_path)
    if archive_path is None:
        archive_path = db_path.parent / "archive.db"

    conn = sqlite3.connect(db_path, timeout=30)
    try:
        cutoff = f"datetime('now', '-{RETAIN_DAYS} day')"
        old = conn.execute(
            f"SELECT id, species, weight_g, waterbody, bait, player, "
            f"caught_date, source, first_seen FROM catches "
            f"WHERE first_seen < {cutoff}").fetchall()
        if not old:
            return 0, 0

        # 1) 아카이브로 복사
        arch = sqlite3.connect(archive_path, timeout=30)
        try:
            arch.executescript(ARCHIVE_SCHEMA)
            arch.executemany(
                "INSERT OR IGNORE INTO catches "
                "(id, species, weight_g, waterbody, bait, player, "
                "caught_date, source, first_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", old)
            arch.commit()
        finally:
            arch.close()

        # 2) 운영 DB에서 삭제 (catches + orphan appearances)
        old_ids = [r[0] for r in old]
        conn.executemany("DELETE FROM appearances WHERE catch_id = ?",
                         [(i,) for i in old_ids])
        conn.executemany("DELETE FROM catches WHERE id = ?",
                         [(i,) for i in old_ids])
        conn.commit()
        # 공간 회수
        conn.execute("VACUUM")
        return len(old), len(old_ids)
    finally:
        conn.close()
