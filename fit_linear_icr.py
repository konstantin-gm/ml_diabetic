#!/usr/bin/env python3
"""Fit a first linear/Ridge model for meal glucose response and estimate ICR."""

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
    features = [column for column in BASE_FEATURES if column in df.columns]
    if include_food:
        for column in sorted(c for c in df.columns if c.startswith("food_") and c[5:].isdigit()):
            if int(df[column].fillna(0).sum()) >= min_food_count:
                features.append(column)
    return features


def prepare_training_frame(df: pd.DataFrame, target: str, min_carbs_xe: float, drop_hypo: bool) -> pd.DataFrame:
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
    x_scaled, means, stds = standardize_matrix(x)
    design = np.column_stack([np.ones(len(x_scaled)), x_scaled])
    penalty = np.eye(design.shape[1]) * alpha
    penalty[0, 0] = 0.0
    coef_scaled = np.linalg.solve(design.T @ design + penalty, design.T @ y)

    intercept = coef_scaled[0] - np.sum(coef_scaled[1:] * means / stds)
    coefficients = coef_scaled[1:] / stds
    x_filled = np.where(np.isfinite(x), x, means)
    predictions = intercept + x_filled @ coefficients
    return coefficients, float(intercept), predictions


def regression_metrics(y: np.ndarray, pred: np.ndarray) -> dict[str, float]:
    residuals = y - pred
    mae = float(np.mean(np.abs(residuals)))
    rmse = float(np.sqrt(np.mean(residuals**2)))
    ss_res = float(np.sum(residuals**2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = float(1.0 - ss_res / ss_tot) if ss_tot > 0 else float("nan")
    baseline_rmse = float(np.sqrt(np.mean((y - np.mean(y)) ** 2)))
    return {"mae": mae, "rmse": rmse, "r2": r2, "baseline_rmse": baseline_rmse}


def estimate_icr(coefficients: dict[str, float]) -> dict[str, float | str | None]:
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
        "insulin_units_per_xe": float(insulin_units_per_xe),
        "xe_per_insulin_unit": float(1.0 / insulin_units_per_xe),
        "carbs_xe_coef_mmol_l": float(carb_coef),
        "short_insulin_coef_mmol_l": float(insulin_coef),
    }


def save_actual_vs_predicted_plot(
    train: pd.DataFrame,
    target: str,
    predictions: np.ndarray,
    output: Path,
    show: bool,
) -> None:
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
    match = re.search(r"_(\d+(?:\.\d+)?)h$", target)
    if not match:
        return None
    return float(match.group(1)), match.group(1)


def target_prediction_time(times: pd.Series, horizon_hours: float) -> pd.Series:
    return times + pd.to_timedelta(horizon_hours, unit="h")


def predicted_glucose_values(train: pd.DataFrame, target: str, predictions: np.ndarray) -> np.ndarray:
    if target.startswith("delta_glucose_"):
        return train["glucose_at_meal"].to_numpy(dtype=float) + predictions
    return predictions


def actual_glucose_values(train: pd.DataFrame, target: str, horizon_label: str) -> pd.Series:
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
    args = parse_args()
    df = pd.read_csv(args.input, parse_dates=["time"])
    train = prepare_training_frame(df, args.target, args.min_carbs_xe, drop_hypo=args.drop_hypo)
    features = selected_feature_columns(train, include_food=not args.no_food, min_food_count=args.min_food_count)
    train = train.dropna(subset=[args.target])
    x = train[features].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    y = train[args.target].to_numpy(dtype=float)

    coefficients, intercept, predictions = fit_ridge(x, y, args.alpha)
    metrics = regression_metrics(y, predictions)
    coefficient_map = dict(zip(features, coefficients))
    icr = estimate_icr(coefficient_map)

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
        "icr_estimate": icr,
        "warnings": [
            "Research-only estimate. Do not use for real dosing decisions.",
            "Small dataset; food effects and ICR can be unstable.",
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
    print(f"ICR estimate: {json.dumps(icr, ensure_ascii=False)}")
    print(f"Saved coefficients to {args.coefficients_output.resolve()}")
    print(f"Saved report to {args.report_output.resolve()}")
    print(f"Saved plot to {args.plot_output.resolve()}")
    if glucose_overlay_saved:
        print(f"Saved glucose overlay to {args.glucose_overlay_output.resolve()}")


if __name__ == "__main__":
    main()
