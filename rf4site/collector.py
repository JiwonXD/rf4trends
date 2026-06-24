# rf4game.kr 주간기록 수집기
# 3개 카테고리(통합/UL/텔레스코픽) x 10개 지역 = 30개 게시판을 순회하며 SQLite에 적재합니다.
# (라이트/바텀라이트/해양은 통합과 동일 데이터라 아래 CATEGORIES에서 제외)
# 같은 물고기가 여러 게시판에 떠도 catches에는 1번만 저장되고,
# 게시판 노출 정보는 appearances에 따로 기록됩니다.
#
# 사용법:
#   pip install requests beautifulsoup4
#   python collector.py
#
# 검증: 연속으로 2번 실행했을 때 두 번째 실행의 "새 기록"이 0이어야 정상입니다.

import re
import sqlite3
import time
from datetime import datetime, date, timezone

import requests
from bs4 import BeautifulSoup

BASE = "https://rf4game.kr"
DB_PATH = "rf4.db"
REQUEST_DELAY_SEC = 1.5  # 서버 예의용 딜레이

CATEGORIES = {
    "records": "통합",
    "ultralight": "울트라라이트",
    "telestick": "텔레스코픽",
    # 참고: recordslight(라이트), bottomlight(바텀라이트), sea(해양)는
    # 공식 사이트가 별도 주간 테이블을 제공하지 않고 통합 테이블을 그대로
    # 보여주는 것으로 확인되어 (2026-06, .kr/.com 모두 동일) 수집에서 제외.
}

REGIONS = {
    "GL": "국제",
    "RU": "러시아/CIS",
    "DE": "독일",
    "US": "미국",
    "FR": "프랑스",
    "CN": "중국",
    "PL": "폴란드",
    "KR": "대한민국",
    "JP": "일본",
    "EN": "기타",
}

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS catches (
    id          INTEGER PRIMARY KEY,
    species     TEXT NOT NULL,
    weight_g    INTEGER NOT NULL,
    waterbody   TEXT NOT NULL,
    bait        TEXT,
    player      TEXT NOT NULL,
    caught_date TEXT NOT NULL,   -- ISO YYYY-MM-DD
    source      TEXT NOT NULL DEFAULT 'weekly_record',
    first_seen  TEXT NOT NULL,   -- 수집 시각
    UNIQUE (player, species, weight_g, caught_date)
);

CREATE TABLE IF NOT EXISTS appearances (
    catch_id   INTEGER NOT NULL REFERENCES catches(id),
    category   TEXT NOT NULL,    -- records / ultralight / ...
    region     TEXT NOT NULL,    -- GL / KR / ...
    rank       INTEGER,          -- 게시판 내 순위 (1~5)
    seen_at    TEXT NOT NULL,
    UNIQUE (catch_id, category, region)
);

CREATE INDEX IF NOT EXISTS idx_catches_species ON catches(species, caught_date);
CREATE INDEX IF NOT EXISTS idx_catches_bait ON catches(bait);
"""


def parse_weight(text):
    """'25.815 kg' -> 25815, '265 g' -> 265"""
    text = text.strip().replace(",", "")
    m = re.match(r"([\d.]+)\s*(kg|g)", text)
    if not m:
        return None
    value, unit = float(m.group(1)), m.group(2)
    return int(round(value * 1000)) if unit == "kg" else int(round(value))


def parse_date(text):
    """'9.06.26' (일.월.년) -> '2026-06-09'"""
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{2})", text.strip())
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), 2000 + int(m.group(3))
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def parse_row(row, species):
    """기록 행 하나 -> dict 또는 None"""
    def col_text(cls):
        el = row.select_one(f".col.{cls}")
        return el.get_text(strip=True) if el else ""

    weight_g = parse_weight(col_text("weight"))
    caught_date = parse_date(col_text("data"))
    player = col_text("gamername")
    waterbody = col_text("location")

    bait_el = row.select_one(".col.bait .bait_icon")
    bait = bait_el.get("title", "").strip() if bait_el else None

    if not (weight_g and caught_date and player and waterbody):
        return None
    return {
        "species": species,
        "weight_g": weight_g,
        "waterbody": waterbody,
        "bait": bait or None,
        "player": player,
        "caught_date": caught_date,
    }


def parse_board(html):
    """게시판 페이지 HTML -> 기록 dict 리스트 (rank 포함)"""
    soup = BeautifulSoup(html, "html.parser")
    records = []
    for sub in soup.select(".records_subtable"):
        header = sub.select_one(".row.header")
        if header is None:
            continue
        fish_el = header.select_one(".col.fish")
        species = fish_el.get_text(strip=True) if fish_el else ""
        if not species:
            continue

        rows = [header] + list(sub.select(".rows > div"))
        rank = 0
        for row in rows:
            rec = parse_row(row, species)
            if rec:
                rank += 1
                rec["rank"] = rank
                records.append(rec)
    return records


MAX_CONSECUTIVE_FAILURES = 5  # 연속 실패가 이만큼 쌓이면 사이클 중단


def collect(conn):
    # first_seen은 UTC로 저장한다. scoring.py의 시간창 필터가 SQLite datetime('now')(UTC)를
    # 쓰므로, 저장도 UTC여야 시간대가 일치한다. (로컬시계로 저장하면 KST-UTC 9시간 차로
    # 시간창 필터가 무력화돼 짧은 창과 긴 창이 같아지는 버그가 생긴다. D-29)
    # 포맷은 SQLite datetime()과 동일한 공백 구분자 'YYYY-MM-DD HH:MM:SS'로 통일한다.
    # (T 구분자로 저장하면 문자열 비교 시 'T'(84)>' '(32)라 같은 날짜의 시간 비교가 깨진다.)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.cursor()
    total_new_catches = 0
    total_new_appearances = 0
    consecutive_failures = 0

    for cat in CATEGORIES:
        for region in REGIONS:
            url = f"{BASE}/{cat}/weekly/region/{region}/"
            try:
                resp = requests.get(url, headers=HEADERS, timeout=30)
                # 403/429는 차단 신호 → 이번 사이클 즉시 중단, 다음 주기에 재시도
                if resp.status_code in (403, 429):
                    print(f"[중단] {cat}/{region}: HTTP {resp.status_code} 수신. "
                          f"차단 방지를 위해 이번 사이클을 종료합니다.")
                    return
                resp.raise_for_status()
                consecutive_failures = 0
            except requests.RequestException as e:
                consecutive_failures += 1
                print(f"[실패] {cat}/{region}: {e}")
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    print(f"[중단] 연속 {consecutive_failures}회 실패. "
                          f"서버 문제로 보고 이번 사이클을 종료합니다.")
                    return
                time.sleep(REQUEST_DELAY_SEC)
                continue

            records = parse_board(resp.text)
            new_c, new_a = 0, 0
            for rec in records:
                cur.execute(
                    """INSERT OR IGNORE INTO catches
                       (species, weight_g, waterbody, bait, player, caught_date, first_seen)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (rec["species"], rec["weight_g"], rec["waterbody"],
                     rec["bait"], rec["player"], rec["caught_date"], now),
                )
                new_c += cur.rowcount
                cur.execute(
                    """SELECT id FROM catches
                       WHERE player=? AND species=? AND weight_g=? AND caught_date=?""",
                    (rec["player"], rec["species"], rec["weight_g"], rec["caught_date"]),
                )
                catch_id = cur.fetchone()[0]
                cur.execute(
                    """INSERT OR IGNORE INTO appearances
                       (catch_id, category, region, rank, seen_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (catch_id, cat, region, rec["rank"], now),
                )
                new_a += cur.rowcount

            conn.commit()
            total_new_catches += new_c
            total_new_appearances += new_a
            print(f"[완료] {CATEGORIES[cat]}/{REGIONS[region]}: "
                  f"파싱 {len(records)}건, 새 기록 {new_c}, 새 노출 {new_a}")
            time.sleep(REQUEST_DELAY_SEC)

    print("=" * 50)
    print(f"이번 수집: 새 기록 {total_new_catches}건, 새 노출 {total_new_appearances}건")
    cur.execute("SELECT COUNT(*) FROM catches")
    print(f"누적 기록: {cur.fetchone()[0]}건")


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    collect(conn)
    conn.close()


if __name__ == "__main__":
    main()
