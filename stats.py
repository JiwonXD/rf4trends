# rf4.db 통계 분석 — 코랩/주피터에서 사용
# 사용법 (셀에서):
#   from stats import *
#   conn = open_db("rf4.db")          # 드라이브 마운트 시 경로 조정
#   load_trophies(conn)               # 최초 1회: trophy_weights.csv 로딩
#   unmatched_species(conn)           # 기준표와 이름이 안 맞는 어종 점검
#   hot_combos(conn)                  # 지금 활발한 어종x장소x미끼 조합 (트로피수 포함)
#   trophy_combos(conn)               # 트로피급 이상이 나오는 조합만
#   species_baits(conn, "거울 잉어")   # 특정 어종의 인기 미끼
#   waterbody_summary(conn, "엠버 호수")  # 특정 장소에서 뭐가 잡히는지
#   species_list(conn)                # 수집된 어종 목록

import csv
import sqlite3
import pandas as pd


def open_db(path="rf4.db"):
    return sqlite3.connect(path)


def _ensure_trophies(conn):
    """trophies 테이블이 없으면 빈 테이블로 생성 (등급은 전부 '일반' 처리됨)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trophies (
            species       TEXT PRIMARY KEY,
            trophy_g      INTEGER,
            rare_trophy_g INTEGER
        )""")


def load_trophies(conn, csv_path="trophy_weights.csv"):
    """트로피 기준표 CSV(출처: rf4kr.com)를 trophies 테이블에 로딩. 재실행 시 갱신."""
    _ensure_trophies(conn)
    with open(csv_path, encoding="utf-8-sig") as f:
        rows = [(r["species"],
                 int(r["trophy_g"]) if r["trophy_g"] else None,
                 int(r["rare_trophy_g"]) if r["rare_trophy_g"] else None)
                for r in csv.DictReader(f)]
    conn.executemany(
        "INSERT OR REPLACE INTO trophies VALUES (?, ?, ?)", rows)
    conn.commit()
    print(f"트로피 기준 {len(rows)}종 로딩 완료")


def unmatched_species(conn):
    """catches에는 있지만 trophies 기준표에 없는 어종 (이름 불일치 점검용)."""
    _ensure_trophies(conn)
    q = """
    SELECT c.species AS 어종, COUNT(*) AS 기록수
    FROM catches c LEFT JOIN trophies t ON t.species = c.species
    WHERE t.species IS NULL
    GROUP BY c.species ORDER BY 기록수 DESC
    """
    return pd.read_sql(q, conn)


# 등급 분류 SQL 조각 (trophies 조인 전제)
_TIER = """
    CASE
        WHEN t.rare_trophy_g IS NOT NULL AND c.weight_g >= t.rare_trophy_g THEN '레어트로피'
        WHEN t.trophy_g IS NOT NULL AND c.weight_g >= t.trophy_g THEN '트로피'
        ELSE '일반'
    END
"""


def hot_combos(conn, days=7, min_count=3, limit=30):
    """최근 N일간 기록이 많이 올라온 어종x장소x미끼 조합 (트로피급 카운트 포함).
    '지금 잡을만한 물고기' 추천의 기본 형태."""
    _ensure_trophies(conn)
    q = f"""
    SELECT c.species AS 어종, c.waterbody AS 장소, c.bait AS 미끼,
           COUNT(*) AS 기록수,
           SUM(CASE WHEN {_TIER} IN ('트로피','레어트로피') THEN 1 ELSE 0 END) AS 트로피이상,
           ROUND(MAX(c.weight_g)/1000.0, 3) AS 최대kg,
           MAX(c.caught_date) AS 최근날짜
    FROM catches c LEFT JOIN trophies t ON t.species = c.species
    WHERE c.caught_date >= date('now', ?)
      AND c.bait IS NOT NULL
    GROUP BY c.species, c.waterbody, c.bait
    HAVING COUNT(*) >= ?
    ORDER BY 기록수 DESC, 최대kg DESC
    LIMIT ?
    """
    return pd.read_sql(q, conn, params=(f"-{days} day", min_count, limit))


def trophy_combos(conn, days=7, min_count=2, limit=30, rare_only=False):
    """트로피급 이상 기록이 나오고 있는 어종x장소x미끼 조합.
    대물 사냥꾼용 추천: '지금 이 조합으로 트로피가 나온다'."""
    _ensure_trophies(conn)
    tier_cond = ("c.weight_g >= t.rare_trophy_g" if rare_only
                 else "c.weight_g >= t.trophy_g")
    q = f"""
    SELECT c.species AS 어종, c.waterbody AS 장소, c.bait AS 미끼,
           COUNT(*) AS 트로피수,
           SUM(CASE WHEN t.rare_trophy_g IS NOT NULL
                    AND c.weight_g >= t.rare_trophy_g THEN 1 ELSE 0 END) AS 레어수,
           ROUND(MAX(c.weight_g)/1000.0, 3) AS 최대kg,
           MAX(c.caught_date) AS 최근날짜
    FROM catches c JOIN trophies t ON t.species = c.species
    WHERE c.caught_date >= date('now', ?)
      AND c.bait IS NOT NULL
      AND t.trophy_g IS NOT NULL
      AND {tier_cond}
    GROUP BY c.species, c.waterbody, c.bait
    HAVING COUNT(*) >= ?
    ORDER BY 트로피수 DESC, 최대kg DESC
    LIMIT ?
    """
    return pd.read_sql(q, conn, params=(f"-{days} day", min_count, limit))


def species_baits(conn, species, days=7):
    """특정 어종이 최근 어떤 미끼/장소에서 나오는지."""
    q = """
    SELECT bait AS 미끼, waterbody AS 장소,
           COUNT(*) AS 기록수,
           ROUND(MAX(weight_g)/1000.0, 3) AS 최대kg
    FROM catches
    WHERE species = ? AND caught_date >= date('now', ?) AND bait IS NOT NULL
    GROUP BY bait, waterbody
    ORDER BY 기록수 DESC, 최대kg DESC
    """
    return pd.read_sql(q, conn, params=(species, f"-{days} day"))


def waterbody_summary(conn, waterbody, days=7, limit=30):
    """특정 장소에서 최근 잡히는 어종과 미끼."""
    q = """
    SELECT species AS 어종, bait AS 미끼,
           COUNT(*) AS 기록수,
           ROUND(MAX(weight_g)/1000.0, 3) AS 최대kg
    FROM catches
    WHERE waterbody = ? AND caught_date >= date('now', ?) AND bait IS NOT NULL
    GROUP BY species, bait
    ORDER BY 기록수 DESC, 최대kg DESC
    LIMIT ?
    """
    return pd.read_sql(q, conn, params=(waterbody, f"-{days} day", limit))


def species_list(conn):
    """수집된 어종 목록 (기록 수 순)."""
    q = """
    SELECT species AS 어종, COUNT(*) AS 기록수,
           COUNT(DISTINCT waterbody) AS 장소수
    FROM catches GROUP BY species ORDER BY 기록수 DESC
    """
    return pd.read_sql(q, conn)


def tackle_filter_example(conn, category="ultralight", days=7, limit=20):
    """appearances 조인 예시: UL 게시판에 뜬 기록만으로 집계."""
    q = """
    SELECT c.species AS 어종, c.waterbody AS 장소, c.bait AS 미끼,
           COUNT(DISTINCT c.id) AS 기록수,
           ROUND(MAX(c.weight_g)/1000.0, 3) AS 최대kg
    FROM catches c
    JOIN appearances a ON a.catch_id = c.id
    WHERE a.category = ? AND c.caught_date >= date('now', ?)
      AND c.bait IS NOT NULL
    GROUP BY c.species, c.waterbody, c.bait
    ORDER BY 기록수 DESC
    LIMIT ?
    """
    return pd.read_sql(q, conn, params=(category, f"-{days} day", limit))


if __name__ == "__main__":
    import os
    conn = open_db()
    pd.set_option("display.max_rows", 50)
    pd.set_option("display.width", 150)

    if os.path.exists("trophy_weights.csv"):
        load_trophies(conn)
        um = unmatched_species(conn)
        if not um.empty:
            print("=== 트로피 기준표에 없는 어종 (이름 불일치 점검 필요) ===")
            print(um.to_string(index=False))
    else:
        print("※ trophy_weights.csv가 없어 트로피 등급 미분류로 동작합니다")

    print("\n=== 최근 7일 활발한 조합 TOP 30 ===")
    print(hot_combos(conn).to_string(index=False))
    print("\n=== 최근 7일 트로피가 나오는 조합 TOP 30 ===")
    print(trophy_combos(conn).to_string(index=False))
    print("\n=== 어종 TOP 20 ===")
    print(species_list(conn).head(20).to_string(index=False))
