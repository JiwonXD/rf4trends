# model.py — 활성도 분류 모델 추론 (D-43, RandomForest를 순수 파이썬으로 export)
#
# 태블릿(Termux/ARM)엔 sklearn을 설치할 수 없다(TUR에 패키지가 없고, pip 소스빌드는
# 비현실적). 그래서 학습은 PC(tools/train_model.py)에서만 하고, 학습된 트리 구조를
# model_data.json으로 내보내 여기서는 numpy/sklearn 없이 순수 파이썬으로만 추론한다.
#
# 클래스 인덱스(고정 순서, 순서형): 0=비활성 1=탐색 2=활성 3=강한 활성.
# 인덱스↔표시 문구 매핑은 scoring.py가 소유(이 모듈은 인덱스·확률만 다룬다).

import json
from pathlib import Path

_DATA_PATH = Path(__file__).parent / "model_data.json"
_data = None


def _load():
    global _data
    if _data is None:
        with open(_DATA_PATH, encoding="utf-8") as f:
            _data = json.load(f)
    return _data


def _encode(features):
    d = _load()
    row = []
    for col in d["numeric_features"]:
        v = features.get(col)
        row.append(float(v) if v is not None else d["numeric_medians"][col])
    for col in d["categorical_features"]:
        v = features.get(col)
        row.append(float(d["categories"][col].get(v, -1)))
    return row


def _tree_predict(tree, row):
    node = 0
    children_left = tree["children_left"]
    children_right = tree["children_right"]
    feature = tree["feature"]
    threshold = tree["threshold"]
    while feature[node] != -2:
        if row[feature[node]] <= threshold[node]:
            node = children_left[node]
        else:
            node = children_right[node]
    return tree["value"][node]


def predict_proba(features):
    """features: {n_rare, n_trophy, n_normal, n_total, consistency,
    trophy_ratio_max/min/avg, rare_ratio_max/min/avg, hours_since_reset,
    species, window, top_waterbody} 형태의 dict.
    반환: [P(비활성), P(탐색), P(활성), P(강한활성)] (합 1)."""
    d = _load()
    row = _encode(features)
    n_classes = 4
    totals = [0.0] * n_classes
    for tree in d["trees"]:
        probs = _tree_predict(tree, row)
        for i in range(n_classes):
            totals[i] += probs[i]
    n = len(d["trees"])
    return [t / n for t in totals]


def expected_value(probs):
    """확률 가중 기댓값(D-22) — 단계별 점수 0~3을 확률로 가중평균.
    0~100 스케일로 변환해 반환(0=비활성 ... 100=강한활성 확정)."""
    ev = sum(i * p for i, p in enumerate(probs))
    return round(ev / 3 * 100, 1)
