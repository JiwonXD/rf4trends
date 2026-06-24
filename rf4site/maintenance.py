# maintenance.py — 데이터 보존 정리
# 운영 rf4.db는 first_seen 7일 이내만 유지(추천은 최대 24h만 쓰므로 충분).
# 7일 초과 catches는 삭제 전, 미끼 분석용으로 archive.db에 어종·미끼·무게만 남긴다.
# (장소·시각·플레이어는 미끼 분석에 불필요하므로 버려 용량을 절감.)
# 미끼는 원본 그대로 보관 — RF4는 채비에 미끼를 최대 2종까지 달 수 있어
# "꿀 반죽; 옥수수씨"처럼 세미콜론으로 묶여 들어오며, 이 조합 정보를 분석에 쓴다.
# users/favorites/labels는 절대 건드리지 않는다.

import sqlite3
from pathlib import Path

RETAIN_DAYS = 7

# 미끼 분석 전용 보관 테이블. 기록별로 남겨 무게 분포까지 분석 가능.
# 분석 예: 미끼별 평균/분포 무게, 어종별 미끼 사용 빈도, 미끼 조합(2종) 연관성.
ARCHIVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS bait_records (
    species   TEXT,
    bait      TEXT,    -- 원본 그대로 (2종 조합은 '미끼A; 미끼B' 형태)
    weight_g  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_bait_species ON bait_records(species);
CREATE INDEX IF NOT EXISTS idx_bait_bait ON bait_records(bait);
"""


def archive_and_prune(db_path, archive_path=None):
    """7일 초과 catches를 삭제하기 전, 미끼 분석용으로 어종·미끼·무게만 archive.db에 보관.
    반환: (보관된 건수, 삭제된 catches 건수)."""
    db_path = Path(db_path)
    if archive_path is None:
        archive_path = db_path.parent / "archive.db"

    conn = sqlite3.connect(db_path, timeout=30)
    try:
        cutoff = f"datetime('now', '-{RETAIN_DAYS} day')"
        old = conn.execute(
            f"SELECT id, species, bait, weight_g FROM catches "
            f"WHERE first_seen < {cutoff}").fetchall()
        if not old:
            return 0, 0

        # 1) 미끼 분석용으로 어종·미끼·무게만 보관 (미끼 없는 기록은 분석 의미 없어 제외)
        arch = sqlite3.connect(archive_path, timeout=30)
        try:
            arch.executescript(ARCHIVE_SCHEMA)
            arch.executemany(
                "INSERT INTO bait_records (species, bait, weight_g) "
                "VALUES (?, ?, ?)",
                [(r[1], r[2], r[3]) for r in old if r[2]])
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
