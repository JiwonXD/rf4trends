# 라벨링 + 아카이브 검증
import sys, os as _os
sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'rf4site'))
_os.environ['RF4_DB'] = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'rf4.db')
import os, sqlite3
for f in ("rf4.db","rf4.db-wal","rf4.db-shm","archive.db"):
    if os.path.exists(f): os.remove(f)
conn = sqlite3.connect("rf4.db")
conn.executescript("""
CREATE TABLE catches (id INTEGER PRIMARY KEY, species TEXT, weight_g INT,
  waterbody TEXT, bait TEXT, player TEXT, caught_date TEXT,
  source TEXT DEFAULT 'weekly_record', first_seen TEXT);
CREATE TABLE appearances (catch_id INT, category TEXT, region TEXT, rank INT, seen_at TEXT,
  UNIQUE(catch_id, category, region));
CREATE TABLE trophies (species TEXT PRIMARY KEY, trophy_g INT, rare_trophy_g INT);
INSERT INTO trophies VALUES ('검은 잉어',28000,40000);
""")
# 최근 수집 6건 + 10일 전 수집 5건(아카이브 대상)
for i in range(6):
    conn.execute("INSERT INTO catches (species,weight_g,waterbody,bait,player,caught_date,first_seen) VALUES ('검은 잉어',?,'곰 호수','크랜베리',?, '2026-06-12', datetime('now','-30 minute'))",(42000+i,f'r{i}'))
    conn.execute("INSERT INTO appearances VALUES (?,?,?,?,?)",(conn.execute("SELECT last_insert_rowid()").fetchone()[0],'records','GL',i+1,'x'))
for i in range(5):
    conn.execute("INSERT INTO catches (species,weight_g,waterbody,bait,player,caught_date,first_seen) VALUES ('검은 잉어',?,'곰 호수','크랜베리',?, '2026-06-02', datetime('now','-10 day'))",(43000+i,f'o{i}'))
    conn.execute("INSERT INTO appearances VALUES (?,?,?,?,?)",(conn.execute("SELECT last_insert_rowid()").fetchone()[0],'records','GL',i+1,'x'))
conn.commit(); conn.close()

from fastapi.testclient import TestClient
from app import app
fails=[]
def check(label, cond):
    print(('PASS' if cond else 'FAIL'), label)
    if not cond: fails.append(label)

c = TestClient(app)
c.post("/signup", data={"username":"admin","password":"secret123"})

# 라벨 저장
r = c.post("/api/label/검은 잉어", data={"label":"강한 활성","window":"today"})
check("라벨 저장 성공", r.status_code==200)
# 같은 어종 다시 라벨 (다른 값) → 새 행으로 쌓임
r = c.post("/api/label/검은 잉어", data={"label":"활성","window":"today"})
check("같은 어종 재라벨 성공", r.status_code==200)
# 잘못된 라벨 거부
r = c.post("/api/label/검은 잉어", data={"label":"이상한값","window":"today"})
check("잘못된 라벨 거부", r.status_code==400)

# DB 확인: 라벨 2건 쌓였는지 + 스냅샷 저장됐는지
conn = sqlite3.connect("rf4.db")
rows = conn.execute("SELECT label, n_total, consistency, top_bait FROM labels ORDER BY id").fetchall()
check("라벨 2건 누적", len(rows)==2)
check("스냅샷 박제됨(n_total, 미끼)", rows[0][1] is not None and rows[0][3]=='크랜베리')
# ratio 통계도 저장됐는지
rrow = conn.execute("SELECT trophy_ratio_max, trophy_ratio_avg FROM labels LIMIT 1").fetchone()
check("ratio 통계 박제됨", rrow[0] is not None and rrow[1] is not None)
conn.close()

# 어종 상세에 라벨 버튼 렌더링 확인
r = c.get("/species/검은 잉어")
check("상세에 라벨 버튼 표시", "label-btn" in r.text and "강한 활성" in r.text)

# 아카이브+정리 실행
import maintenance
archived, pruned = maintenance.archive_and_prune("rf4.db")
check("10일전 5건 아카이브", archived==5)
check("10일전 5건 운영DB서 삭제", pruned==5)

conn = sqlite3.connect("rf4.db")
remaining = conn.execute("SELECT COUNT(*) FROM catches").fetchone()[0]
orphan = conn.execute("SELECT COUNT(*) FROM appearances").fetchone()[0]
conn.close()
check("운영DB에 최근 6건만 남음", remaining==6)
check("orphan appearances도 정리됨", orphan==6)

arch = sqlite3.connect("archive.db")
acount = arch.execute("SELECT COUNT(*) FROM bait_records").fetchone()[0]
# 어종·미끼·무게만 보관되는지 확인
sample = arch.execute("SELECT species, bait, weight_g FROM bait_records LIMIT 1").fetchone()
arch.close()
check("archive.db(bait_records)에 5건 보존", acount==5)
check("어종·미끼·무게 보관됨", sample is not None and sample[0]=="검은 잉어" and sample[1]=="크랜베리" and sample[2] is not None)

# 라벨은 정리 후에도 남아있는지 (절대 안 건드려야)
conn = sqlite3.connect("rf4.db")
lcount = conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0]
conn.close()
check("정리 후에도 라벨 보존", lcount==2)

# 라벨 권한 개방: 일반 유저도 버튼 보이고 저장됨, source로 구분 박제
usr = TestClient(app)
usr.post("/signup", data={"username":"angler_x","password":"secret123"})
usr.post("/api/favorites/검은 잉어")
r = usr.get("/species/검은 잉어")
check("일반유저 라벨 버튼 보임(제한 풀림)", "label-btn" in r.text)
r = usr.post("/api/label/검은 잉어", data={"label":"활성","window":"today"})
check("일반유저 라벨 저장 성공(제한 풀림)", r.status_code==200)
# source 구분 박제 확인
_c = sqlite3.connect("rf4.db")
_src = dict(_c.execute("SELECT source, COUNT(*) FROM labels GROUP BY source").fetchall())
_c.close()
check("admin 라벨 source='admin'", _src.get("admin", 0) >= 1)
check("일반유저 라벨 source='user'", _src.get("user", 0) >= 1)

print("="*40)
print("실패", len(fails), "건" if fails else "— 전체 통과")
