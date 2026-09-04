"""Train the individual behavioral-risk XGBoost model."""

from __future__ import annotations

from typing import Any, Iterable

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
)
from sklearn.model_selection import train_test_split
from xgboost import XGBClassifier


RANDOM_STATE = 42
POPULATIONS_WITH_BEHAVIORAL_RISK = {"individual_abuser", "coordinated_ring"}


def _feature_names(feature_table: list[dict[str, Any]]) -> list[str]:
    if not feature_table:
        raise ValueError("feature_table must contain at least one customer")
    if not (names := [name for name in feature_table[0] if name != "customer_id"]):
        raise ValueError("feature_table must contain numeric feature columns")
    return names


def _customer_id(customer: Any) -> str:
    if isinstance(customer, dict):
        return str(customer.get("id", customer.get("customer_id", "")))
    return str(getattr(customer, "id", getattr(customer, "customer_id", "")))


def _population(customer: Any) -> str:
    if isinstance(customer, dict):
        return str(customer.get("population", ""))
    return str(getattr(customer, "population", ""))


def train_model(
    feature_table: Iterable[dict[str, Any]],
    customers: Iterable[Any],
) -> tuple[XGBClassifier, list[dict[str, float | str]], dict[str, float], list[dict[str, float]], dict[str, float]]:
    """Train and score the individual behavioral-risk model.

    Returns the fitted model, a score for every customer, PR-optimized test
    metrics, a downsampled precision-recall curve, and sorted importances.
    Graph membership is intentionally not used as a feature or label source;
    the label is the behavioral population only.
    """
    feature_rows = list(feature_table)
    customer_rows = list(customers)
    if len(feature_rows) != len(customer_rows):
        raise ValueError("feature_table and customers must have the same length")

    names = _feature_names(feature_rows)
    customer_ids = [str(row.get("customer_id", "")) for row in feature_rows]
    labels_by_id = {
        _customer_id(customer): int(_population(customer) in POPULATIONS_WITH_BEHAVIORAL_RISK)
        for customer in customer_rows
    }
    if missing_labels := [customer_id for customer_id in customer_ids if customer_id not in labels_by_id]:
        raise ValueError(f"Missing customer labels for: {missing_labels[:3]}")

    matrix = np.asarray([[float(row.get(name, 0.0)) for name in names] for row in feature_rows], dtype=float)
    labels = np.asarray([labels_by_id[customer_id] for customer_id in customer_ids], dtype=int)
    if len(np.unique(labels)) < 2:
        raise ValueError("training requires both behavioral-risk classes")

    train_indices, test_indices = train_test_split(
        np.arange(len(labels)),
        test_size=0.25,
        random_state=RANDOM_STATE,
        stratify=labels,
    )
    model = XGBClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.85,
        colsample_bytree=0.85,
        objective="binary:logistic",
        eval_metric="logloss",
        random_state=RANDOM_STATE,
        n_jobs=1,
    )
    model.fit(matrix[train_indices], labels[train_indices])
    test_scores = model.predict_proba(matrix[test_indices])[:, 1]
    test_predictions = (test_scores >= 0.5).astype(int)

    # Accuracy is intentionally excluded: abuse is rare, so predicting every
    # customer as fine can score high accuracy while being useless.
    precision, recall, thresholds = precision_recall_curve(labels[test_indices], test_scores)
    metrics = {
        "precision": float(precision_score(labels[test_indices], test_predictions, zero_division=0)),
        "recall": float(recall_score(labels[test_indices], test_predictions, zero_division=0)),
        "f1": float(f1_score(labels[test_indices], test_predictions, zero_division=0)),
        "pr_auc": float(average_precision_score(labels[test_indices], test_scores)),
    }
    curve_size = min(100, len(precision))
    curve_indices = np.linspace(0, len(precision) - 1, curve_size, dtype=int)
    pr_curve = [
        {
            "precision": float(precision[index]),
            "recall": float(recall[index]),
            "threshold": float(thresholds[index]) if index < len(thresholds) else 1.0,
        }
        for index in curve_indices
    ]

    all_scores = model.predict_proba(matrix)[:, 1]
    scores = [
        {"customer_id": customer_id, "risk_score": float(score)}
        for customer_id, score in zip(customer_ids, all_scores)
    ]
    importances = {
        name: float(importance)
        for name, importance in sorted(
            zip(names, model.feature_importances_), key=lambda item: item[1], reverse=True
        )
    }
    return model, scores, metrics, pr_curve, importances
