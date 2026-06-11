#!/usr/bin/env python3
"""Fit a first linear/Ridge model for meal glucose response and estimate ICR.

This script works with the meal-level dataset produced by build_meal_dataset.py.
It trains a simple interpretable model for post-meal glucose change, estimates a
practical insulin-to-carb ratio, and writes diagnostic Plotly charts.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from plot_my_glucose import find_dataset_dir, find_monitor_file, load_glucose_monitor, target_range


BASE_FEATURES = [
    "carbs_xe",
    "short_insulin_units",
    "glucose_at_meal",
    "glucose_slope_30m",
    "iob_units",
    "insulin_activity_units_per_hour",
    "cob_xe",
    "carb_activity_xe_per_hour",
    "meal_carb_absorbed_xe_1h",
    "meal_carb_absorbed_xe_2h",
    "meal_carb_absorbed_xe_3h",
    "bike_minutes_recent_4h",
    "hour_sin",
    "hour_cos",
    "has_fast_food",
    "has_slow_food",
]


def parse_args() -> argparse.Namespace:
    """Parse command-line options for training, reports, and plots.

    The defaults are chosen for the local project layout: the meal dataset is
    read from outputs/my_meal_model_dataset.csv and generated reports are written
    back to outputs/. Optional flags allow disabling food features, filtering
    hypoglycemia rows, changing the Ridge penalty, or opening generated HTML
    charts automatically.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Fit an interpretable Ridge model for post-meal glucose delta. "
            "The insulin/carbs coefficient ratio gives a first rough ICR estimate."
        )
    )
    parser.add_argument("--input", type=Path, default=Path("outputs/my_meal_model_dataset.csv"))
    parser.add_argument("--target", default="delta_glucose_3h")
    parser.add_argument("--alpha", type=float, default=3.0, help="Ridge penalty. Intercept is not penalized.")
    parser.add_argument("--min-carbs-xe", type=float, default=0.01)
    parser.add_argument("--drop-hypo", action="store_true", help="Drop rows with hypoglycemia in next 4h.")
    parser.add_argument("--no-food", action="store_true", help="Do not include food multi-hot columns.")
    parser.add_argument("--min-food-count", type=int, default=2, help="Only include food columns appearing at least this often.")
    parser.add_argument("--coefficients-output", type=Path, default=Path("outputs/linear_icr_coefficients.csv"))
    parser.add_argument("--report-output", type=Path, default=Path("outputs/linear_icr_report.json"))
    parser.add_argument(
        "--icr-output",
        type=Path,
        default=Path("outputs/linear_icr_practical_icr.csv"),
        help="Per-meal marginal +1 XE ICR estimates.",
    )
    parser.add_argument("--plot-output", type=Path, default=Path("outputs/linear_icr_actual_vs_predicted.html"))
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=None,
        help="Directory with source glucose monitor file. Defaults to current/parent 'My Dataset'.",
    )
    parser.add_argument(
        "--monitor-file",
        type=Path,
        default=None,
        help="Source glucose monitor file for the time-series overlay plot.",
    )
    parser.add_argument(
        "--glucose-overlay-output",
        type=Path,
        default=Path("outputs/linear_icr_glucose_overlay.html"),
        help="HTML output with predicted glucose points overlaid on real glucose curve.",
    )
    parser.add_argument("--no-glucose-overlay", action="store_true", help="Do not generate the time-series overlay plot.")
    parser.add_argument("--show-glucose-overlay", action="store_true", help="Open the time-series overlay plot.")
    parser.add_argument("--show", action="store_true", help="Open the actual-vs-predicted plot.")
    return parser.parse_args()


def selected_feature_columns(df: pd.DataFrame, include_food: bool, min_food_count: int) -> list[str]:
    """Return the model feature list that is actually available in the dataset.

    BASE_FEATURES defines the stable hand-engineered features. Food features are
    optional multi-hot columns named food_001, food_002, and so on. Rare food
    columns can easily overfit a small personal dataset, so min_food_count keeps
    only food indicators that appear in at least that many rows.
    """
    features = [column for column in BASE_FEATURES if column in df.columns]
    if include_food:
        for column in sorted(c for c in df.columns if c.startswith("food_") and c[5:].isdigit()):
            if int(df[column].fillna(0).sum()) >= min_food_count:
                features.append(column)
    return features


def prepare_training_frame(df: pd.DataFrame, target: str, min_carbs_xe: float, drop_hypo: bool) -> pd.DataFrame:
    """Filter the meal dataset down to rows suitable for model fitting.

    The target is usually a column such as delta_glucose_3h. Rows must have a
    meal with at least min_carbs_xe carbohydrates and a known short-insulin dose.
    When drop_hypo is enabled, rows with hypoglycemia in the next 4 hours are
    removed so they do not pull the regression toward intentionally lower
    post-meal glucose outcomes.
    """
    if target not in df.columns:
        raise ValueError(f"Target column {target!r} not found.")

    train = df.loc[df["carbs_xe"] >= min_carbs_xe].copy()
    if drop_hypo and "hypo_next_4h" in train.columns:
        train = train.loc[train["hypo_next_4h"] == 0].copy()
    train = train.dropna(subset=[target, "carbs_xe", "short_insulin_units"])
    if len(train) < 10:
        raise ValueError(f"Too few training rows after filtering: {len(train)}")
    return train


def standardize_matrix(x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Standardize a numeric design matrix for stable Ridge fitting.

    Ridge penalties depend on feature scale. Standardizing keeps large-unit
    features from being penalized differently than small-unit features. Missing
    values are filled with the column mean before scaling; the returned means and
    standard deviations are later used to convert coefficients back to the
    original units.
    """
    means = np.nanmean(x, axis=0)
    stds = np.nanstd(x, axis=0)
    stds[~np.isfinite(stds) | (stds == 0)] = 1.0
    x_filled = np.where(np.isfinite(x), x, means)
    return (x_filled - means) / stds, means, stds


def fit_ridge(
    x: np.ndarray,
    y: np.ndarray,
    alpha: float,
) -> tuple[np.ndarray, float, np.ndarray]:
    """Fit Ridge regression using NumPy and return coefficients in raw units.

    The model is linear:
        y = intercept + X @ coefficients

    Internally, features are standardized before solving the penalized normal
    equations. The intercept is not penalized. After fitting, coefficients are
    transformed back to the original feature units so they can be interpreted in
    mmol/L per XE, mmol/L per insulin unit, and similar practical units.
    """
    x_scaled, means, stds = standardize_matrix(x)
    design = np.column_stack([np.ones(len(x_scaled)), x_scaled])
    penalty = np.eye(design.shape[1]) * alpha
    penalty[0, 0] = 0.0
    coef_scaled = np.linalg.solve(design.T @ design + penalty, design.T @ y)

    # Convert standardized coefficients back to the original feature scale.
    intercept = coef_scaled[0] - np.sum(coef_scaled[1:] * means / stds)
    coefficients = coef_scaled[1:] / stds
    x_filled = np.where(np.isfinite(x), x, means)
    predictions = intercept + x_filled @ coefficients
    return coefficients, float(intercept), predictions


def regression_metrics(y: np.ndarray, pred: np.ndarray) -> dict[str, float]:
    """Compute basic in-sample regression quality metrics.

    MAE and RMSE are reported in the same unit as the target, typically mmol/L.
    R2 compares the model with predicting the mean target value. baseline_rmse is
    the RMSE of that mean-only baseline and helps judge whether the fitted model
    is doing better than a trivial predictor.
    """
    residuals = y - pred
    mae = float(np.mean(np.abs(residuals)))
    rmse = float(np.sqrt(np.mean(residuals**2)))
    ss_res = float(np.sum(residuals**2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = float(1.0 - ss_res / ss_tot) if ss_tot > 0 else float("nan")
    baseline_rmse = float(np.sqrt(np.mean((y - np.mean(y)) ** 2)))
    return {"mae": mae, "rmse": rmse, "r2": r2, "baseline_rmse": baseline_rmse}


def estimate_coefficient_ratio_icr(coefficients: dict[str, float]) -> dict[str, float | str | None]:
    """Compute the old direct coefficient-ratio ICR diagnostic.

    This estimate divides the carbs_xe coefficient by the negative
    short_insulin_units coefficient. It is only reliable when carbs_xe is the
    only current-meal carbohydrate feature. In this project it is intentionally
    kept as a diagnostic because Bateman-derived carb features split the
    carbohydrate effect across several correlated columns.
    """
    carb_coef = coefficients.get("carbs_xe")
    insulin_coef = coefficients.get("short_insulin_units")
    if carb_coef is None or insulin_coef is None:
        return {"status": "missing_coefficients", "insulin_units_per_xe": None, "xe_per_insulin_unit": None}
    if carb_coef <= 0 or insulin_coef >= 0:
        return {
            "status": "unexpected_signs",
            "insulin_units_per_xe": None,
            "xe_per_insulin_unit": None,
            "carbs_xe_coef_mmol_l": carb_coef,
            "short_insulin_coef_mmol_l": insulin_coef,
        }

    insulin_units_per_xe = carb_coef / (-insulin_coef)
    return {
        "status": "ok",
        "method": "single_coefficient_ratio",
        "insulin_units_per_xe": float(insulin_units_per_xe),
        "xe_per_insulin_unit": float(1.0 / insulin_units_per_xe),
        "carbs_xe_coef_mmol_l": float(carb_coef),
        "short_insulin_coef_mmol_l": float(insulin_coef),
    }


def numeric_feature_frame(frame: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    """Select feature columns and coerce them to numeric values.

    CSV inputs can preserve empty cells or mixed values as strings. Coercing
    here gives the model a clean numeric matrix and turns invalid values into
    NaN, which fit_ridge then fills with feature means.
    """
    return frame[features].apply(pd.to_numeric, errors="coerce")


def xe_grams_from_frame(frame: pd.DataFrame) -> float:
    """Infer how many carbohydrate grams one XE represents in the dataset.

    build_meal_dataset.py currently writes carbs_grams = carbs_xe * 12 by
    default. This function reads the ratio back from data when possible, falling
    back to 12 g/XE if the grams column is missing or unusable.
    """
    if "carbs_grams" not in frame.columns:
        return 12.0
    carbs_xe = pd.to_numeric(frame["carbs_xe"], errors="coerce")
    carbs_grams = pd.to_numeric(frame["carbs_grams"], errors="coerce")
    ratios = (carbs_grams / carbs_xe).replace([np.inf, -np.inf], np.nan).dropna()
    if ratios.empty:
        return 12.0
    return float(ratios.median())


def current_meal_carb_delta(
    train: pd.DataFrame,
    features: list[str],
    xe_delta: float,
) -> pd.DataFrame:
    """Build the feature change caused by adding xe_delta to the current meal.

    The practical ICR calculation asks: "If this meal had +1 XE, how much extra
    short insulin would keep the model prediction unchanged?" The raw carbs_xe
    column grows by xe_delta. Bateman current-meal absorption features also grow
    proportionally because they are deterministic fractions of the same meal
    carbohydrate amount.
    """
    delta = pd.DataFrame(0.0, index=train.index, columns=features)
    if "carbs_xe" in delta.columns:
        delta["carbs_xe"] = xe_delta
    if "carbs_grams" in delta.columns:
        delta["carbs_grams"] = xe_delta * xe_grams_from_frame(train)

    carbs_xe = pd.to_numeric(train["carbs_xe"], errors="coerce").replace(0, np.nan)
    for column in [feature for feature in features if feature.startswith("meal_carb_absorbed_xe_")]:
        # Each current-meal Bateman feature is proportional to carbs_xe, so the
        # per-XE ratio tells us how much that feature changes for +1 XE.
        ratios = (pd.to_numeric(train[column], errors="coerce") / carbs_xe).replace([np.inf, -np.inf], np.nan)
        fallback = float(ratios.dropna().median()) if not ratios.dropna().empty else 0.0
        delta[column] = xe_delta * ratios.fillna(fallback)
    return delta


def summarize_series(series: pd.Series, prefix: str) -> dict[str, float]:
    """Summarize a numeric series using stable descriptive statistics.

    The output keys are prefixed so several summaries can be merged into one
    JSON report without naming collisions. NaN and infinite values are removed
    before the mean, median, standard deviation, and p10/p90 quantiles are
    computed.
    """
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if values.empty:
        return {
            f"{prefix}_mean": float("nan"),
            f"{prefix}_median": float("nan"),
            f"{prefix}_std": float("nan"),
            f"{prefix}_p10": float("nan"),
            f"{prefix}_p90": float("nan"),
        }
    return {
        f"{prefix}_mean": float(values.mean()),
        f"{prefix}_median": float(values.median()),
        f"{prefix}_std": float(values.std(ddof=0)),
        f"{prefix}_p10": float(values.quantile(0.10)),
        f"{prefix}_p90": float(values.quantile(0.90)),
    }


def observed_dose_per_xe_summary(train: pd.DataFrame) -> dict[str, float | int]:
    """Summarize the actually recorded bolus dose per XE.

    This is not a model estimate. It is a sanity-check baseline from the diary:
    short_insulin_units / carbs_xe for rows where both are positive. Comparing
    this with the model-derived practical ICR helps detect unstable or implausible
    model behavior.
    """
    dose_rows = train.loc[(train["carbs_xe"] > 0) & (train["short_insulin_units"] > 0)].copy()
    dose_per_xe = dose_rows["short_insulin_units"] / dose_rows["carbs_xe"]
    summary: dict[str, float | int] = {
        "row_count": int(len(dose_rows)),
        "xe_grams": xe_grams_from_frame(train),
    }
    summary.update(summarize_series(dose_per_xe, "insulin_units_per_xe"))
    median = summary["insulin_units_per_xe_median"]
    summary["xe_per_insulin_unit_median"] = float(1.0 / median) if np.isfinite(median) and median > 0 else float("nan")
    summary["grams_per_insulin_unit_median"] = (
        float(summary["xe_grams"] / median) if np.isfinite(median) and median > 0 else float("nan")
    )
    return summary


def estimate_practical_icr(
    train: pd.DataFrame,
    features: list[str],
    coefficients: np.ndarray,
    coefficient_map: dict[str, float],
    output: Path,
    xe_delta: float = 1.0,
) -> dict[str, float | int | str | list[str] | None]:
    """Estimate a practical ICR by marginal simulation of +1 XE.

    For every meal row, the function creates a synthetic feature delta that adds
    xe_delta carbohydrates to the same meal context. Because the fitted model is
    linear, the predicted glucose increase from extra carbs is simply:

        carb_effect = feature_delta @ coefficients

    The extra short-insulin dose needed to offset that glucose increase is found
    by solving:

        carb_effect + extra_insulin * insulin_coef = 0

    Therefore:

        extra_insulin = -carb_effect / insulin_coef

    The per-row results are written to CSV and a robust aggregate summary is
    returned for the JSON report. This is still a research estimate, not a dosing
    recommendation.
    """
    insulin_coef = coefficient_map.get("short_insulin_units")
    if insulin_coef is None:
        return {"status": "missing_insulin_coefficient", "method": "marginal_plus_1_xe"}
    if insulin_coef >= 0:
        return {
            "status": "unexpected_insulin_sign",
            "method": "marginal_plus_1_xe",
            "short_insulin_coef_mmol_l": float(insulin_coef),
        }

    carb_delta = current_meal_carb_delta(train, features, xe_delta)
    changed_carb_features = [column for column in features if float(carb_delta[column].abs().max()) > 0]
    # Linear marginal effect of adding carbohydrates to the current meal.
    carb_effect = carb_delta.to_numpy(dtype=float) @ coefficients
    # Extra insulin that would bring the linear prediction back to the old value.
    required_insulin = -carb_effect / insulin_coef

    rows = train[["time", "carbs_xe", "short_insulin_units", "glucose_at_meal", "notes"]].copy()
    rows["carb_delta_xe"] = xe_delta
    rows["predicted_glucose_delta_from_plus_1_xe_mmol_l"] = carb_effect
    rows["extra_short_insulin_units_to_offset_plus_1_xe"] = required_insulin
    rows["short_insulin_coef_mmol_l_per_unit"] = insulin_coef
    output.parent.mkdir(parents=True, exist_ok=True)
    rows.to_csv(output, index=False)

    valid = rows.loc[
        np.isfinite(rows["extra_short_insulin_units_to_offset_plus_1_xe"])
        & np.isfinite(rows["predicted_glucose_delta_from_plus_1_xe_mmol_l"])
        & (rows["extra_short_insulin_units_to_offset_plus_1_xe"] > 0)
        & (rows["predicted_glucose_delta_from_plus_1_xe_mmol_l"] > 0)
    ]
    if valid.empty:
        return {
            "status": "no_positive_marginal_estimates",
            "method": "marginal_plus_1_xe",
            "row_count": int(len(rows)),
            "valid_row_count": 0,
            "short_insulin_coef_mmol_l_per_unit": float(insulin_coef),
            "carb_features_changed": changed_carb_features,
        }

    insulin_per_xe = valid["extra_short_insulin_units_to_offset_plus_1_xe"] / xe_delta
    glucose_delta = valid["predicted_glucose_delta_from_plus_1_xe_mmol_l"] / xe_delta
    xe_grams = xe_grams_from_frame(train)
    median_insulin = float(insulin_per_xe.median())

    summary: dict[str, float | int | str | list[str] | None] = {
        "status": "ok",
        "method": "marginal_plus_1_xe_offset",
        "row_count": int(len(rows)),
        "valid_row_count": int(len(valid)),
        "carb_delta_xe": float(xe_delta),
        "xe_grams": float(xe_grams),
        "short_insulin_coef_mmol_l_per_unit": float(insulin_coef),
        "carb_features_changed": changed_carb_features,
        "insulin_feature_changed": "short_insulin_units",
        "xe_per_insulin_unit_median": float(1.0 / median_insulin),
        "grams_per_insulin_unit_median": float(xe_grams / median_insulin),
    }
    summary.update(summarize_series(insulin_per_xe, "insulin_units_per_xe"))
    summary.update(summarize_series(glucose_delta, "predicted_glucose_delta_mmol_l_per_xe"))
    return summary


def save_actual_vs_predicted_plot(
    train: pd.DataFrame,
    target: str,
    predictions: np.ndarray,
    output: Path,
    show: bool,
) -> None:
    """Save a scatter plot comparing actual target values with predictions.

    This chart is a compact fit diagnostic. Points near the dashed diagonal are
    meals where the model predicted the target delta well. Points far from the
    diagonal show meals that may need better features, different absorption
    assumptions, or more data.
    """
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=train[target],
            y=predictions,
            mode="markers",
            text=train["time"].astype(str) + "<br>" + train["notes"].fillna(""),
            name="Meals",
            marker=dict(size=9, opacity=0.75, color=train["carbs_xe"], colorscale="Viridis", showscale=True),
            hovertemplate="actual=%{x:.2f}<br>predicted=%{y:.2f}<br>%{text}<extra></extra>",
        )
    )
    low = float(min(train[target].min(), np.min(predictions)))
    high = float(max(train[target].max(), np.max(predictions)))
    fig.add_trace(
        go.Scatter(x=[low, high], y=[low, high], mode="lines", name="ideal", line=dict(color="#111111", dash="dash"))
    )
    fig.update_layout(
        title=f"Actual vs predicted {target}",
        template="plotly_white",
        xaxis_title=f"Actual {target}, mmol/L",
        yaxis_title=f"Predicted {target}, mmol/L",
        height=680,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(output, include_plotlyjs=True, full_html=True, auto_open=show, config={"displaylogo": False})


def parse_target_horizon(target: str) -> tuple[float, str] | None:
    """Extract the forecast horizon from a target name like delta_glucose_3h.

    The returned tuple contains the numeric horizon in hours and the original
    string label used to find related columns such as glucose_plus_3h.
    """
    match = re.search(r"_(\d+(?:\.\d+)?)h$", target)
    if not match:
        return None
    return float(match.group(1)), match.group(1)


def target_prediction_time(times: pd.Series, horizon_hours: float) -> pd.Series:
    """Convert meal times into target times by adding the forecast horizon."""
    return times + pd.to_timedelta(horizon_hours, unit="h")


def predicted_glucose_values(train: pd.DataFrame, target: str, predictions: np.ndarray) -> np.ndarray:
    """Convert model target predictions into absolute glucose values.

    The model usually predicts a delta, for example delta_glucose_3h. For overlay
    plots we need absolute glucose, so the predicted delta is added to the
    measured glucose_at_meal. If the target is already absolute glucose, the
    predictions are returned unchanged.
    """
    if target.startswith("delta_glucose_"):
        return train["glucose_at_meal"].to_numpy(dtype=float) + predictions
    return predictions


def actual_glucose_values(train: pd.DataFrame, target: str, horizon_label: str) -> pd.Series:
    """Return absolute observed glucose values for the prediction horizon.

    Prefer the direct glucose_plus_Nh column from the meal dataset. If it is not
    available but the target is a delta column, reconstruct the absolute value by
    adding the observed delta to glucose_at_meal.
    """
    direct_column = f"glucose_plus_{horizon_label}h"
    if direct_column in train.columns:
        return pd.to_numeric(train[direct_column], errors="coerce")
    if target.startswith("delta_glucose_"):
        return pd.to_numeric(train["glucose_at_meal"], errors="coerce") + pd.to_numeric(train[target], errors="coerce")
    return pd.to_numeric(train[target], errors="coerce")


def save_glucose_overlay_plot(
    glucose_data: pd.DataFrame,
    glucose_unit: str,
    train: pd.DataFrame,
    target: str,
    predictions: np.ndarray,
    output: Path,
    show: bool,
) -> None:
    """Save an interactive time-series plot with real and predicted glucose.

    The plot overlays the continuous monitor glucose curve, meal/bolus markers,
    actual glucose at the target horizon, and predicted glucose at that same
    horizon. Vertical segments show the prediction error for each meal, making it
    easier to see whether errors cluster in time or around specific foods.
    """
    horizon = parse_target_horizon(target)
    if horizon is None:
        raise ValueError(f"Cannot infer forecast horizon from target column {target!r}.")
    horizon_hours, horizon_label = horizon

    plot_df = train.reset_index(drop=True).copy()
    plot_df["prediction_time"] = target_prediction_time(plot_df["time"], horizon_hours)
    plot_df["actual_glucose"] = actual_glucose_values(plot_df, target, horizon_label)
    plot_df["predicted_glucose"] = predicted_glucose_values(plot_df, target, predictions)
    plot_df["prediction_error"] = plot_df["predicted_glucose"] - plot_df["actual_glucose"]
    plot_df = plot_df.dropna(subset=["prediction_time", "actual_glucose", "predicted_glucose", "glucose_at_meal"])

    fig = go.Figure()
    low_target, high_target = target_range(glucose_unit)
    fig.add_hrect(
        y0=low_target,
        y1=high_target,
        fillcolor="rgba(46, 160, 67, 0.10)",
        line_width=0,
        layer="below",
    )
    fig.add_trace(
        go.Scatter(
            x=glucose_data["time"],
            y=glucose_data["glucose"],
            mode="lines",
            name="Реальная глюкоза",
            line=dict(color="#1f77b4", width=1.8),
            hovertemplate="%{x|%Y-%m-%d %H:%M}<br>Глюкоза: %{y:.2f} " + glucose_unit + "<extra></extra>",
        )
    )

    meal_customdata = np.column_stack(
        [
            plot_df["carbs_xe"].to_numpy(dtype=float),
            plot_df["short_insulin_units"].to_numpy(dtype=float),
            plot_df["notes"].fillna("").to_numpy(),
        ]
    )
    fig.add_trace(
        go.Scatter(
            x=plot_df["time"],
            y=plot_df["glucose_at_meal"],
            mode="markers",
            name="Прием еды / болюс",
            customdata=meal_customdata,
            marker=dict(color="#f59f00", size=9, symbol="triangle-up", line=dict(color="#111111", width=0.7)),
            hovertemplate=(
                "%{x|%Y-%m-%d %H:%M}<br>"
                "Глюкоза в момент еды: %{y:.2f} "
                + glucose_unit
                + "<br>ХЕ: %{customdata[0]:.2f}<br>"
                "Короткий инсулин: %{customdata[1]:.2f} U<br>"
                "Примечание: %{customdata[2]}<extra></extra>"
            ),
        )
    )

    predicted_customdata = np.column_stack(
        [
            plot_df["time"].dt.strftime("%Y-%m-%d %H:%M").to_numpy(),
            plot_df["carbs_xe"].to_numpy(dtype=float),
            plot_df["short_insulin_units"].to_numpy(dtype=float),
            plot_df["actual_glucose"].to_numpy(dtype=float),
            plot_df["prediction_error"].to_numpy(dtype=float),
            plot_df["notes"].fillna("").to_numpy(),
        ]
    )
    error_limit = float(np.nanmax(np.abs(plot_df["prediction_error"]))) if not plot_df.empty else 1.0
    error_limit = max(error_limit, 0.5)

    fig.add_trace(
        go.Scatter(
            x=plot_df["prediction_time"],
            y=plot_df["actual_glucose"],
            mode="markers",
            name=f"Реально через {horizon_label}h",
            customdata=predicted_customdata,
            marker=dict(color="#222222", size=8, symbol="circle-open", line=dict(width=1.6)),
            hovertemplate=(
                "%{x|%Y-%m-%d %H:%M}<br>"
                "Реально: %{y:.2f} "
                + glucose_unit
                + "<br>Еда: %{customdata[0]}<br>"
                "ХЕ: %{customdata[1]:.2f}<br>"
                "Короткий инсулин: %{customdata[2]:.2f} U<br>"
                "Примечание: %{customdata[5]}<extra></extra>"
            ),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=plot_df["prediction_time"],
            y=plot_df["predicted_glucose"],
            mode="markers",
            name=f"Прогноз через {horizon_label}h",
            customdata=predicted_customdata,
            marker=dict(
                color=plot_df["prediction_error"],
                cmin=-error_limit,
                cmax=error_limit,
                colorscale="RdBu_r",
                colorbar=dict(title="Ошибка"),
                size=10,
                symbol="diamond",
                line=dict(color="#111111", width=0.6),
            ),
            hovertemplate=(
                "%{x|%Y-%m-%d %H:%M}<br>"
                "Прогноз: %{y:.2f} "
                + glucose_unit
                + "<br>Реально: %{customdata[3]:.2f} "
                + glucose_unit
                + "<br>Ошибка: %{customdata[4]:+.2f} "
                + glucose_unit
                + "<br>Еда: %{customdata[0]}<br>"
                "ХЕ: %{customdata[1]:.2f}<br>"
                "Короткий инсулин: %{customdata[2]:.2f} U<br>"
                "Примечание: %{customdata[5]}<extra></extra>"
            ),
        )
    )

    for _, row in plot_df.iterrows():
        color = "rgba(190, 18, 60, 0.28)" if row["prediction_error"] >= 0 else "rgba(30, 64, 175, 0.28)"
        fig.add_shape(
            type="line",
            x0=row["prediction_time"],
            x1=row["prediction_time"],
            y0=row["actual_glucose"],
            y1=row["predicted_glucose"],
            line=dict(color=color, width=1),
            layer="below",
        )

    fig.add_hline(y=low_target, line=dict(color="rgba(46, 160, 67, 0.45)", width=1, dash="dot"))
    fig.add_hline(y=high_target, line=dict(color="rgba(46, 160, 67, 0.45)", width=1, dash="dot"))
    start = glucose_data["time"].min().strftime("%Y-%m-%d")
    end = glucose_data["time"].max().strftime("%Y-%m-%d")
    fig.update_layout(
        title=(
            f"Реальная глюкоза и прогноз модели на горизонте {horizon_label}h<br>"
            f"<sup>{start} to {end}; target={target}; points={len(plot_df)}</sup>"
        ),
        template="plotly_white",
        xaxis_title="Время",
        yaxis_title=f"Глюкоза, {glucose_unit}",
        height=780,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=70, r=40, t=110, b=70),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(output, include_plotlyjs=True, full_html=True, auto_open=show, config={"displaylogo": False})


def main() -> None:
    """Run the complete training, reporting, and plotting workflow.

    The workflow reads the meal dataset, filters training rows, fits Ridge,
    writes coefficients and JSON reports, saves ICR diagnostics, and generates
    Plotly HTML charts. File paths and filtering behavior are controlled through
    parse_args().
    """
    args = parse_args()
    df = pd.read_csv(args.input, parse_dates=["time"])
    train = prepare_training_frame(df, args.target, args.min_carbs_xe, drop_hypo=args.drop_hypo)
    features = selected_feature_columns(train, include_food=not args.no_food, min_food_count=args.min_food_count)
    train = train.dropna(subset=[args.target])
    x = numeric_feature_frame(train, features).to_numpy(dtype=float)
    y = train[args.target].to_numpy(dtype=float)

    coefficients, intercept, predictions = fit_ridge(x, y, args.alpha)
    metrics = regression_metrics(y, predictions)
    coefficient_map = dict(zip(features, coefficients))
    coefficient_ratio_icr = estimate_coefficient_ratio_icr(coefficient_map)
    practical_icr = estimate_practical_icr(
        train,
        features,
        coefficients,
        coefficient_map,
        args.icr_output,
    )
    observed_dose_summary = observed_dose_per_xe_summary(train)

    coef_df = pd.DataFrame(
        {
            "feature": ["intercept"] + features,
            "coefficient": [intercept] + list(coefficients),
        }
    ).sort_values("coefficient", key=lambda series: series.abs(), ascending=False)

    args.coefficients_output.parent.mkdir(parents=True, exist_ok=True)
    coef_df.to_csv(args.coefficients_output, index=False)

    report = {
        "input": str(args.input),
        "target": args.target,
        "alpha": args.alpha,
        "rows": int(len(train)),
        "features": features,
        "metrics": metrics,
        "icr_estimate": practical_icr,
        "practical_icr_estimate": practical_icr,
        "coefficient_ratio_icr_estimate": coefficient_ratio_icr,
        "observed_dose_per_xe": observed_dose_summary,
        "warnings": [
            "Research-only estimate. Do not use for real dosing decisions.",
            "Small dataset; food effects and ICR can be unstable.",
            "Practical ICR is a model marginal estimate: +1 XE at meal time offset by extra short insulin.",
        ],
    }
    args.report_output.parent.mkdir(parents=True, exist_ok=True)
    args.report_output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    save_actual_vs_predicted_plot(train, args.target, predictions, args.plot_output, args.show)
    glucose_overlay_saved = False
    if not args.no_glucose_overlay:
        try:
            dataset_dir = find_dataset_dir(args.dataset_dir)
            monitor_file = find_monitor_file(dataset_dir, args.monitor_file)
            glucose_data, glucose_unit = load_glucose_monitor(monitor_file)
            save_glucose_overlay_plot(
                glucose_data,
                glucose_unit,
                train,
                args.target,
                predictions,
                args.glucose_overlay_output,
                args.show_glucose_overlay,
            )
            glucose_overlay_saved = True
        except (FileNotFoundError, ValueError) as exc:
            print(f"Skipped glucose overlay: {exc}")

    print(f"Training rows: {len(train)}")
    print(f"Features: {len(features)}")
    print(f"MAE={metrics['mae']:.3f} mmol/L RMSE={metrics['rmse']:.3f} R2={metrics['r2']:.3f}")
    print(f"Practical ICR estimate: {json.dumps(practical_icr, ensure_ascii=False)}")
    print(f"Coefficient-ratio ICR diagnostic: {json.dumps(coefficient_ratio_icr, ensure_ascii=False)}")
    print(f"Observed dose per XE: {json.dumps(observed_dose_summary, ensure_ascii=False)}")
    print(f"Saved coefficients to {args.coefficients_output.resolve()}")
    print(f"Saved practical ICR rows to {args.icr_output.resolve()}")
    print(f"Saved report to {args.report_output.resolve()}")
    print(f"Saved plot to {args.plot_output.resolve()}")
    if glucose_overlay_saved:
        print(f"Saved glucose overlay to {args.glucose_overlay_output.resolve()}")


if __name__ == "__main__":
    main()
