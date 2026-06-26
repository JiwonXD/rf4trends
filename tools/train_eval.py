# train_eval.py — 활성도 분류 모델 평가 실험 (운영 코드 아님, 개발용 dev 스크립트)
#
# 목적: 라벨 스냅샷의 원본 피처가 사람이 찍은 활성도 라벨을 실제로 예측하는지 확인.
#       정확도·인접정확도·순서형 MAE·혼동행렬·피처 중요도를 뽑아, 운영(scoring.py)
#       교체로 넘어갈 만한 신호가 있는지 판단한다. scoring.py는 건드리지 않는다.
#
# 실행 (tools/mlenv 가상환경에서):
#   tools/mlenv/Scripts/python.exe tools/train_eval.py "C:\\Users\\jiwon\\Desktop\\rf4.db"
#
# 트리 모델은 sklearn 내장 HistGradientBoosting(히스토그램 기반 GBT) 사용 —
# 운영 통합 시엔 LightGBM으로 교체 예정(D-31). 신호 확인엔 동등 계열.

import sqlite3
import sys

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.metrics import accuracy_score, confusion_matrix

# 순서형: 비활성 < 가능성 < 활성 < 강한 활성
# '불명'은 D-42에서 '가능성'으로 이름이 바뀌었으나, 옛 라벨(이름 변경 전 박제분)도
# 학습에 포함해야 하므로 같은 인덱스로 매핑한다.
ORDER = ["비활성", "가능성", "활성", "강한 활성"]
ORD = {lab: i for i, lab in enumerate(ORDER)}
ORD["불명"] = ORD["가능성"]

NUMERIC = ["n_rare", "n_trophy", "n_normal", "n_total", "consistency",
           "trophy_ratio_max", "trophy_ratio_min", "trophy_ratio_avg",
           "rare_ratio_max", "rare_ratio_min", "rare_ratio_avg",
           "hours_since_reset"]
CATEGORICAL = ["species", "window", "top_waterbody"]
SEED = 42


def load(db_path):
    conn = sqlite3.connect(db_path)
    df = pd.read_sql("SELECT * FROM labels", conn)
    conn.close()
    # 폐기된 3d 시간창(소수, 다른 스케일로 박제됨)은 제외
    df = df[df["window"] != "3d"].copy()
    df = df[df["label"].isin(ORD)].copy()
    df["y"] = df["label"].map(ORD).astype(int)
    return df


def make_X(df):
    X = df[NUMERIC + CATEGORICAL].copy()
    for c in CATEGORICAL:
        X[c] = X[c].astype("category")
    return X


def hgb():
    # 범주형은 category dtype에서 자동 인식, 결측(NaN)도 네이티브 처리
    return HistGradientBoostingClassifier(
        categorical_features="from_dtype",
        learning_rate=0.05, max_iter=300, max_depth=4,
        min_samples_leaf=10, l2_regularization=1.0, random_state=SEED)


def report_cv(name, model, X, y, cv):
    pred = cross_val_predict(model, X, y, cv=cv)
    acc = accuracy_score(y, pred)
    adj = np.mean(np.abs(pred - y) <= 1)          # 인접(±1) 정확도
    mae = np.mean(np.abs(pred - y))               # 순서형 MAE
    print(f"\n[{name}]")
    print(f"  정확도      : {acc:.3f}")
    print(f"  인접(±1)정확도: {adj:.3f}")
    print(f"  순서형 MAE  : {mae:.3f}")
    return pred


def main():
    db = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\jiwon\Desktop\rf4.db"
    df = load(db)
    X = make_X(df)
    y = df["y"].to_numpy()

    print("=" * 56)
    print("활성도 분류 모델 평가 실험")
    print("=" * 56)
    print(f"표본: {len(df)}건 (3d 제외), 어종 {df['species'].nunique()}종")
    print("클래스 분포:", {ORDER[i]: int((y == i).sum()) for i in range(4)})

    # 현재 임시 수식(score)이 라벨을 얼마나 잘 따라가는지 — 넘어야 할 기준선
    rho, _ = spearmanr(df["score"], y)
    print(f"\n[참고] 현재 수식 score ↔ 라벨 Spearman 상관: {rho:.3f}")
    print("  (1에 가까울수록 현재 수식이 이미 사람 판단을 잘 따라간다는 뜻)")

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED)

    # 1) 다수 클래스 베이스라인 (정확도 바닥)
    report_cv("베이스라인 · 다수클래스",
              DummyClassifier(strategy="most_frequent"), X, y, cv)

    # 2) 로지스틱 회귀 — 수치 피처만 (D-31 베이스라인)
    Xn = X[NUMERIC]
    logreg = make_pipeline(
        SimpleImputer(strategy="median"), StandardScaler(),
        LogisticRegression(max_iter=2000))
    report_cv("베이스라인 · 로지스틱회귀(수치 피처만)", logreg, Xn, y, cv)

    # 3) 트리 모델 — 전체 피처(범주형 포함)
    pred = report_cv("트리(HistGradientBoosting) · 전체 피처", hgb(), X, y, cv)

    # 혼동행렬 (트리 모델, CV 예측 기준)
    cm = confusion_matrix(y, pred, labels=[0, 1, 2, 3])
    print("\n[혼동행렬] 행=실제, 열=예측  (순서: 비활성/불명/활성/강한활성)")
    head = "          " + "".join(f"{o[:4]:>8}" for o in ORDER)
    print(head)
    for i, o in enumerate(ORDER):
        print(f"  {o:>6} " + "".join(f"{cm[i][j]:>8}" for j in range(4)))

    # 피처 중요도 (순열 중요도, 단일 stratified 분할에서)
    from sklearn.model_selection import train_test_split
    Xtr, Xte, ytr, yte = train_test_split(
        X, y, test_size=0.25, stratify=y, random_state=SEED)
    m = hgb().fit(Xtr, ytr)
    imp = permutation_importance(m, Xte, yte, n_repeats=20, random_state=SEED)
    print("\n[피처 중요도] 순열 중요도 (값이 클수록 그 피처를 섞으면 정확도가 더 떨어짐)")
    order = np.argsort(imp.importances_mean)[::-1]
    cols = NUMERIC + CATEGORICAL
    for idx in order:
        mean, std = imp.importances_mean[idx], imp.importances_std[idx]
        if mean > 0.0005:
            print(f"  {cols[idx]:>18}: {mean:.4f} ± {std:.4f}")


if __name__ == "__main__":
    main()
