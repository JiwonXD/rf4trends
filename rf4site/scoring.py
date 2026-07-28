# scoring.py — 활성도 계산 (RandomForest 분류 모델, D-43)
# 추천 로직은 이 모듈 안에서만 바뀐다. app.py는 이 모듈의 출력 형식만 의존.
# 실제 추론은 model.py(순수 파이썬, 태블릿에 sklearn 불필요)가 담당 — 이 모듈은
# 피처를 모아 model에 넘기고, 결과(확률)를 state/score로 변환하는 역할만 한다.
# 모델은 tools/train_model.py로 PC에서 학습해 rf4site/model_data.json으로 내보낸다.
#
# 모집단: 주간기록에 등장한 전체 기록(무게 하한 없음). 작은 기록이 갱신 안 되고
# 남아있다는 것 자체가 "큰 게 안 나온다 = 비활성"의 근거이므로 버리지 않는다(D-21).

import datetime as _dt
import re

import model as _model

MIN_SAMPLE = 5            # 전체 기록 최소 표본 (미만이면 모델 신뢰 구간 밖 — 그냥 비활성)

# 시간창: first_seen >= datetime('now', '-N hour')
# first_seen = 우리 수집기가 그 기록을 DB에 처음 담은 시각 (수집 시점 기준 롤링).
# caught_date(잡힌 날짜)가 아니라 first_seen으로 세는 이유: 주간 탑5는 24시간 동안
# 여러 번 갈리는데, 갈려나간 기록까지 수집기가 주워둔 "교체 빈도"가 곧 활성도다.
# 자정 경계가 아닌 접속(수집) 시점 기준 롤링이라, 언제 봐도 꽉 찬 6/24시간 표본을 본다.
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
    """UTC로 저장된 first_seen 문자열(예 '2026-06-16 07:25:05')을 KST 'MM-DD HH:MM'로 변환.
    파싱 실패 시 원본 앞 16자를 그대로 반환(방어적)."""
    if not utc_str:
        return ""
    try:
        d = _dt.datetime.fromisoformat(utc_str).replace(tzinfo=_dt.timezone.utc)
        return d.astimezone(_KST).strftime("%m-%d %H:%M")
    except (ValueError, TypeError):
        return utc_str[:16].replace("T", " ")


def week_start_utc(now_utc=None):
    """직전 주간 리셋(월 04:00 KST = 일 19:00 UTC) 시각을 UTC datetime으로 반환."""
    if now_utc is None:
        now_utc = _dt.datetime.now(_dt.timezone.utc)
    now_kst = now_utc.astimezone(_KST)
    monday = (now_kst - _dt.timedelta(days=now_kst.weekday())).replace(
        hour=4, minute=0, second=0, microsecond=0)
    if now_kst < monday:
        monday -= _dt.timedelta(days=7)
    return monday.astimezone(_dt.timezone.utc)


def hours_since_reset(now_utc=None):
    """직전 주간 리셋(월 04:00 KST)으로부터 경과 시간(시간 단위, 0~168).
    태블릿 시간대 설정과 무관하게 UTC 기준으로 계산한다.
    라벨 학습 피처: 주 초반(0 근처)과 주말(168 근처)의 활성 추세 차이를 담는다."""
    if now_utc is None:
        now_utc = _dt.datetime.now(_dt.timezone.utc)
    now_kst = now_utc.astimezone(_KST)
    monday_kst = week_start_utc(now_utc).astimezone(_KST)
    return round((now_kst - monday_kst).total_seconds() / 3600, 1)


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
    JOIN species_master t ON t.species = c.species
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


# 미끼 패밀리 정규화 (D-49): 크기 표기만 제거해 "같은 미끼, 크기만 다름"을 한 키로 합친다.
# 보일리류는 끝 숫자가 크기(연어 팝업 14/20), 루어는 크기토큰-시리즈(Balsa Crank 80F-003 →
# 80F만 크기, -003 시리즈는 색상 패턴이라 정체성 유지). 2미끼 조합("A; B")은 성분 패밀리를
# 정렬해 순서 차이(A; B vs B; A)를 흡수한다. 파싱 안 되는 이름은 원문 소문자 그대로 —
# 실패해도 기존(원문 비교)과 동일할 뿐 더 나빠지지 않는다.
_EX_STORFISK = re.compile(r"^(?P<base>Stor Fisk)\s+[A-Z]{1,2}\d+-\d+\s+(?P<series>#\d+)$")
_EX_PILKER = re.compile(r"^(?P<base>Pilker №\d+)-\d+(?:\s+(?P<series>[A-Z]{2}))?$")
_EX_CLR = re.compile(r"^(?P<base>.+?)\s+\d+(?:\.\d+)?\s+(?P<series>CLR-[A-Z])$")
_EX_MS = re.compile(r"^(?P<base>.+?)\s+[msl]-(?P<series>\d+)$")
_R_SPACED = re.compile(r"^(?P<base>.+?)\s*-?\s*\d+(?:[.]\d+)?(?:-\d+)?\s+-\s+(?P<series>\d+)$")
_R_ALPHA_SIZE = re.compile(r"^(?P<base>.+?)\s+[A-Za-z]{1,2}\d+(?:[.]\d+)?-(?P<series>[A-Za-z]{0,2}\d+)$")
_R_NUM_SIZE = re.compile(r"^(?P<base>.+?)\s*\d+(?:[.]\d+)?[A-Za-z]{0,2}-(?P<series>[A-Za-z]{0,2}\d+)$")
_R_PADDED = re.compile(r"^(?P<base>.*\S)\s+(?P<series>0\d{1,2})$")
_R_SIZE_TAIL = re.compile(r"^(?P<base>.+?)\s+#?\d+(?:[./]\d+)?\s*(?:g|oz|kg|mm)?$", re.I)
_R_TRAIL_SIZE = re.compile(r"^(?P<base>.*\S)\s+[1-9]\d{0,3}(?:[.]\d+)?$")


def _clean_base(base):
    return base.rstrip(" -").rstrip()


def _component_family(name):
    name = " ".join(name.replace("\xa0", " ").split())
    for rx in (_EX_STORFISK, _EX_PILKER, _EX_CLR, _EX_MS,
               _R_SPACED, _R_ALPHA_SIZE, _R_NUM_SIZE):
        m = rx.match(name)
        if m:
            base = _clean_base(m.group("base"))
            series = m.groupdict().get("series")
            return f"{base} -{series}" if series else base
    m = _R_PADDED.match(name)
    if m:
        m2 = _R_SIZE_TAIL.match(m.group("base"))
        if m2:
            return f"{_clean_base(m2.group('base'))} -{m.group('series')}"
        return name
    m = _R_TRAIL_SIZE.match(name)
    if m:
        return _clean_base(m.group("base"))
    return name


def bait_family(bait):
    """미끼 이름에서 크기 표기를 제거한 패밀리 키(소문자). 2미끼 조합은 성분을 정렬해
    순서 차이를 흡수한다. family_consistency 피처 계산에만 쓰이며, 기존 consistency
    (원문 미끼 최빈값)는 절대 이 함수를 거치지 않는다(구 라벨 호환)."""
    if not bait:
        return bait
    parts = [p.strip() for p in bait.split(";") if p.strip()]
    return "; ".join(sorted(_component_family(p).lower() for p in parts))


_RATIO_KEYS = ("trophy_ratio_max", "trophy_ratio_min", "trophy_ratio_avg",
               "rare_ratio_max", "rare_ratio_min", "rare_ratio_avg")


def _trophy_thresholds(conn, species):
    """(trophy_g, rare_g) 반환. 미등록이면 (None, None)."""
    th = conn.execute(
        "SELECT trophy_g, rare_trophy_g FROM species_master WHERE species = ?",
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
    _, family_share = _top_share([bait_family(r[1]) for r in rows])
    family_consistency = round(family_share * 100)

    if n_total == 0:
        return {"state": STATE_INACTIVE, "score": 0.0, "low_sample": True,
                "n_rare": 0, "n_trophy": 0, "n_normal": 0, "n_total": 0,
                "consistency": 0, "family_consistency": 0, "top_bait": None}

    base = {"n_rare": n_rare, "n_trophy": n_trophy, "n_normal": n_normal,
            "n_total": n_total, "consistency": consistency_pct,
            "family_consistency": family_consistency, "top_bait": top_bait}

    if n_total < MIN_SAMPLE:
        # 표본 미달은 모델 신뢰 구간 밖 — 합산 보정 없이 그냥 비활성 처리(원칙 #2 유지)
        return {**base, "state": STATE_INACTIVE, "score": 0.0, "low_sample": True}

    features = {
        **{k: v for k, v in base.items() if k != "top_bait"},
        **_ratio_from_rows(rows, trophy_g, rare_g),
        "hours_since_reset": hours_since_reset(),
        "species": species, "window": window, "top_waterbody": waterbody,
    }
    probs = _model.predict_proba(features)
    state_idx = max(range(len(probs)), key=lambda i: probs[i])
    return {**base, "state": _STATE_BY_IDX[state_idx],
            "score": _model.expected_value(probs), "low_sample": False}


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
        "low_sample": rep["low_sample"],
        "n_rare": rep["n_rare"],
        "n_trophy": rep["n_trophy"],
        "n_normal": rep["n_normal"],
        "n_total": rep["n_total"],
        "consistency": rep["consistency"],
        "family_consistency": rep["family_consistency"],
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
        "low_sample": rep["low_sample"],
        "n_rare": rep["n_rare"],
        "n_trophy": rep["n_trophy"],
        "n_normal": rep["n_normal"],
        "n_total": rep["n_total"],
        "consistency": rep["consistency"],
        "family_consistency": rep["family_consistency"],
        "top_bait": rep["top_bait"],
        "top_waterbody": waterbody,
    }


# 전 어종 사전계산 스토어 (D-53): {window: {species: card_dict}}.
# 데이터는 15분 수집 주기에만 바뀌는데 활성도는 요청마다 다시 계산됐다 — 같은 사이클
# 안에서 접속할 때마다 같은 계산을 반복하던 것을, 수집 사이클(_collect_loop, app.py)
# 끝에 refresh_scores로 한 번만 계산해 두고 요청은 cached_card로 조회만 하게 바꿨다.
# 계산 비용을 아무도 기다리지 않는 백그라운드 스레드로 옮긴 셈(251종 × 두 시간창
# 전량이 태블릿 기준으로도 15분 주기에 충분히 들어간다). 저장된 점수는 다음 사이클까지 최대 15분 낡을 수
# 있다(시간창이 datetime('now') 기준 롤링이고 hours_since_reset 피처도 시간 의존이지만,
# 데이터 자체가 15분 주기로만 바뀌므로 이 정도 지연은 의도된 트레이드오프다).
_store = {}


def refresh_scores(conn):
    """species_master 전 어종 × WINDOWS 전 시간창의 활성도를 새로 계산해 스토어를
    통째로 교체한다. 새 dict를 완전히 채운 뒤 모듈 변수에 한 번에 대입하는 원자
    교체 방식 — 단일 프로세스 + GIL 덕분에 dict 참조 대입 자체가 원자적이라, 조회
    스레드가 절반만 채워진 스토어를 볼 일이 없다(락 불필요). 계산한 어종 수를 반환."""
    species_list = [r[0] for r in conn.execute("SELECT species FROM species_master")]
    new_store = {window: {sp: score_species(conn, sp, window) for sp in species_list}
                 for window in WINDOWS}
    global _store
    _store = new_store
    return len(species_list)


def cached_card(conn, species, window="today"):
    """스토어에서 어종 카드 조회. 스토어에 없으면(기동 직후 등 첫 refresh_scores
    전) score_species로 즉석 계산해 반환한다 — 단, 그 결과를 스토어에 쓰지는
    않는다(스토어 쓰기는 refresh_scores 한곳으로 유지).
    반환된 카드는 스토어에 담긴 그 객체다. 호출부에서 고치면 다음 사이클까지 모든
    사용자에게 번지므로 읽기 전용으로만 쓸 것."""
    card = _store.get(window, {}).get(species)
    if card is not None:
        return card
    return score_species(conn, species, window)


def top_active(window, limit=5):
    """전 어종 중 활성도 상위 limit개(비활성 제외). 스토어만 읽고 즉석 계산으로
    폴백하지 않는다 — 스토어가 비어 있으면(기동 직후 첫 refresh_scores 전) 빈
    리스트를 반환한다(251종 즉석 계산은 D-51에서 보류됐던 성능 함정 재현이므로)."""
    cards = [c for c in _store.get(window, {}).values() if c["state"] != STATE_INACTIVE]
    cards.sort(key=lambda c: (c["score"], c["n_total"]), reverse=True)
    return cards[:limit]


def dashboard(conn, favorites, window="today"):
    """선호 어종 전체 평가. 활성도 점수 내림차순, 비활성은 항상 하단."""
    cards = [cached_card(conn, sp, window) for sp in favorites]
    active = [c for c in cards if c["state"] != STATE_INACTIVE]
    inactive = [c for c in cards if c["state"] == STATE_INACTIVE]
    active.sort(key=lambda c: c["score"], reverse=True)
    inactive.sort(key=lambda c: c["n_total"], reverse=True)
    return active + inactive


def species_detail(conn, species, window="today"):
    """어종 상세: 원본 기록 + 수역별 활성도 + 기준선.
    미끼/장소/트로피 집계·필터·표시는 전부 클라이언트가 records로 수행한다(D-38).
    활성도 점수(score/state)만 모델 추론이 필요해 서버가 수역별로 계산해 넘긴다."""
    trophy_g, rare_g = _trophy_thresholds(conn, species)

    # 수역별 활성도 점수·상태 — 대시보드 대표값과 같은 기준(전체 기록)으로 계산.
    all_rows = _tier_records(conn, species, window)
    rows_by_water = {}
    for r in all_rows:
        rows_by_water.setdefault(r[2], []).append(r)
    water_score = {wb: _score_from_rows(rs, trophy_g, rare_g, species, window, wb)
                   for wb, rs in rows_by_water.items()}

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

    # 서식 수역 맵(species_waterbodies, D-46 수동 시드) — 장소 분포에 기록 0건 수역도
    # 표시하기 위해 넘긴다. 점수는 기록 있는 수역만 계산(0건은 클라이언트가 비활성 처리).
    habitat_waterbodies = [r[0] for r in conn.execute(
        "SELECT waterbody FROM species_waterbodies WHERE species = ? ORDER BY waterbody",
        (species,))]

    return {
        "card": card,
        "trophy_str": _weight_str(trophy_g) if trophy_g else None,
        "rare_str": _weight_str(rare_g) if rare_g else None,
        "records": records,
        "water_scores": water_scores,
        "habitat_waterbodies": habitat_waterbodies,
        "trophy_g": trophy_g,
        "rare_g": rare_g,
    }
