from __future__ import annotations

from typing import Optional

import pandas as pd
import streamlit as st

from theme import STATUS_COLORS as _STATUS
from theme import palette
from ui import empty_state

OK = "#22C55E"
BAR = "#7C9CBF"

PALETTE = [BAR, "#5AA9FF", "#C4A1FF", "#FFB020", "#38BDF8", "#F472B6", "#34D399", "#FB7185"]

STATUS_COLORS = {key.title(): value for key, value in _STATUS.items()}

HEAT_RANGE = ["#FCA5A5", "#FCD34D", "#BBF7D0", "#4ADE80"]

RISK_COLORS = {
    "0–0.25": "#22C55E",
    "0.25–0.5": "#84CC16",
    "0.5–0.75": "#F59E0B",
    "0.75–1.0": "#EF4444",
}


def _axis() -> dict:
    p = palette()
    return dict(
        labelColor=p["axis_label"],
        titleColor=p["axis_title"],
        gridColor=p["grid"],
        domainColor=p["domain"],
        tickColor=p["domain"],
        labelFontSize=11,
        titleFontSize=12,
        titleFontWeight="normal",
    )


def style_chart(chart):
    return (
        chart.configure(background="transparent")
        .configure_axis(**_axis())
        .configure_view(strokeWidth=0)
    )


def line_chart(
    frame: pd.DataFrame,
    x: str,
    y: str,
    x_title: str,
    y_title: str,
    height: int = 280,
    color: str = OK,
    empty: str = "No points in this window",
) -> None:
    import altair as alt

    if frame.empty or not frame[y].fillna(0).any():
        empty_state(empty, "Widen the window or wait for the next check-in.")
        return
    x_type = "T" if pd.api.types.is_datetime64_any_dtype(frame[x]) else "N"
    tooltip = [
        alt.Tooltip(f"{x}:{x_type}", title=x_title),
        alt.Tooltip(f"{y}:Q", title=y_title, format=".2f"),
    ]
    base = alt.Chart(frame).encode(
        x=alt.X(f"{x}:{x_type}", title=x_title),
        y=alt.Y(f"{y}:Q", title=y_title, scale=alt.Scale(zero=True)),
        tooltip=tooltip,
    )
    area = base.mark_area(color=color, opacity=0.12, interpolate="monotone")
    line = base.mark_line(color=color, strokeWidth=2, interpolate="monotone")
    latest = (
        alt.Chart(frame.tail(1))
        .mark_point(filled=True, color=color, size=70, opacity=1)
        .encode(
            x=alt.X(f"{x}:{x_type}"),
            y=alt.Y(f"{y}:Q"),
            tooltip=tooltip,
        )
    )
    chart = style_chart((area + line + latest).properties(height=height))
    st.altair_chart(chart, use_container_width=True)


def bar_chart(
    frame: pd.DataFrame,
    category: str,
    value: str,
    x_title: str,
    y_title: str,
    height: int = 280,
    horizontal: bool = False,
    color: Optional[str] = None,
    color_by: bool = False,
    color_map: Optional[dict] = None,
    empty: str = "No categories to compare",
    sort: Optional[list] = None,
) -> None:
    import altair as alt

    color = color or palette()["bar"]
    if frame.empty or not frame[value].fillna(0).any():
        empty_state(empty, "Nothing to compare in this filter.")
        return
    cat_sort = sort if sort is not None else "-x" if horizontal else "-y"
    if horizontal:
        encode = dict(
            y=alt.Y(
                f"{category}:N",
                title=y_title,
                sort=cat_sort,
                axis=alt.Axis(labelLimit=280),
            ),
            x=alt.X(f"{value}:Q", title=x_title, scale=alt.Scale(zero=True), axis=alt.Axis(tickMinStep=1, format="d")),
        )
        auto_height = max(height, 28 * len(frame) + 40)
    else:
        encode = dict(
            x=alt.X(f"{category}:N", title=x_title, sort=cat_sort, axis=alt.Axis(labelAngle=0)),
            y=alt.Y(f"{value}:Q", title=y_title, scale=alt.Scale(zero=True), axis=alt.Axis(tickMinStep=1, format="d")),
        )
        auto_height = height
    encode["tooltip"] = [
        alt.Tooltip(f"{category}:N", title=x_title if not horizontal else y_title),
        alt.Tooltip(f"{value}:Q", title=y_title if not horizontal else x_title, format="d"),
    ]
    if color_map:
        domain = list(dict.fromkeys(frame[category].tolist()))
        shades = [color_map.get(key, color) for key in domain]
        encode["color"] = alt.Color(
            f"{category}:N",
            scale=alt.Scale(domain=domain, range=shades),
            legend=None,
        )
        mark = alt.Chart(frame).mark_bar(size=18, cornerRadius=3)
    else:
        mark = alt.Chart(frame).mark_bar(color=color, size=18, cornerRadius=3)
    chart = style_chart(mark.encode(**encode).properties(height=auto_height))
    st.altair_chart(chart, use_container_width=True)


def heatmap(
    frame: pd.DataFrame,
    x: str,
    y: str,
    value: str = "Rate",
    x_order: Optional[list] = None,
    y_label: str = "Course",
    x_label: str = "Day",
    empty: str = "Nothing to show yet",
) -> None:
    import altair as alt

    if frame.empty:
        empty_state(empty, "Sessions with check-ins will fill this grid.")
        return
    rows = list(dict.fromkeys(frame[y].tolist()))
    p = palette()
    height = max(120, 46 * len(rows) + 36)
    base = alt.Chart(frame).encode(
        x=alt.X(
            f"{x}:O",
            sort=x_order,
            title=None,
            axis=alt.Axis(labelAngle=0, orient="top", ticks=False, domain=False),
            scale=alt.Scale(paddingInner=0.08, domain=x_order) if x_order else alt.Scale(paddingInner=0.08),
        ),
        y=alt.Y(
            f"{y}:N",
            sort=rows,
            title=None,
            axis=alt.Axis(labelLimit=220, ticks=False, domain=False),
            scale=alt.Scale(paddingInner=0.08),
        ),
    )
    tooltip = [
        alt.Tooltip(f"{y}:N", title=y_label),
        alt.Tooltip(f"{x}:O", title=x_label),
        alt.Tooltip(f"{value}:Q", title="Attendance", format=".0%"),
    ]
    for extra in ("Present", "Enrolled"):
        if extra in frame.columns:
            tooltip.append(alt.Tooltip(f"{extra}:Q", title=extra, format="d"))
    rect = base.mark_rect(cornerRadius=4, stroke=p["surface"], strokeWidth=2).encode(
        color=alt.Color(
            f"{value}:Q",
            scale=alt.Scale(domain=[0, 0.5, 0.8, 1], range=HEAT_RANGE, clamp=True),
            legend=None,
        ),
        tooltip=tooltip,
    )
    text = base.mark_text(fontSize=11, fontWeight=600, color="#0F172A").encode(
        text=alt.Text(f"{value}:Q", format=".0%"),
    )
    chart = style_chart((rect + text).properties(height=height))
    st.altair_chart(chart, use_container_width=True)