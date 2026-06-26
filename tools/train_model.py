# train_model.py — RandomForest를 학습해 rf4site/model_data.json으로 export (D-43)
#
# 운영(scoring.py)이 쓰는 모델 자체는 이 파일이 만드는 게 전부다 — 태블릿엔
# sklearn이 깔리지 않으므로(TUR에 패키지 없음, pip 소스빌드는 비현실적), 학습은
# PC(tools/mlenv)에서만 하고 결과(트리 구조 + 인코딩 표)를 순수 데이터로 내보낸다.
# 런타임(rf4site/model.py)은 이 JSON만 읽고 numpy/sklearn 없이 추론한다.
#
# 실행: tools/mlenv/Scripts/python.exe tools/train_model.py "C:\\경로\\rf4.db"

import json
import sqlite3
import sys
from pathlib import Path

from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import OrdinalEncoder
from sklearn.impute import SimpleImputer
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.metrics import accuracy_score
import numpy as np

# 순서형 클래스 인덱스: 0=비활성 1=가능성(구 불명) 2=활성 3=강한 활성.
# 모델 산출물엔 라벨 텍스트를 담지 않는다 — 인덱스↔표시문구 매핑은 scoring.py가 소유.
LABEL_TO_IDX = {"비활성": 0, "불명": 1, "가능성": 1, "활성": 2, "강한 활성": 3}

NUMERIC = ["n_rare", "n_trophy", "n_normal", "n_total", "consistency",
           "trophy_ratio_max", "trophy_ratio_min", "trophy_ratio_avg",
           "rare_ratio_max", "rare_ratio_min", "rare_ratio_avg",
           "hours_since_reset"]
CATEGORICAL = ["species", "window", "top_waterbody"]
FEATURE_ORDER = NUMERIC + CATEGORICAL
SEED = 42

N_ESTIMATORS = 200
MAX_DEPTH = 6
MIN_SAMPLES_LEAF = 5

OUT_PATH = Path(__file__).parent.parent / "rf4site" / "model_data.json"


def load(db_path):
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT * FROM labels").fetchall()
    cols = [d[0] for d in conn.execute("SELECT * FROM labels LIMIT 1").description]
    conn.close()
    import pandas as pd
    df = pd.DataFrame(rows, columns=cols)
    df = df[df["window"] != "3d"]
    df = df[df["label"].isin(LABEL_TO_IDX)].copy()
    df["y"] = df["label"].map(LABEL_TO_IDX).astype(int)
    return df


def export_tree(tree):
    """단일 sklearn DecisionTree(tree_)를 순수 파이썬 추론용 배열로 변환.
    leaf의 value는 정규화된 클래스 확률(합 1)로 미리 변환해둔다 — 추론 시 나눗셈 불필요."""
    t = tree.tree_
    values = []
    for v in t.value:  # shape (n_nodes, 1, n_classes), 클래스별 가중 표본 수
        counts = v[0]
        total = counts.sum()
        values.append((counts / total).tolist() if total > 0 else [0.25] * 4)
    return {
        "children_left": t.children_left.tolist(),
        "children_right": t.children_right.tolist(),
        "feature": t.feature.tolist(),   # -2면 리프
        "threshold": t.threshold.tolist(),
        "value": values,
    }


def main():
    db = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\jiwon\Desktop\rf4.db"
    df = load(db)
    y = df["y"].to_numpy()
    X = df[FEATURE_ORDER].copy()

    enc = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)
    X[CATEGORICAL] = enc.fit_transform(X[CATEGORICAL].astype(str))
    imputer = SimpleImputer(strategy="median")
    X[NUMERIC] = imputer.fit_transform(X[NUMERIC])

    # 학습 전 5-fold 교차검증으로 export 직전 성능을 한 번 더 확인(회귀 방지)
    rf_cv = RandomForestClassifier(
        n_estimators=N_ESTIMATORS, max_depth=MAX_DEPTH,
        min_samples_leaf=MIN_SAMPLES_LEAF, random_state=SEED, n_jobs=-1)
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED)
    pred = cross_val_predict(rf_cv, X, y, cv=cv)
    acc = accuracy_score(y, pred)
    adj = float(np.mean(np.abs(pred - y) <= 1))
    print(f"[학습 전 검증] 표본 {len(df)}건, 정확도 {acc:.3f}, 인접(±1)정확도 {adj:.3f}")

    # 전체 데이터로 최종 모델 학습 (export용)
    rf = RandomForestClassifier(
        n_estimators=N_ESTIMATORS, max_depth=MAX_DEPTH,
        min_samples_leaf=MIN_SAMPLES_LEAF, random_state=SEED, n_jobs=-1)
    rf.fit(X, y)

    categories = {
        col: {cat: int(code) for code, cat in enumerate(cats)}
        for col, cats in zip(CATEGORICAL, enc.categories_)
    }
    medians = {col: float(m) for col, m in zip(NUMERIC, imputer.statistics_)}

    data = {
        "feature_order": FEATURE_ORDER,
        "numeric_features": NUMERIC,
        "categorical_features": CATEGORICAL,
        "categories": categories,
        "numeric_medians": medians,
        "n_estimators": N_ESTIMATORS,
        "trees": [export_tree(est) for est in rf.estimators_],
        "trained_on": len(df),
        "cv_accuracy": round(acc, 3),
        "cv_adjacent_accuracy": round(adj, 3),
    }
    OUT_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"[완료] {OUT_PATH} ({OUT_PATH.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
