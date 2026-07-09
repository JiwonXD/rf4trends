# 라벨링 + 아카이브 검증
import sys, os as _os
sys.stdout.reconfigure(encoding="utf-8")  # 한국 Windows 콘솔(cp949)에서 em-dash 출력 크래시 방지
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
CREATE TABLE species_master (species TEXT PRIMARY KEY, trophy_g INT, rare_trophy_g INT, added_at TEXT DEFAULT (datetime('now')));
INSERT INTO species_master (species,trophy_g,rare_trophy_g) VALUES ('검은 잉어',28000,40000);
CREATE TABLE species_waterbodies (species TEXT NOT NULL, waterbody TEXT NOT NULL, UNIQUE (species, waterbody));
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
r = c.post("/api/label/검은 잉어", data={"label":"강한 활성","window":"today","waterbody":"곰 호수"})
check("라벨 저장 성공", r.status_code==200)
# 같은 어종 다시 라벨 (다른 값) → 새 행으로 쌓임
r = c.post("/api/label/검은 잉어", data={"label":"활성","window":"today","waterbody":"곰 호수"})
check("같은 어종 재라벨 성공", r.status_code==200)
# 잘못된 라벨 거부
r = c.post("/api/label/검은 잉어", data={"label":"이상한값","window":"today","waterbody":"곰 호수"})
check("잘못된 라벨 거부", r.status_code==400)

# DB 확인: 라벨 2건 쌓였는지 + 스냅샷 저장됐는지
conn = sqlite3.connect("rf4.db")
rows = conn.execute("SELECT label, n_total, consistency, top_bait, top_waterbody, family_consistency FROM labels ORDER BY id").fetchall()
check("라벨 2건 누적", len(rows)==2)
check("스냅샷 박제됨(n_total, 미끼)", rows[0][1] is not None and rows[0][3]=='크랜베리')
check("단일 수역 스냅샷(곰 호수, 6건)", rows[0][4]=='곰 호수' and rows[0][1]==6)
# family_consistency: 6건 전부 '크랜베리'(패밀리 파싱 안 되는 원문 그대로) → 미끼 일관성과 동일하게 100
check("family_consistency 박제됨(100)", rows[0][5]==100)
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
r = usr.post("/api/label/검은 잉어", data={"label":"활성","window":"today","waterbody":"곰 호수"})
check("일반유저 라벨 저장 성공(제한 풀림)", r.status_code==200)
# source 구분 박제 확인
_c = sqlite3.connect("rf4.db")
_src = dict(_c.execute("SELECT source, COUNT(*) FROM labels GROUP BY source").fetchall())
_c.close()
check("admin 라벨 source='admin'", _src.get("admin", 0) >= 1)
check("일반유저 라벨 source='user'", _src.get("user", 0) >= 1)


# 수역별 격리 검증: 같은 어종이 여러 수역에 있을 때 한 수역만 스냅샷에 잡혀야 함
_c = sqlite3.connect("rf4.db")
_c.execute("INSERT INTO species_master (species,trophy_g,rare_trophy_g) VALUES ('용잉어',1000,2000)")
for i in range(6):
    _c.execute("INSERT INTO catches (species,weight_g,waterbody,bait,player,caught_date,first_seen) VALUES "
               "('용잉어',?,'샘플호A','지렁이',?, '2026-06-20', datetime('now','-20 minute'))", (1500+i, f'a{i}'))
    _c.execute("INSERT INTO catches (species,weight_g,waterbody,bait,player,caught_date,first_seen) VALUES "
               "('용잉어',?,'샘플호B','지렁이',?, '2026-06-20', datetime('now','-20 minute'))", (1500+i, f'b{i}'))
_c.commit(); _c.close()

r = c.post("/api/label/용잉어", data={"label":"활성","window":"today","waterbody":"샘플호A"})
check("수역 지정 라벨 저장 성공", r.status_code==200)

_c = sqlite3.connect("rf4.db")
wb_row = _c.execute("SELECT n_total, top_waterbody FROM labels WHERE species='용잉어' ORDER BY id DESC LIMIT 1").fetchone()
_c.close()
check("수역별 격리(샘플호A만 6건, 12 아님)", wb_row[0]==6)
check("top_waterbody 일치", wb_row[1]=='샘플호A')

r = c.post("/api/label/용잉어", data={"label":"활성","window":"today","waterbody":"없는호수"})
check("존재하지 않는 수역 400", r.status_code==400)

# 리더보드 / 순위 검증
import auth
import labels as labels_mod

lconn = sqlite3.connect("rf4.db")
auth.init_db(lconn)

uid_a, _ = auth.create_user(lconn, "lb_alice", "secret123")
uid_b, _ = auth.create_user(lconn, "lb_bob", "secret123")
uid_hidden, _ = auth.create_user(lconn, "lb_hidden", "secret123")
uid_nonick, _ = auth.create_user(lconn, "lb_nonick", "secret123")
auth.change_nickname(lconn, uid_a, "lb_alice")
auth.change_nickname(lconn, uid_b, "lb_bob")
auth.change_nickname(lconn, uid_hidden, "lb_hidden")
# uid_nonick은 닉네임 미등록 상태로 둔다 (visible=1이어도 리더보드 제외돼야 함)
auth.set_leaderboard_visible(lconn, uid_hidden, False)
uid_admin = lconn.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]

since = "2026-01-01 00:00:00"
old = "2025-01-01 00:00:00"


def _label(uid, labeled_at, n=1):
    for _ in range(n):
        lconn.execute("""INSERT INTO labels (user_id, species, label, window, labeled_at)
                          VALUES (?, '검은 잉어', '활성', 'today', ?)""", (uid, labeled_at))
    lconn.commit()


_label(uid_a, since, 3)
_label(uid_b, since, 5)
_label(uid_hidden, since, 10)
_label(uid_nonick, since, 7)
_label(uid_admin, since, 10)
_label(uid_a, old, 100)  # since 이전 → 카운트 제외돼야 함

ranking = labels_mod.weekly_ranking(lconn, since, "admin")
names = [r["nickname"] for r in ranking]
counts = {r["nickname"]: r["count"] for r in ranking}
check("weekly_ranking: since 이전 라벨 제외(alice 3건만)", counts.get("lb_alice") == 3)
check("weekly_ranking: admin 제외", "admin" not in names)
check("weekly_ranking: 비공개 유저 제외", "lb_hidden" not in names)
check("weekly_ranking: 닉네임 미등록 유저 제외", "lb_nonick" not in names)
check("weekly_ranking: count DESC 정렬", ranking[0]["nickname"] == "lb_bob")

mr_a = labels_mod.my_rank(lconn, uid_a, since, "admin")
check("my_rank: bob(5)보다 적은 alice(3)는 2위", mr_a["rank"] == 2)
mr_hidden = labels_mod.my_rank(lconn, uid_hidden, since, "admin")
check("my_rank: 비공개 유저는 rank None", mr_hidden["rank"] is None and mr_hidden["count"] == 10)
mr_nonick = labels_mod.my_rank(lconn, uid_nonick, since, "admin")
check("my_rank: 닉네임 미등록은 count만 보이고 rank None",
      mr_nonick["rank"] is None and mr_nonick["count"] == 7)

# 닉네임 등록하면 리더보드에 나타나야 함
auth.change_nickname(lconn, uid_nonick, "lb_nonick")
ranking2 = labels_mod.weekly_ranking(lconn, since, "admin")
check("weekly_ranking: 닉네임 등록 후 노출", "lb_nonick" in [r["nickname"] for r in ranking2])
mr_nonick2 = labels_mod.my_rank(lconn, uid_nonick, since, "admin")
check("my_rank: 닉네임 등록 후 rank 부여", mr_nonick2["rank"] is not None)

uid_none, _ = auth.create_user(lconn, "lb_none", "secret123")
check("my_rank: 제보 0건은 None", labels_mod.my_rank(lconn, uid_none, since, "admin") is None)

# 동점 타이브레이크: 제보수 같으면 마지막 제보 시각이 이른 쪽이 위
uid_tie1, _ = auth.create_user(lconn, "lb_tie1", "secret123")
uid_tie2, _ = auth.create_user(lconn, "lb_tie2", "secret123")
auth.change_nickname(lconn, uid_tie1, "lb_tie1")
auth.change_nickname(lconn, uid_tie2, "lb_tie2")
_label(uid_tie1, "2026-01-02 00:00:00", 3)
_label(uid_tie1, "2026-01-02 10:00:00", 1)  # count=4, last_at=10:00 (더 늦음)
_label(uid_tie2, "2026-01-02 00:00:00", 3)
_label(uid_tie2, "2026-01-02 05:00:00", 1)  # count=4, last_at=05:00 (더 이름 → 위)

ranking3 = labels_mod.weekly_ranking(lconn, since, "admin")
tie_idx = {r["nickname"]: i for i, r in enumerate(ranking3)}
check("타이브레이크: 동일 count(4)에서 마지막 제보가 이른 tie2가 tie1보다 위",
      tie_idx["lb_tie2"] < tie_idx["lb_tie1"])
mr_tie1 = labels_mod.my_rank(lconn, uid_tie1, since, "admin")
mr_tie2 = labels_mod.my_rank(lconn, uid_tie2, since, "admin")
check("my_rank도 타이브레이크와 일치(tie2가 더 높은 순위)", mr_tie2["rank"] < mr_tie1["rank"])

lconn.close()

print("="*40)
print("실패", len(fails), "건" if fails else "— 전체 통과")
