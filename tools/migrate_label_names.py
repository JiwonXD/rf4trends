# migrate_label_names.py — 라벨 단계명 마이그레이션 (D-42, D-44)
# 옛 이름('불명', '가능성')으로 저장된 라벨을 현재 이름('탐색')으로 일괄 변경.
# 한글 타이핑 없이 실행만 하면 되도록 — Termux 한글 입력 문제 우회용.
#
# 실행: python3 migrate_label_names.py [DB경로]   (기본: ./rf4.db)

import sqlite3
import sys

db = sys.argv[1] if len(sys.argv) > 1 else "rf4.db"
conn = sqlite3.connect(db)
cur = conn.execute(
    "UPDATE labels SET label = ? WHERE label IN (?, ?)",
    ("탐색", "불명", "가능성"))
conn.commit()
print(f"변경된 라벨: {cur.rowcount}건 ('불명'/'가능성' → '탐색')")
conn.close()
