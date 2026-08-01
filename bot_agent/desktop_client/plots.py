from __future__ import annotations

from collections import defaultdict

import plotly.graph_objects as go  # type: ignore[import-untyped]

from app.api.schemas import GlucosePlotData, GlucosePoint


def build_glucose_plot_html(data: GlucosePlotData) -> str:
    points_by_user: dict[int, list[GlucosePoint]] = defaultdict(list)
    for point in data.points:
        points_by_user[point.telegram_user_id].append(point)

    figure = go.Figure()
    for user_id in sorted(points_by_user):
        points = points_by_user[user_id]
        figure.add_trace(
            go.Scatter(
                x=[point.occurred_at for point in points],
                y=[float(point.blood_glucose_mmol_l) for point in points],
                mode="lines+markers",
                name=f"Telegram user {user_id}",
                hovertemplate="%{x}<br>%{y:.2f} mmol/L<extra>%{fullData.name}</extra>",
            )
        )

    if not data.points:
        figure.add_annotation(
            text="No glucose measurements in the selected interval",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
        )

    figure.update_layout(
        title="Blood glucose",
        xaxis_title="Time",
        yaxis_title="Blood glucose (mmol/L)",
        hovermode="x unified",
        template="plotly_white",
        margin={"l": 70, "r": 30, "t": 60, "b": 60},
    )
    return str(figure.to_html(include_plotlyjs=True, full_html=True))
