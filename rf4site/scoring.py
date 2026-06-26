# scoring.py — 활성도 계산 (RandomForest 분류 모델, D-43)
# 추천 로직은 이 모듈 안에서만 바뀐다. app.py는 이 모듈의 출력 형식만 의존.
# 실제 추론은 model.py(순수 파이썬, 태블릿에 sklearn 불필요)가 담당 — 이 모듈은
# 피처를 모아 model에 넘기고, 결과(확률)를 state/score로 변환하는 역할만 한다.
# 모델은 tools/train_model.py로 PC에서 학습해 rf4site/model_data.json으로 내보낸다.
#
# 모집단: 주간기록에 등장한 전체 기록(무게 하한 없음). 작은 기록이 갱신 안 되고
# 남아있다는 것 자체가 "큰 게 안 나온다 = 비활성"의 근거이므로 버리지 않는다(D-21).

import datetime as _dt

import model as _model

MIN_SAMPLE = 5            # 전체 기록 최소 표본 (미만이면 모델 신뢰 구간 밖 — 그냥 비활성)

# 시간창: first_seen >= datetime('now', '-N hour')
# first_seen = 우리 수집기가 그 기록을 DB에 처음 담은 시각 (수집 시점 기준 롤링).
# caught_date(잡힌 날짜)가 아니라 first_seen으로 세는 이유: 주간 탑5는 24시간 동안
# 여러 번 갈리는데, 갈려나간 기록까지 수집기가 주워둔 "교체 빈도"가 곧 활성도다.
# 자정 경계가 아닌 접속(수집) 시점 기준 롤링이라, 언제 봐도 꽉 찬 24/72시간 표본을 본다.
WINDOWS = {"6h": 6, "today": 24}   # 단위: 시간(hour)

STATE_STRONG = "강한 활성"
STATE_ACTIVE = "활성"
STATE_POSSIBLE = "탐색"     # 구 '불명'→'가능성'(D-42)→'탐색'(D-44) — 확실히 활성은 아니나 가볼 만한 가치가 있는 단계
STATE_INACTIVE = "비활성"
# 모델 클래스 인덱스(순서형, D-22) ↔ 표시 문구. model.py는 인덱스만 다루고 이 매핑은 몰라야 한다.
_STATE_BY_IDX = [STATE_INACTIVE, STATE_POSSIBLE, STATE_ACTIVE, STATE_STRONG]


# 주간기록 리셋: 매주 월요일 04:00 KST (= 일요일 19:00 UTC). 러시아 서머타임 폐지로 고정.
_KST = _dt.timezone(_dt.timedelta(hours=9))


def _weight_str(weight_g):
    """무게 표시 문자열. 1kg 이상은 'X.XXX kg', 1kg 미만은 'NNN g'."""
    if weight_g >= 1000:
        return f"{round(weight_g / 1000, 3)} kg"
    return f"{int(weight_g)} g"


def _to_kst_str(utc_str):
    """UTC로 저장된 first_seen 문자열(예 '2026-06-16T07:25:05')을 KST 'MM-DD HH:MM'로 변환.
    파싱 실패 시 원본 앞 16자를 그대로 반환(방어적)."""
    if not utc_str:
        return ""
    try:
        d = _dt.datetime.fromisoformat(utc_str).replace(tzinfo=_dt.timezone.utc)
        return d.astimezone(_KST).strftime("%m-%d %H:%M")
    except (ValueError, TypeError):
        return utc_str[:16].replace("T", " ")


def hours_since_reset(now_utc=None):
    """직전 주간 리셋(월 04:00 KST)으로부터 경과 시간(시간 단위, 0~168).
    태블릿 시간대 설정과 무관하게 UTC 기준으로 계산한다.
    라벨 학습 피처: 주 초반(0 근처)과 주말(168 근처)의 활성 추세 차이를 담는다."""
    if now_utc is None:
        now_utc = _dt.datetime.now(_dt.timezone.utc)
    now_kst = now_utc.astimezone(_KST)
    monday = (now_kst - _dt.timedelta(days=now_kst.weekday())).replace(
        hour=4, minute=0, second=0, microsecond=0)
    if now_kst < monday:
        monday -= _dt.timedelta(days=7)
    return round((now_kst - monday).total_seconds() / 3600, 1)


# [SQL 안전 규율] 이 모듈은 일부 SQL을 f-string으로 조립한다.
# 현재는 끼워넣는 값이 전부 서버 상수(WINDOWS 딕셔너리, int 캐스팅된 무게)뿐이라 안전하다.
# 절대 규칙: 사용자 입력(어종명/미끼/검색어 등)은 f-string에 넣지 말고 반드시 ? 바인딩으로만 전달할 것.
# (species 등은 이미 ? 바인딩으로 처리됨)
def _window_clause(window):
    # window는 호출부에서 norm_window()로 검증된 키만 들어오며, 여기서도 .get 기본값으로 한 번 더 가둔다
    hours = WINDOWS.get(window, WINDOWS["today"])
    return f"datetime('now', '-{int(hours)} hour')"


def _tier_records(conn, species, window):
    """시간창(first_seen 기준 롤링) 내 해당 어종의 **전체 기록**을
    (tier, bait, waterbody, weight_g, caught_date, first_seen)로 반환.
    tier: 'rare' | 'trophy' | 'normal'. 트로피 기준 미등록 어종이면 빈 리스트.
    무게 하한 없음 — 작은 기록도 활성도 판단의 근거이므로 전부 포함."""
    q = f"""
    SELECT CASE
             WHEN t.rare_trophy_g IS NOT NULL AND c.weight_g >= t.rare_trophy_g THEN 'rare'
             WHEN c.weight_g >= t.trophy_g THEN 'trophy'
             ELSE 'normal'
           END AS tier,
           c.bait, c.waterbody, c.weight_g, c.caught_date, c.first_seen
    FROM catches c
    JOIN trophies t ON t.species = c.species
    WHERE c.species = ?
      AND t.trophy_g IS NOT NULL
      AND c.first_seen >= {_window_clause(window)}
    """
    return conn.execute(q, (species,)).fetchall()


def _top_share(values):
    """최빈값과 그 점유율. values가 비면 (None, 0.0)."""
    vals = [v for v in values if v]
    if not vals:
        return None, 0.0
    counts = {}
    for v in vals:
        counts[v] = counts.get(v, 0) + 1
    top = max(counts, key=counts.get)
    return top, counts[top] / len(vals)


_RATIO_KEYS = ("trophy_ratio_max", "trophy_ratio_min", "trophy_ratio_avg",
               "rare_ratio_max", "rare_ratio_min", "rare_ratio_avg")


def _trophy_thresholds(conn, species):
    """(trophy_g, rare_g) 반환. 미등록이면 (None, None)."""
    th = conn.execute(
        "SELECT trophy_g, rare_trophy_g FROM trophies WHERE species = ?",
        (species,)).fetchone()
    if not th or not th[0]:
        return None, None
    return th[0], th[1]


def _ratio_from_rows(rows, trophy_g, rare_g):
    """기록 묶음의 무게/트로피기준, 무게/레어기준 비율(최대·최소·평균).
    트로피 기준 없거나 기록 없으면 전부 None."""
    weights = [r[3] for r in rows]
    if not trophy_g or not weights:
        return {k: None for k in _RATIO_KEYS}
    t_ratios = [w / trophy_g for w in weights]
    out = {
        "trophy_ratio_max": round(max(t_ratios), 4),
        "trophy_ratio_min": round(min(t_ratios), 4),
        "trophy_ratio_avg": round(sum(t_ratios) / len(t_ratios), 4),
    }
    if rare_g:
        r_ratios = [w / rare_g for w in weights]
        out.update({
            "rare_ratio_max": round(max(r_ratios), 4),
            "rare_ratio_min": round(min(r_ratios), 4),
            "rare_ratio_avg": round(sum(r_ratios) / len(r_ratios), 4),
        })
    else:
        out.update({"rare_ratio_max": None, "rare_ratio_min": None,
                    "rare_ratio_avg": None})
    return out


def ratio_stats(conn, species, window, waterbody=None):
    """라벨 학습용 비율 피처(공개 API — app.py가 라벨 저장 시 호출).
    waterbody가 주어지면 그 수역 기록만으로 계산한다."""
    trophy_g, rare_g = _trophy_thresholds(conn, species)
    if trophy_g is None:
        return {k: None for k in _RATIO_KEYS}
    rows = _tier_records(conn, species, window)
    if waterbody is not None:
        rows = [r for r in rows if r[2] == waterbody]
    return _ratio_from_rows(rows, trophy_g, rare_g)


def _score_from_rows(rows, trophy_g, rare_g, species, window, waterbody):
    """기록 묶음(rows)으로 활성도 지표 1세트 계산.
    rows: _tier_records가 반환하는 (tier, bait, waterbody, weight_g, caught_date, first_seen) 리스트.
    한 수역의 기록만 넘기면 그 수역의 활성도가 된다.
    분류는 RandomForest 모델(model.py, D-43)이 맡고, 이 함수는 피처를 모아 넘긴
    뒤 결과(확률)를 state/score로 변환한다."""
    n_rare = sum(1 for r in rows if r[0] == "rare")
    n_trophy = sum(1 for r in rows if r[0] == "trophy")
    n_normal = sum(1 for r in rows if r[0] == "normal")
    n_total = len(rows)
    top_bait, consistency = _top_share([r[1] for r in rows])
    consistency_pct = round(consistency * 100)

    if n_total == 0:
        return {"state": STATE_INACTIVE, "score": 0.0,
                "n_rare": 0, "n_trophy": 0, "n_normal": 0, "n_total": 0,
                "consistency": 0, "top_bait": None}

    base = {"n_rare": n_rare, "n_trophy": n_trophy, "n_normal": n_normal,
            "n_total": n_total, "consistency": consistency_pct, "top_bait": top_bait}

    if n_total < MIN_SAMPLE:
        # 표본 미달은 모델 신뢰 구간 밖 — 합산 보정 없이 그냥 비활성 처리(원칙 #2 유지)
        return {**base, "state": STATE_INACTIVE, "score": 0.0}

    features = {
        **{k: v for k, v in base.items() if k != "top_bait"},
        **_ratio_from_rows(rows, trophy_g, rare_g),
        "hours_since_reset": hours_since_reset(),
        "species": species, "window": window, "top_waterbody": waterbody,
    }
    probs = _model.predict_proba(features)
    state_idx = max(range(len(probs)), key=lambda i: probs[i])
    return {**base, "state": _STATE_BY_IDX[state_idx],
            "score": _model.expected_value(probs)}


def score_species(conn, species, window="today"):
    """어종 1개의 활성도 평가. 대시보드 카드 1장에 필요한 모든 값.
    수역별로 따로 집계해, 가장 활성도 점수가 높은 수역을 대표값으로 쓴다.
    (게임이 수역별로 독립적으로 돌아가므로 — 같은 어종이라도 수역마다 먹는 미끼가
     달라, 전체를 합치면 미끼 일관성이 희석되어 활성도가 낮게 잡히는 문제를 해결.)"""
    trophy_g, rare_g = _trophy_thresholds(conn, species)
    rows = _tier_records(conn, species, window)

    # 수역별로 기록을 나눠 각각 점수 계산
    by_water = {}
    for r in rows:
        by_water.setdefault(r[2], []).append(r)
    per_water = {wb: _score_from_rows(rs, trophy_g, rare_g, species, window, wb)
                 for wb, rs in by_water.items()}

    if per_water:
        # 대표 수역 = 점수가 가장 높은 수역. 동점이면 표본 많은 쪽.
        top_wb = max(per_water,
                     key=lambda wb: (per_water[wb]["score"], per_water[wb]["n_total"]))
        rep = per_water[top_wb]
    else:
        # 기록이 아예 없으면(트로피 미등록 등) 빈 카드
        rep = _score_from_rows([], trophy_g, rare_g, species, window, None)
        top_wb = None

    return {
        "species": species,
        "state": rep["state"],
        "score": rep["score"],
        "n_rare": rep["n_rare"],
        "n_trophy": rep["n_trophy"],
        "n_normal": rep["n_normal"],
        "n_total": rep["n_total"],
        "consistency": rep["consistency"],
        "top_bait": rep["top_bait"],
        "top_waterbody": top_wb,
    }


def score_species_at(conn, species, window="today", waterbody=None):
    """어종 1개를 지정한 수역 1곳만 기준으로 평가. score_species와 같은 키 구성을
    반환하되, top_waterbody는 인자로 받은 waterbody를 그대로 넣는다.
    (라벨링을 수역 단위로 박제하기 위함 — 대시보드는 score_species를 그대로 쓴다.)"""
    trophy_g, rare_g = _trophy_thresholds(conn, species)
    rows = _tier_records(conn, species, window)
    rows = [r for r in rows if r[2] == waterbody]
    rep = _score_from_rows(rows, trophy_g, rare_g, species, window, waterbody)
    return {
        "species": species,
        "state": rep["state"],
        "score": rep["score"],
        "n_rare": rep["n_rare"],
        "n_trophy": rep["n_trophy"],
        "n_normal": rep["n_normal"],
        "n_total": rep["n_total"],
        "consistency": rep["consistency"],
        "top_bait": rep["top_bait"],
        "top_waterbody": waterbody,
    }


def dashboard(conn, favorites, window="today"):
    """선호 어종 전체 평가. 활성도 점수 내림차순, 비활성은 항상 하단."""
    cards = [score_species(conn, sp, window) for sp in favorites]
    active = [c for c in cards if c["state"] != STATE_INACTIVE]
    inactive = [c for c in cards if c["state"] == STATE_INACTIVE]
    active.sort(key=lambda c: c["score"], reverse=True)
    inactive.sort(key=lambda c: c["n_total"], reverse=True)
    return active + inactive


def species_detail(conn, species, window="today"):
    """어종 상세: 미끼 순위 / 장소 분포 / 최근 트로피 기록 / 기준선.
    미끼·장소·트로피 집계는 초기값이며, 실제 표시·필터는 클라이언트가
    records로 직접 재집계한다(교차 필터링). 트로피만 보기도 클라이언트 토글."""
    wc = _window_clause(window)
    trophy_g, rare_g = _trophy_thresholds(conn, species)

    bait_rows = conn.execute(f"""
        SELECT c.bait, COUNT(*) AS n
        FROM catches c
        WHERE c.species = ? AND c.bait IS NOT NULL
          AND c.first_seen >= {wc}
        GROUP BY c.bait ORDER BY n DESC LIMIT 15
    """, (species,)).fetchall()
    # 분모는 상위15개 합이 아니라 시간창 내 미끼 있는 전체 기록 수.
    # (LIMIT으로 자른 합을 분모로 쓰면 1등 비율이 부풀려져 카드 일관성과 불일치)
    bait_total = conn.execute(f"""
        SELECT COUNT(*) FROM catches c
        WHERE c.species = ? AND c.bait IS NOT NULL
          AND c.first_seen >= {wc}
    """, (species,)).fetchone()[0] or 1
    baits = [{"bait": r[0], "n": r[1], "share": round(r[1] * 100 / bait_total)}
             for r in bait_rows]

    place_rows = conn.execute(f"""
        SELECT c.waterbody, COUNT(*) AS n
        FROM catches c
        WHERE c.species = ? AND c.first_seen >= {wc}
        GROUP BY c.waterbody ORDER BY n DESC LIMIT 10
    """, (species,)).fetchall()
    # 수역별 활성도 점수·상태 — 대시보드 대표값과 같은 기준(전체 기록)으로 계산.
    all_rows = _tier_records(conn, species, window)
    rows_by_water = {}
    for r in all_rows:
        rows_by_water.setdefault(r[2], []).append(r)
    water_score = {wb: _score_from_rows(rs, trophy_g, rare_g, species, window, wb)
                   for wb, rs in rows_by_water.items()}
    places = [{
        "waterbody": r[0], "n": r[1],
        "score": water_score.get(r[0], {}).get("score", 0),
        "state": water_score.get(r[0], {}).get("state", STATE_INACTIVE),
    } for r in place_rows]

    trophy_records = []
    if trophy_g:
        rows = conn.execute(f"""
            SELECT c.weight_g, c.waterbody, c.bait, c.first_seen
            FROM catches c
            WHERE c.species = ? AND c.weight_g >= ?
              AND c.first_seen >= {wc}
            ORDER BY c.first_seen DESC, c.weight_g DESC LIMIT 15
        """, (species, int(trophy_g))).fetchall()
        # first_seen은 UTC 저장(수집 시각). 화면엔 KST로 변환해 보여준다.
        # caught_date(사이트가 주는 MSK 기준 날짜) 대신 first_seen을 쓰는 이유:
        # 활성도 시간창과 기준이 통일되고, MSK/KST 날짜 혼란이 사라진다.
        trophy_records = [{
            "weight": _weight_str(r[0]),
            "rare": bool(rare_g and r[0] >= rare_g),
            "waterbody": r[1], "bait": r[2], "date": _to_kst_str(r[3]),
        } for r in rows]

    card = score_species(conn, species, window)

    # 교차 필터링용 원본 기록 + 수역별 활성도 점수.
    # 미끼/장소/트로피 집계는 클라이언트(JS)가 이 records로 직접 계산·필터링한다.
    # 단 활성도 점수(score/state)는 모델 추론이 필요해 서버가 수역별로 계산해 넘긴다
    # (모델이 또 바뀌어도 서버만 고치면 되도록 — JS에 점수 로직을 중복시키지 않음).
    records = [{
        "weight_g": r[3],
        "weight": _weight_str(r[3]),
        "waterbody": r[2],
        "bait": r[1],
        "tier": r[0],                       # 'rare' | 'trophy' | 'normal'
        "date": _to_kst_str(r[5]),          # first_seen(UTC)→KST
    } for r in all_rows]
    water_scores = {wb: {"score": s["score"], "state": s["state"]}
                    for wb, s in water_score.items()}

    return {
        "card": card,
        "trophy_str": _weight_str(trophy_g) if trophy_g else None,
        "rare_str": _weight_str(rare_g) if rare_g else None,
        "baits": baits,
        "places": places,
        "trophy_records": trophy_records,
        "records": records,
        "water_scores": water_scores,
        "trophy_g": trophy_g,
        "rare_g": rare_g,
    }
