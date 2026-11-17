from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta

import pandas as pd
import streamlit as st

from access import require_admin
from charts import RISK_COLORS, STATUS_COLORS, bar_chart, line_chart
from client import (
    as_total,
    checkin_slo,
    grafana_dashboard_url,
    now_sgt,
    parse_dt,
)
from config import CHECKIN_LATENCY_TARGET_S, CHECKIN_SLO_TARGET
from data import attendees_for, enrolled_for, load_bundle
from ui import (
    display_name,
    empty_state,
    health_pills,
    latency_bar,
    live_badge,
    page_header,
    panel_title,
    grafana_grid,
    panel,
    render_html_table,
    risk_threshold_for,
    session_label,
    stat_row,
    status_tag,
    user_directory,
)
from views.overview import _verification_donut, flag_alert, recent_flag_rate

CHART_H = 220

def render_metrics(client, health: dict) -> None:
    if not require_admin(client):
        return
    page_header("Monitoring", "System Metrics", "Service health, check-in speed and risk at a glance.")

    bundle = load_bundle(client)
    checkins = bundle["checkins"]
    sessions = bundle["sessions"]
    today = now_sgt().date()
    
    today_n = sum(1 for row in checkins
                  if (dt := parse_dt(row.get("checked_in_at"))) is not None and dt.date() == today)
    flagged_n = sum(1 for row in checkins if str(row.get("status") or "").lower() in {"flagged", "appealed"})
    
    today_sessions = [s for s in sessions
                      if (dt := parse_dt(s.get("scheduled_start"))) is not None and dt.date() == today]
    enrolled = sum(enrolled_for(s) for s in today_sessions)
    present = sum(attendees_for(s, checkins) for s in today_sessions)
    attendance = (present / enrolled) if enrolled else None

    total_checkins = max(as_total(client.list_checkins(limit=1)), len(checkins))
    flagged_total = max(as_total(client.flagged_checkins(limit=1)), flagged_n)
    slo = checkin_slo(threshold=CHECKIN_LATENCY_TARGET_S, target=CHECKIN_SLO_TARGET)
    p95 = slo.get("p95")
    pct = slo.get("pct_under_2s")
    slo_ok = slo.get("met") is True
    error_rate = slo.get("error_rate")
    samples = int(slo.get("n") or 0)

    tb1, tb2 = st.columns((2.2, 1.4))
    with tb1:
        live_badge()
        st.markdown(health_pills(health), unsafe_allow_html=True)
    with tb2:
        st.markdown(
            f'<p class="grafana-open metrics-grafana"><a href="{grafana_dashboard_url()}" '
            f'target="_blank" rel="noopener">Open Grafana</a></p>',
            unsafe_allow_html=True,
        )

    flag_alert(recent_flag_rate(bundle.get("live_window") or []))

    panel_title("Business metrics")
    st.markdown(
        stat_row(
            [
                ("Total Check-ins", total_checkins, ""),
                ("Today", today_n, ""),
                ("Flagged", flagged_total, "warn" if flagged_total else "good"),
                ("Attendance Today", f"{attendance:.0%}" if attendance is not None else "—", ""),
            ]
        ),
        unsafe_allow_html=True,
    )

    panel_title("Technical metrics")
    st.markdown(latency_bar(p95, target=CHECKIN_LATENCY_TARGET_S), unsafe_allow_html=True)
    slo_value = "Met" if slo_ok else ("—" if samples == 0 else "Missed")
    slo_tone = "good" if slo_ok else ("bad" if samples else "")
    slo_label = f"SLO: {CHECKIN_SLO_TARGET:.0%} under {CHECKIN_LATENCY_TARGET_S:g}s" + (f" ({pct:.0%} of {samples} calls)" if pct is not None else "")
    st.markdown(
        stat_row(
            [
                ("Error rate", f"{error_rate:.0%}" if error_rate is not None else "—", "bad" if (error_rate or 0) > 0 else "good"),
                (slo_label, slo_value, slo_tone),
                ("Check-in API samples", samples, ""),
            ]
        ),
        unsafe_allow_html=True,
    )

    r1, r2 = st.columns(2)
    with r1:
        with panel("Risk score distribution", "How risky check-ins are, by band"):
            _risk_histogram(checkins)
    with r2:
        with panel("Flagged vs approved", "Outcome of every check-in"):
            _verification_donut(checkins)

    tab_hour, tab_status = st.tabs(["Check-ins by hour (last 24 h)", "Sessions by status"])
    with tab_hour:
        _hourly_line(checkins)
    with tab_status:
        _session_status_chart(sessions)

    sessions_by_id = {s["id"]: s for s in sessions if s.get("id")}
    course_map = {c["id"]: c for c in bundle["courses"] if c.get("id")}

    def _threshold(row: dict) -> float:
        session = sessions_by_id.get(row.get("session_id") or "") or {}
        return risk_threshold_for(session, course_map.get(session.get("course_id")))

    high_risk = [row for row in checkins if (row.get("risk_score") or 0) >= _threshold(row)]
    directory = user_directory(client, [row.get("student_id") for row in high_risk])
    panel_title("High-risk check-ins (at or above the session's risk threshold)")
    if not high_risk:
        empty_state("No high-risk check-ins", "Nothing scored at or above its session's threshold.")
    else:
        table = pd.DataFrame(
            [
                {
                    "Student": display_name(row, directory),
                    "Session": (
                        session_label(session)
                        if (session := sessions_by_id.get(row.get("session_id") or ""))
                        else (row.get("session_name") or "—")
                    ),
                    "Risk": row.get("risk_score"),
                    "Threshold": _threshold(row),
                    "Status": status_tag(row.get("status")),
                }
                for row in sorted(high_risk, key=lambda item: item.get("risk_score") or 0, reverse=True)[:20]
            ]
        )
        render_html_table(table, height=260)

    panel_title("Live dashboards · Prometheus & Grafana")
    grafana_grid(height=280)


def _risk_histogram(checkins: list) -> None:
    bands = [("0–0.25", 0.0, 0.25), ("0.25–0.5", 0.25, 0.5), ("0.5–0.75", 0.5, 0.75), ("0.75–1.0", 0.75, 1.01)]
    counts = Counter()
    for row in checkins:
        score = row.get("risk_score")
        if score is None:
            continue
        value = float(score)
        for label, lo, hi in bands:
            if lo <= value < hi:
                counts[label] += 1
                break
    if not counts:
        empty_state("No risk samples", "Risk bands appear after check-ins land.")
        return
    frame = pd.DataFrame(
        {"Band": [label for label, _, _ in bands], "Check-ins": [int(counts[label]) for label, _, _ in bands]}
    )
    bar_chart(
        frame,
        "Band",
        "Check-ins",
        "Risk band",
        "Check-ins",
        height=CHART_H,
        sort=[label for label, _, _ in bands],
        color_map=RISK_COLORS,
    )


def _hourly_line(checkins: list) -> None:
    now = now_sgt().replace(minute=0, second=0, microsecond=0)
    stamps = [now - timedelta(hours=23 - i) for i in range(24)]
    buckets: dict = defaultdict(int)
    start = stamps[0]
    for row in checkins:
        dt = parse_dt(row.get("checked_in_at"))
        if dt is None or dt < start:
            continue
        buckets[(dt.date(), dt.hour)] += 1
    frame = pd.DataFrame(
        {
            "Time": stamps,
            "Check-ins": [int(buckets.get((stamp.date(), stamp.hour), 0)) for stamp in stamps],
        }
    )
    line_chart(frame, "Time", "Check-ins", "Hour (SGT)", "Check-ins", height=CHART_H)


def _session_status_chart(sessions: list) -> None:
    order = ["active", "scheduled", "closed", "cancelled"]
    counts = Counter(str(session.get("status") or "").lower() for session in sessions)
    keys = [key for key in order if counts.get(key)] + [key for key in counts if key not in order]
    if not keys:
        empty_state("No sessions", "Create a session to see status load.")
        return
    frame = pd.DataFrame(
        {"Status": [key.title() for key in keys], "Sessions": [int(counts[key]) for key in keys]}
    )
    bar_chart(
        frame,
        "Status",
        "Sessions",
        "Status",
        "Sessions",
        height=CHART_H,
        sort=[k.title() for k in keys],
        color_map=STATUS_COLORS,
    )