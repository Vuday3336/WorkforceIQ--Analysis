"""
WorkforceIQ - attrition risk model.

Trains two classifiers on the historical attrition label, compares them, and
scores every currently-active employee into attrition_risk_scores so the
Power BI Watchlist page and the web dashboard have something to read.

MODEL CHOICE
    Logistic Regression is the primary model on purpose. When HR tells a
    manager that a named person is a flight risk, the very next question is
    "why?", and a coefficient answers that in one sentence. A Random Forest
    is trained alongside purely as a check that the linear model is not
    leaving accuracy on the table -- if the forest wins by a wide margin the
    linear form is wrong, and if it does not, interpretability is free.

CLASS IMBALANCE
    Only 16.1% of employees left, so accuracy is a useless metric here --
    predicting "nobody leaves" scores 83.9%. Both models therefore use
    balanced class weights, and everything is judged on recall and PR-AUC.
    For a retention watchlist recall matters more than precision: a
    false positive costs a manager one coffee conversation, a false negative
    costs a replacement hire.

KNOWN LIMITATION (stated rather than hidden)
    tenure_years is measured at termination for leavers and at the snapshot
    date for active employees, because that is how the source encodes
    YearsAtCompany. That is the correct framing for "what did tenure look
    like when they left", but it means the model sees a slightly different
    tenure distribution for the two classes. In production this would be
    replaced by a proper point-in-time feature store snapshotting every
    employee at a fixed lookback date.

    python generators/train_attrition_model.py
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    classification_report,
    confusion_matrix,
    precision_recall_curve,
    roc_auc_score,
    roc_curve,
)
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent))
from feature_store import (  # noqa: E402
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    load_features,
)

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
CHARTS = DOCS / "charts"
PROCESSED = ROOT / "data" / "processed"

SCORED_DATE = date(2025, 12, 31)
RANDOM_STATE = 42

# Watchlist tiers are quantiles of the ACTIVE population, not absolute
# probability cutoffs. A fixed 0.5 threshold under balanced class weights
# would flag several hundred people, which no HR team can action. The top
# decile is a list a business partner can actually work through in a month.
HIGH_RISK_QUANTILE = 0.90
MEDIUM_RISK_QUANTILE = 0.70

plt.rcParams.update({
    "figure.dpi": 130, "font.size": 9, "axes.spines.top": False,
    "axes.spines.right": False, "axes.grid": True, "grid.alpha": 0.25,
})
INK = "#1f2937"
ACCENT = "#2563eb"
WARN = "#dc2626"


def make_preprocessor() -> ColumnTransformer:
    numeric = Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
    ])
    categorical = Pipeline([
        ("impute", SimpleImputer(strategy="most_frequent")),
        ("onehot", OneHotEncoder(handle_unknown="ignore", drop="first",
                                 sparse_output=False)),
    ])
    return ColumnTransformer([
        ("num", numeric, NUMERIC_FEATURES),
        ("cat", categorical, CATEGORICAL_FEATURES),
    ])


def evaluate(name, pipe, X_te, y_te) -> dict:
    proba = pipe.predict_proba(X_te)[:, 1]
    pred = (proba >= 0.5).astype(int)
    rep = classification_report(y_te, pred, output_dict=True, zero_division=0)
    cm = confusion_matrix(y_te, pred)
    tn, fp, fn, tp = cm.ravel()
    out = {
        "model": name,
        "roc_auc": round(float(roc_auc_score(y_te, proba)), 4),
        "pr_auc": round(float(average_precision_score(y_te, proba)), 4),
        "precision_leavers": round(float(rep["1"]["precision"]), 4),
        "recall_leavers": round(float(rep["1"]["recall"]), 4),
        "f1_leavers": round(float(rep["1"]["f1-score"]), 4),
        "accuracy": round(float(rep["accuracy"]), 4),
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }
    print("\n--- " + name + " ---")
    print(classification_report(y_te, pred, target_names=["Stayed", "Left"],
                               zero_division=0))
    print("ROC-AUC " + str(out["roc_auc"]) + "   PR-AUC " + str(out["pr_auc"]))
    print("confusion matrix [[tn fp][fn tp]]:\n" + str(cm))
    return out, proba


def feature_names(pipe) -> list[str]:
    pre = pipe.named_steps["pre"]
    cat = pre.named_transformers_["cat"].named_steps["onehot"]
    return NUMERIC_FEATURES + list(cat.get_feature_names_out(CATEGORICAL_FEATURES))


def main() -> None:
    CHARTS.mkdir(parents=True, exist_ok=True)
    df = load_features()

    X = df[NUMERIC_FEATURES + CATEGORICAL_FEATURES]
    y = df["attrition_flag"].astype(int)

    print("rows " + str(len(df)) + "   leavers " + str(int(y.sum()))
          + "   base rate " + str(round(float(y.mean()), 4)))

    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=0.25, stratify=y, random_state=RANDOM_STATE
    )
    print("train " + str(len(X_tr)) + "   test " + str(len(X_te))
          + "   test leavers " + str(int(y_te.sum())))

    logreg = Pipeline([
        ("pre", make_preprocessor()),
        ("clf", LogisticRegression(max_iter=3000, class_weight="balanced",
                                   C=0.5, random_state=RANDOM_STATE)),
    ])
    forest = Pipeline([
        ("pre", make_preprocessor()),
        ("clf", RandomForestClassifier(
            n_estimators=500, min_samples_leaf=3, max_features="sqrt",
            class_weight="balanced_subsample", n_jobs=-1,
            random_state=RANDOM_STATE)),
    ])

    logreg.fit(X_tr, y_tr)
    forest.fit(X_tr, y_tr)

    lr_metrics, lr_proba = evaluate("Logistic Regression", logreg, X_te, y_te)
    rf_metrics, rf_proba = evaluate("Random Forest", forest, X_te, y_te)

    # cross-validated PR-AUC on the full data, so the comparison does not
    # hinge on one lucky split
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=RANDOM_STATE)
    for name, pipe, metrics in [("Logistic Regression", logreg, lr_metrics),
                                ("Random Forest", forest, rf_metrics)]:
        scores = cross_val_score(pipe, X, y, cv=cv, scoring="average_precision")
        metrics["cv_pr_auc_mean"] = round(float(scores.mean()), 4)
        metrics["cv_pr_auc_std"] = round(float(scores.std()), 4)
        print(name + " 5-fold PR-AUC " + str(metrics["cv_pr_auc_mean"])
              + " +/- " + str(metrics["cv_pr_auc_std"]))

    winner = ("Logistic Regression" if lr_metrics["cv_pr_auc_mean"]
              >= rf_metrics["cv_pr_auc_mean"] else "Random Forest")
    print("\nbetter cross-validated PR-AUC: " + winner)

    # ---------------------------------------------------------------- coefficients
    names = feature_names(logreg)
    coefs = logreg.named_steps["clf"].coef_[0]
    coef_df = (pd.DataFrame({"feature": names, "coefficient": coefs})
               .assign(odds_ratio=lambda d: np.exp(d.coefficient),
                       abs_coef=lambda d: d.coefficient.abs())
               .sort_values("abs_coef", ascending=False)
               .reset_index(drop=True))
    print("\nTop 12 logistic-regression coefficients (standardised features):")
    print(coef_df.head(12)[["feature", "coefficient", "odds_ratio"]]
          .round(3).to_string(index=False))

    rf_imp = (pd.DataFrame({
        "feature": names,
        "importance": forest.named_steps["clf"].feature_importances_})
        .sort_values("importance", ascending=False).reset_index(drop=True))

    perm = permutation_importance(logreg, X_te, y_te, n_repeats=15,
                                  random_state=RANDOM_STATE,
                                  scoring="average_precision", n_jobs=-1)
    perm_df = (pd.DataFrame({
        "feature": X_te.columns,
        "importance": perm.importances_mean,
        "std": perm.importances_std})
        .sort_values("importance", ascending=False).reset_index(drop=True))
    print("\nPermutation importance (drop in PR-AUC when shuffled), top 10:")
    print(perm_df.head(10).round(4).to_string(index=False))

    # ---------------------------------------------------------------- charts
    top = coef_df.head(14).iloc[::-1]
    fig, ax = plt.subplots(figsize=(7.2, 5))
    ax.barh(top.feature, top.coefficient,
            color=[WARN if c > 0 else ACCENT for c in top.coefficient])
    ax.axvline(0, color=INK, lw=0.8)
    ax.set_xlabel("Logistic regression coefficient (standardised)")
    ax.set_title("What drives attrition at Everline Corp\n"
                 "red = raises risk, blue = lowers risk", loc="left")
    fig.tight_layout()
    fig.savefig(CHARTS / "feature_importance.png", bbox_inches="tight")
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(9, 3.8))
    for ax, (nm, proba) in zip(axes, [("Logistic Regression", lr_proba),
                                      ("Random Forest", rf_proba)]):
        fpr, tpr, _ = roc_curve(y_te, proba)
        ax.plot(fpr, tpr, color=ACCENT, lw=2,
                label=nm + " (AUC " + str(round(roc_auc_score(y_te, proba), 3)) + ")")
        ax.plot([0, 1], [0, 1], "--", color="#9ca3af", lw=1)
        ax.set_xlabel("False positive rate"); ax.set_ylabel("True positive rate")
        ax.set_title(nm, loc="left"); ax.legend(loc="lower right", fontsize=8)
    fig.suptitle("ROC curves on the held-out 25% test set", x=0.02, ha="left")
    fig.tight_layout()
    fig.savefig(CHARTS / "roc_curves.png", bbox_inches="tight")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(5.2, 3.8))
    for nm, proba, col in [("Logistic Regression", lr_proba, ACCENT),
                           ("Random Forest", rf_proba, WARN)]:
        pr, rc, _ = precision_recall_curve(y_te, proba)
        ax.plot(rc, pr, color=col, lw=2,
                label=nm + " (AP " + str(round(average_precision_score(y_te, proba), 3)) + ")")
    ax.axhline(float(y_te.mean()), ls="--", color="#9ca3af", lw=1,
               label="base rate " + str(round(float(y_te.mean()), 3)))
    ax.set_xlabel("Recall"); ax.set_ylabel("Precision")
    ax.set_title("Precision-recall - the metric that matters\nwith a 16% base rate",
                 loc="left")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(CHARTS / "precision_recall.png", bbox_inches="tight")
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(7.5, 3.4))
    for ax, (nm, m) in zip(axes, [("Logistic Regression", lr_metrics),
                                  ("Random Forest", rf_metrics)]):
        c = m["confusion_matrix"]
        mat = np.array([[c["tn"], c["fp"]], [c["fn"], c["tp"]]])
        ax.imshow(mat, cmap="Blues")
        for (i, j), v in np.ndenumerate(mat):
            ax.text(j, i, str(v), ha="center", va="center",
                    color="white" if v > mat.max() / 2 else INK, fontsize=12)
        ax.set_xticks([0, 1], ["pred stay", "pred leave"])
        ax.set_yticks([0, 1], ["actually stayed", "actually left"])
        ax.set_title(nm + "  (recall " + str(m["recall_leavers"]) + ")", loc="left")
        ax.grid(False)
    fig.tight_layout()
    fig.savefig(CHARTS / "confusion_matrix.png", bbox_inches="tight")
    plt.close(fig)
    print("\nwrote 4 charts to " + str(CHARTS))

    # ---------------------------------------------------------------- scoring
    # Refit on ALL history before scoring: the metrics above come from the
    # held-out split, but the model that goes to production should see every
    # labelled example available.
    logreg.fit(X, y)
    forest.fit(X, y)

    active = df[df.current_status == "Active"].copy()
    X_active = active[NUMERIC_FEATURES + CATEGORICAL_FEATURES]

    rows = []
    for model_name, pipe in [("logistic_regression", logreg),
                             ("random_forest", forest)]:
        scores = pipe.predict_proba(X_active)[:, 1]
        hi = float(np.quantile(scores, HIGH_RISK_QUANTILE))
        med = float(np.quantile(scores, MEDIUM_RISK_QUANTILE))
        tiers = np.where(scores >= hi, "High",
                         np.where(scores >= med, "Medium", "Low"))
        rows.append(pd.DataFrame({
            "employee_id": active.employee_id.values,
            "scored_date": SCORED_DATE,
            "risk_score": np.round(scores, 5),
            "risk_tier": tiers,
            "model_name": model_name,
        }))
        print(model_name + ": High>=" + str(round(hi, 3))
              + "  Medium>=" + str(round(med, 3))
              + "  High tier n=" + str(int((tiers == "High").sum())))

    scored = pd.concat(rows, ignore_index=True)
    scored.to_csv(PROCESSED / "attrition_risk_scores.csv", index=False)
    print("wrote " + str(len(scored)) + " rows to attrition_risk_scores.csv")

    # ---------------------------------------------------------------- heuristic
    # The explainable fallback the DAX measure mirrors, so the report can put
    # "what a human would guess" next to "what the model says".
    a = active
    heuristic = (
        2.0 * (a.overtime == 1)
        + 1.5 * (a.job_satisfaction <= 2)
        + 1.0 * (a.work_life_balance <= 2)
        + 1.5 * (a.income_pct_rank_in_role <= 0.25)
        + 1.0 * ((a.tenure_years >= 1) & (a.tenure_years <= 3))
        + 0.5 * (a.distance_from_home >= 20)
    ) / 7.5
    lr_scores = scored[scored.model_name == "logistic_regression"].set_index("employee_id")
    aligned = lr_scores.loc[a.employee_id, "risk_score"].astype(float).values
    corr = float(np.corrcoef(heuristic.fillna(0).values, aligned)[0, 1])
    top_h = set(a.employee_id[heuristic >= heuristic.quantile(0.90)])
    top_m = set(lr_scores[lr_scores.risk_tier == "High"].index)
    overlap = len(top_h & top_m) / max(len(top_m), 1)
    print("\nrule-based heuristic vs model: r=" + str(round(corr, 3))
          + ", top-decile overlap=" + str(round(overlap, 3)))

    # ---------------------------------------------------------------- results file
    results = {
        "generated_for_snapshot": str(SCORED_DATE),
        "rows": int(len(df)),
        "leavers": int(y.sum()),
        "base_rate": round(float(y.mean()), 4),
        "test_set_size": int(len(X_te)),
        "models": [lr_metrics, rf_metrics],
        "better_cv_pr_auc": winner,
        "top_coefficients": coef_df.head(15)[
            ["feature", "coefficient", "odds_ratio"]].round(4).to_dict("records"),
        "random_forest_top_importances": rf_imp.head(15).round(4).to_dict("records"),
        "permutation_importance": perm_df.head(12).round(4).to_dict("records"),
        "heuristic_vs_model": {
            "pearson_r": round(corr, 4),
            "top_decile_overlap": round(overlap, 4),
        },
        "tier_thresholds": {
            "high_quantile": HIGH_RISK_QUANTILE,
            "medium_quantile": MEDIUM_RISK_QUANTILE,
        },
    }
    (DOCS / "model_results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8")
    print("wrote docs/model_results.json")


if __name__ == "__main__":
    main()
