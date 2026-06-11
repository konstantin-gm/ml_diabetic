#!/usr/bin/env python3
"""Build a meal-level modeling dataset from personal glucose and event logs.

The output CSV is the main input for fit_linear_icr.py. Each row represents one
meal and contains measured glucose context, food/insulin diary fields, engineered
IOB/COB features, Bateman current-meal absorption features, and future glucose
targets at several horizons.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd

from plot_my_glucose import (
    add_glucose_at_event,
    build_food_vocabulary,
    find_dataset_dir,
    find_events_file,
    find_monitor_file,
    infer_events_year,
    load_food_insulin_events,
    load_glucose_monitor,
    save_events_dataset,
    save_food_features,
)


DEFAULT_HORIZONS_HOURS = (1, 2, 3, 4)
DEFAULT_FAST_FOOD_TOKENS = {"гипофри", "конфета", "кола", "квас"}
DEFAULT_SLOW_FOOD_TOKENS = {"пицца", "шаурма", "картофель", "макароны", "пиво"}


def parse_args() -> argparse.Namespace:
    """Parse command-line options for dataset generation.

    The defaults match the local project structure and the assumptions used in
    the first modeling iteration: 12 g per XE, 5-hour rapid-insulin duration,
    75-minute insulin peak, and a 4-hour carbohydrate absorption window. These
    values are empirical modeling parameters, not medical device settings.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Create meal-level features for insulin/carb modeling: glucose outcomes, "
            "insulin-on-board, carb-on-board, food vectors, and activity flags."
        )
    )
    parser.add_argument("--dataset-dir", type=Path, default=None)
    parser.add_argument("--monitor-file", type=Path, default=None)
    parser.add_argument("--events-file", type=Path, default=None)
    parser.add_argument("--events-year", type=int, default=None)
    parser.add_argument("--events-output", type=Path, default=Path("outputs/my_events_dataset.csv"))
    parser.add_argument("--food-vocab-output", type=Path, default=Path("outputs/my_food_vocabulary.csv"))
    parser.add_argument("--vectorized-events-output", type=Path, default=Path("outputs/my_events_vectorized.csv"))
    parser.add_argument("--output", type=Path, default=Path("outputs/my_meal_model_dataset.csv"))
    parser.add_argument("--min-carbs-xe", type=float, default=0.01, help="Meal rows must have at least this many XE.")
    parser.add_argument("--xe-grams", type=float, default=12.0, help="Carbohydrate grams per XE.")
    parser.add_argument("--target-glucose", type=float, default=6.0, help="Target glucose in mmol/L.")
    parser.add_argument("--insulin-dia-hours", type=float, default=5.0, help="Duration of rapid insulin activity.")
    parser.add_argument("--insulin-peak-minutes", type=float, default=75.0, help="Peak rapid insulin activity.")
    parser.add_argument("--carb-duration-hours", type=float, default=4.0, help="Duration of modeled carb absorption.")
    parser.add_argument("--carb-peak-minutes", type=float, default=70.0, help="Default Bateman carb response peak.")
    parser.add_argument("--pre-window-minutes", type=float, default=30.0)
    parser.add_argument("--activity-window-hours", type=float, default=4.0)
    return parser.parse_args()


def time_to_minutes(series: pd.Series) -> np.ndarray:
    """Convert pandas datetimes to floating-point minutes.

    NumPy interpolation works on numeric arrays, so timestamps are converted from
    nanoseconds since epoch to minutes. Absolute minute values are large, but
    interpolation is stable because both source and query times use the same
    scale.
    """
    return series.astype("int64").to_numpy(dtype=float) / 60_000_000_000


def interpolate_glucose(glucose_data: pd.DataFrame, times: pd.Series) -> np.ndarray:
    """Interpolate monitor glucose at arbitrary event or target times.

    Diary events rarely fall exactly on monitor sample timestamps. Linear
    interpolation estimates glucose at those event times using the surrounding
    monitor readings. Queries outside the monitor range return NaN.
    """
    glucose_minutes = time_to_minutes(glucose_data["time"])
    values = glucose_data["glucose"].to_numpy(dtype=float)
    query_minutes = time_to_minutes(times)
    return np.interp(query_minutes, glucose_minutes, values, left=np.nan, right=np.nan)


def glucose_window(glucose_data: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    """Return all monitor glucose values inside a closed time window.

    This is used for pre-meal context statistics and for future min/max glucose
    features after a meal. The function returns only the glucose Series, keeping
    downstream calculations simple.
    """
    mask = (glucose_data["time"] >= start) & (glucose_data["time"] <= end)
    return glucose_data.loc[mask, "glucose"]


def gamma_activity_raw(minutes: np.ndarray, peak_minutes: float, duration_minutes: float) -> np.ndarray:
    """Create an unnormalized rapid-insulin activity shape.

    The curve is an empirical gamma-like shape: activity starts at zero, rises
    toward a peak controlled by peak_minutes, and is clipped to zero after the
    assumed duration. It is normalized later so that the total area equals one
    unit of insulin action.
    """
    minutes = np.asarray(minutes, dtype=float)
    raw = np.zeros_like(minutes)
    mask = (minutes > 0) & (minutes < duration_minutes)
    raw[mask] = minutes[mask] * np.exp(1.0 - minutes[mask] / peak_minutes)
    return raw


def normalized_curve_grid(duration_minutes: float, step_minutes: float = 1.0) -> np.ndarray:
    """Create the minute grid used to tabulate activity and absorption curves."""
    return np.arange(0, duration_minutes + step_minutes, step_minutes, dtype=float)


def insulin_activity_curve(
    duration_minutes: float,
    peak_minutes: float,
    step_minutes: float = 1.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return normalized insulin activity and remaining IOB fractions.

    The activity curve is normalized so its integral over time equals one. If a
    previous bolus dose was D units, D * activity(age) is the instantaneous
    activity contribution at that age, and D * iob_fraction(age) is the estimated
    insulin still on board. This is an empirical approximation rather than the
    exact proprietary Medtronic formula.
    """
    grid = normalized_curve_grid(duration_minutes, step_minutes)
    raw = gamma_activity_raw(grid, peak_minutes, duration_minutes)
    area = np.trapezoid(raw, grid)
    activity = raw / area if area > 0 else raw
    # Cumulative area gives delivered action; one minus it is remaining IOB.
    cumulative = np.array([np.trapezoid(activity[: idx + 1], grid[: idx + 1]) for idx in range(len(grid))])
    iob_fraction = np.clip(1.0 - cumulative, 0.0, 1.0)
    return grid, activity, iob_fraction


def bateman_rate_raw(minutes: np.ndarray, peak_minutes: float, duration_minutes: float) -> np.ndarray:
    """Create an unnormalized two-compartment carbohydrate absorption rate.

    A Bateman-like response is the difference between two exponentials. It gives
    a delayed rise and gradual fall, which is a better first approximation for
    carbohydrate appearance than an immediate step function. The chosen constants
    prioritize numerical stability and an approximate requested peak.
    """
    minutes = np.asarray(minutes, dtype=float)
    rate = np.zeros_like(minutes)
    mask = (minutes > 0) & (minutes < duration_minutes)
    # Choose a stable two-compartment shape with an approximate requested peak.
    k_elim = 1.0 / max(duration_minutes, 1.0)
    k_abs = max(k_elim * 1.1, 1.0 / max(peak_minutes, 1.0))
    rate[mask] = np.exp(-k_elim * minutes[mask]) - np.exp(-k_abs * minutes[mask])
    return np.maximum(rate, 0.0)


def bateman_curve(
    duration_minutes: float,
    peak_minutes: float,
    step_minutes: float = 1.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return normalized carbohydrate absorption rate and remaining COB.

    The rate curve integrates to one XE of absorption over the configured
    duration. The remaining curve is one minus cumulative absorption and is used
    to estimate carbohydrates still on board from earlier meals.
    """
    grid = normalized_curve_grid(duration_minutes, step_minutes)
    raw = bateman_rate_raw(grid, peak_minutes, duration_minutes)
    area = np.trapezoid(raw, grid)
    rate = raw / area if area > 0 else raw
    # Cumulative absorbed fraction converts the rate curve into remaining COB.
    cumulative = np.array([np.trapezoid(rate[: idx + 1], grid[: idx + 1]) for idx in range(len(grid))])
    remaining = np.clip(1.0 - cumulative, 0.0, 1.0)
    return grid, rate, remaining


def interp_curve(grid: np.ndarray, values: np.ndarray, ages_minutes: np.ndarray) -> np.ndarray:
    """Evaluate a precomputed curve at event ages in minutes.

    Values before the curve starts and after it ends are treated as zero. This
    means old meals or boluses outside the configured duration no longer
    contribute to COB/IOB features.
    """
    return np.interp(ages_minutes, grid, values, left=0.0, right=0.0)


def activity_minutes_from_note(note: object) -> float:
    """Extract bicycle activity duration from a diary note.

    The current diary parser only recognizes notes containing "велосипед" and a
    duration like "30 мин". Unknown activity descriptions are treated as zero
    minutes so the feature is conservative.
    """
    if note is None or pd.isna(note):
        return 0.0
    text = str(note).lower()
    if "велосипед" not in text:
        return 0.0
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*мин", text)
    if not match:
        return 0.0
    return float(match.group(1).replace(",", "."))


def food_speed_flags(food_names: str) -> tuple[int, int]:
    """Mark meals containing manually defined fast or slow food tokens.

    These flags are intentionally simple domain features. They let the linear
    model distinguish obvious fast carbs such as cola or candy from slower mixed
    meals such as pizza or potatoes without needing many observations per exact
    food item.
    """
    tokens = set(str(food_names).split("|")) if pd.notna(food_names) else set()
    return int(bool(tokens & DEFAULT_FAST_FOOD_TOKENS)), int(bool(tokens & DEFAULT_SLOW_FOOD_TOKENS))


def iob_features_for_time(
    event_time: pd.Timestamp,
    all_events: pd.DataFrame,
    insulin_grid: np.ndarray,
    insulin_activity: np.ndarray,
    iob_fraction: np.ndarray,
) -> tuple[float, float]:
    """Calculate insulin-on-board features from previous short-insulin events.

    For each prior bolus, the function computes its age at event_time and looks
    up both remaining IOB fraction and current activity. Contributions are dose
    weighted and summed across all still-active previous boluses.
    """
    previous = all_events.loc[(all_events["time"] < event_time) & (all_events["short_insulin_units"] > 0)]
    if previous.empty:
        return 0.0, 0.0

    ages = (event_time - previous["time"]).dt.total_seconds().to_numpy() / 60
    doses = previous["short_insulin_units"].to_numpy(dtype=float)
    iob_units = float(np.sum(doses * interp_curve(insulin_grid, iob_fraction, ages)))
    activity_units_per_hour = float(np.sum(doses * interp_curve(insulin_grid, insulin_activity, ages)) * 60)
    return iob_units, activity_units_per_hour


def cob_features_for_time(
    event_time: pd.Timestamp,
    all_events: pd.DataFrame,
    carb_grid: np.ndarray,
    carb_rate: np.ndarray,
    carb_remaining: np.ndarray,
) -> tuple[float, float]:
    """Calculate carbohydrate-on-board features from previous meals.

    This mirrors iob_features_for_time but uses carbohydrate amounts and the
    Bateman absorption curve. cob_xe estimates unabsorbed carbohydrates from
    previous meals, while carb_activity_xe_per_hour estimates the current
    absorption rate.
    """
    previous = all_events.loc[(all_events["time"] < event_time) & (all_events["carbs_xe"] > 0)]
    if previous.empty:
        return 0.0, 0.0

    ages = (event_time - previous["time"]).dt.total_seconds().to_numpy() / 60
    carbs = previous["carbs_xe"].to_numpy(dtype=float)
    cob_xe = float(np.sum(carbs * interp_curve(carb_grid, carb_remaining, ages)))
    carb_activity_xe_per_hour = float(np.sum(carbs * interp_curve(carb_grid, carb_rate, ages)) * 60)
    return cob_xe, carb_activity_xe_per_hour


def recent_activity_features(event_time: pd.Timestamp, all_events: pd.DataFrame, window_hours: float) -> tuple[int, float]:
    """Summarize recent exercise before a meal.

    The current feature set uses a binary flag and total bicycle minutes inside
    a lookback window. Activity after the meal is not included here because the
    goal is to predict dose from information known at bolus time.
    """
    start = event_time - pd.Timedelta(hours=window_hours)
    recent = all_events.loc[(all_events["time"] < event_time) & (all_events["time"] >= start)]
    minutes = float(recent["activity_minutes"].sum()) if "activity_minutes" in recent else 0.0
    return int(minutes > 0), minutes


def current_meal_bateman_areas(
    carbs_xe: float,
    carb_grid: np.ndarray,
    carb_cumulative: np.ndarray,
    horizons_hours: tuple[int, ...],
) -> dict[str, float]:
    """Compute how much of the current meal is modeled as absorbed by each horizon.

    For a meal with C XE, the value at horizon H is C multiplied by the Bateman
    cumulative absorbed fraction at H hours. These features let the linear model
    learn horizon-dependent carbohydrate effects without recomputing the curve
    during model fitting.
    """
    values: dict[str, float] = {}
    for horizon in horizons_hours:
        fraction = float(np.interp(horizon * 60, carb_grid, carb_cumulative, left=0.0, right=1.0))
        values[f"meal_carb_absorbed_xe_{horizon}h"] = carbs_xe * fraction
    return values


def build_meal_dataset(
    glucose_data: pd.DataFrame,
    events: pd.DataFrame,
    vectorized_events: pd.DataFrame,
    args: argparse.Namespace,
) -> pd.DataFrame:
    """Build one modeling row per meal event.

    Each row combines the diary fields for the meal, glucose context before the
    meal, IOB and COB from previous events, activity flags, time-of-day features,
    food indicators, current-meal Bateman absorption features, and future glucose
    targets. The resulting table is intentionally flat so it can be used by
    simple models such as linear regression or Ridge.
    """
    horizons = DEFAULT_HORIZONS_HOURS
    insulin_grid, insulin_activity, iob_fraction = insulin_activity_curve(
        args.insulin_dia_hours * 60,
        args.insulin_peak_minutes,
    )
    carb_grid, carb_rate, carb_remaining = bateman_curve(
        args.carb_duration_hours * 60,
        args.carb_peak_minutes,
    )
    carb_cumulative = 1.0 - carb_remaining

    events = events.copy()
    events["activity_minutes"] = events["notes"].map(activity_minutes_from_note)
    meals = vectorized_events.loc[vectorized_events["carbs_xe"] >= args.min_carbs_xe].copy()
    meals = meals.loc[meals["glucose_at_event"].notna()].copy()
    food_columns = [column for column in meals.columns if column.startswith("food_") and column[5:].isdigit()]

    rows: list[dict[str, object]] = []
    for _, meal in meals.iterrows():
        event_time = pd.Timestamp(meal["time"])
        # Start with raw event data and direct glucose at meal time.
        row: dict[str, object] = {
            "time": event_time,
            "carbs_xe": float(meal["carbs_xe"]),
            "carbs_grams": float(meal["carbs_xe"]) * args.xe_grams,
            "short_insulin_units": float(meal["short_insulin_units"]),
            "long_insulin_units": float(meal["long_insulin_units"]),
            "notes": meal.get("notes", ""),
            "food_ids": meal.get("food_ids", ""),
            "food_names": meal.get("food_names", ""),
            "glucose_at_meal": float(meal["glucose_at_event"]),
            "target_glucose": args.target_glucose,
        }

        # Pre-meal glucose context captures direction and variability before the bolus.
        before_time = event_time - pd.Timedelta(minutes=args.pre_window_minutes)
        before_glucose = interpolate_glucose(glucose_data, pd.Series([before_time]))[0]
        row["glucose_before_window"] = before_glucose
        row["glucose_slope_30m"] = (
            (row["glucose_at_meal"] - before_glucose) / (args.pre_window_minutes / 60)
            if np.isfinite(before_glucose)
            else np.nan
        )
        prev_window = glucose_window(glucose_data, before_time, event_time)
        row["glucose_mean_prev_30m"] = float(prev_window.mean()) if not prev_window.empty else np.nan
        row["glucose_std_prev_30m"] = float(prev_window.std()) if len(prev_window) > 1 else 0.0

        # Previous meals and boluses contribute through empirical COB/IOB curves.
        iob_units, insulin_activity_now = iob_features_for_time(event_time, events, insulin_grid, insulin_activity, iob_fraction)
        cob_xe, carb_activity_now = cob_features_for_time(event_time, events, carb_grid, carb_rate, carb_remaining)
        row["iob_units"] = iob_units
        row["insulin_activity_units_per_hour"] = insulin_activity_now
        row["cob_xe"] = cob_xe
        row["carb_activity_xe_per_hour"] = carb_activity_now

        has_activity, bike_minutes = recent_activity_features(event_time, events, args.activity_window_hours)
        row["activity_recent"] = has_activity
        row["bike_minutes_recent_4h"] = bike_minutes

        # Encode time of day cyclically so midnight and 23:59 stay close.
        hour = event_time.hour + event_time.minute / 60
        row["hour"] = hour
        row["hour_sin"] = np.sin(2 * np.pi * hour / 24)
        row["hour_cos"] = np.cos(2 * np.pi * hour / 24)
        row["day_of_week"] = event_time.dayofweek

        fast_food, slow_food = food_speed_flags(str(meal.get("food_names", "")))
        row["has_fast_food"] = fast_food
        row["has_slow_food"] = slow_food

        row.update(current_meal_bateman_areas(float(meal["carbs_xe"]), carb_grid, carb_cumulative, horizons))

        # Future target columns describe glucose response at fixed horizons.
        for horizon in horizons:
            future_time = event_time + pd.Timedelta(hours=horizon)
            future_glucose = interpolate_glucose(glucose_data, pd.Series([future_time]))[0]
            window_values = glucose_window(glucose_data, event_time, future_time)
            row[f"glucose_plus_{horizon}h"] = future_glucose
            row[f"delta_glucose_{horizon}h"] = (
                future_glucose - row["glucose_at_meal"] if np.isfinite(future_glucose) else np.nan
            )
            row[f"glucose_min_next_{horizon}h"] = float(window_values.min()) if not window_values.empty else np.nan
            row[f"glucose_max_next_{horizon}h"] = float(window_values.max()) if not window_values.empty else np.nan

        # Safety flags preserve whether the post-meal window contained hypo/hyperglycemia.
        next4 = glucose_window(glucose_data, event_time, event_time + pd.Timedelta(hours=4))
        row["hypo_next_4h"] = int((next4 < 3.9).any()) if not next4.empty else 0
        row["hyper_next_4h"] = int((next4 > 10.0).any()) if not next4.empty else 0

        for column in food_columns:
            row[column] = int(meal[column])

        rows.append(row)

    dataset = pd.DataFrame(rows).sort_values("time").reset_index(drop=True)
    return dataset


def main() -> None:
    """Run the complete dataset-building workflow.

    The function locates input files, parses monitor and diary data, vectorizes
    food notes, builds the meal-level modeling dataset, writes all intermediate
    CSV outputs, and prints a short summary for sanity checking.
    """
    args = parse_args()
    dataset_dir = find_dataset_dir(args.dataset_dir)
    monitor_file = find_monitor_file(dataset_dir, args.monitor_file)
    glucose_data, glucose_unit = load_glucose_monitor(monitor_file)
    if glucose_unit != "mmol/L":
        raise ValueError("This first modeling dataset expects glucose in mmol/L.")

    events_file = find_events_file(dataset_dir, args.events_file)
    events_year = infer_events_year(glucose_data, args.events_year)
    events = load_food_insulin_events(events_file, events_year)
    events = add_glucose_at_event(events, glucose_data)
    save_events_dataset(events, args.events_output)
    vocabulary, vectorized_events = save_food_features(events, args.food_vocab_output, args.vectorized_events_output)

    dataset = build_meal_dataset(glucose_data, events, vectorized_events, args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_csv(args.output, index=False)

    complete_target = int(dataset["delta_glucose_3h"].notna().sum()) if "delta_glucose_3h" in dataset else 0
    print(f"Glucose rows: {len(glucose_data)} from {monitor_file}")
    print(f"Event rows: {len(events)} from {events_file}")
    print(f"Food variants: {len(vocabulary)}")
    print(f"Meal rows: {len(dataset)}; rows with 3h target: {complete_target}")
    print(f"Saved meal modeling dataset to {args.output.resolve()}")


if __name__ == "__main__":
    main()
