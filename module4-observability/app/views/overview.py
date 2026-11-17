from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from html import escape

import pandas as pd
import streamlit as st

from access import require_staff
from config import (
    FAILED_LOGIN_ALERT,
    FLAG_ALERT_MIN_CHECKINS,
    FLAG_ALERT_RATE,
    FLAG_ALERT_WINDOW_HOURS,
    GOOD_ATTENDANCE,
    LOW_ATTENDANCE,
)
from client import now_sgt, parse_dt, pick
from charts import heatmap, line_chart, style_chart
from theme import palette
from data import attendees_for, enrolled_for, load_bundle
from ui import (
    checkins_to_frame,
    course_label,
    dataframe,
    panel_title,
    attendance_bar,
    VERIFY_ORDER,
    display_name,
    empty_state,
    fmt_pct,
    live_badge,
    page_header,
    panel,
    review_item,
    session_label,
    stat_row,
    user_directory,
    verification_label,
)


def _derive(client) -> dict:
    bundle = load_bundle(client)
    sessions = bundle["sessions"]
    courses = bundle["courses"]
    checkins = bundle["checkins"]
    flagged = bundle["flagged"]

    today = now_sgt().date()
    today_rows = []
    hour_counts = defaultdict(int)
    status_counts = Counter()
    for row in checkins:
        status_counts[str(row.get("status") or "pending").lower()] += 1
        dt = parse_dt(row.get("checked_in_at"))
        if dt is None:
            continue
        hour_counts[(dt.date(), dt.hour)] += 1
        if dt.date() == today:
            today_rows.append(row)

    today_sessions = []
    for session in sessions:
        start = parse_dt(session.get("scheduled_start"))
        end = parse_dt(session.get("scheduled_end"))
        active_today = str(session.get("status")).lower() == "active"
        on_today = (start and start.date() == today) or (end and end.date() == today)
        if active_today or on_today:
            today_sessions.append(session)

    approved = status_counts.get("approved", 0)
    decided = approved + status_counts.get("rejected", 0) + status_counts.get("flagged", 0)
    approval_rate = (approved / decided) if decided else 0

    roster = []
    for session in sessions:
        enrolled = enrolled_for(session)
        attendees = attendees_for(session, checkins)
        roster.append(
            {
                "session": session,
                "label": session.get("name") or "Session",
                "enrolled": enrolled,
                "attendees": attendees,
            }
        )
    today_enrolled = sum(enrolled_for(s) for s in today_sessions)
    today_attendees = sum(attendees_for(s, checkins) for s in today_sessions)
    checkin_rate = (today_attendees / today_enrolled) if today_enrolled else None

    inactive = sum(1 for s in sessions if str(s.get("status")).lower() != "active")
    risks = [float(c["risk_score"]) for c in checkins if c.get("risk_score") is not None]
    avg_risk = sum(risks) / len(risks) if risks else 0
    flagged_n = len(flagged)

    flag_window = recent_flag_rate(bundle.get("live_window") or [])

    summary = client.audit_summary(days=1)
    failed_logins = int(((summary or {}).get("by_action") or {}).get("login_failed", 0))

    return {
        "total_sessions": len(sessions),
        "active_sessions": sum(1 for s in sessions if str(s.get("status")).lower() == "active"),
        "inactive_sessions": inactive,
        "today_sessions": len(today_sessions),
        "total_checkins": len(checkins),
        "today_checkins": len(today_rows),
        "flagged_pending": flagged_n,
        "flag_window": flag_window,
        "checkin_rate": checkin_rate,
        "failed_logins": failed_logins,
        "approval_rate": approval_rate,
        "average_risk": avg_risk,
        "sessions": sessions,
        "courses": courses,
        "checkins": checkins,
        "flagged": flagged,
        "hour_counts": hour_counts,
        "status_counts": status_counts,
        "roster": roster,
    }


def recent_flag_rate(live_checkins: list) -> dict:
    cutoff = now_sgt() - timedelta(hours=FLAG_ALERT_WINDOW_HOURS)
    recent = [
        c for c in live_checkins
        if (parse_dt(c.get("checked_in_at")) or cutoff) > cutoff
    ]
    flagged = [c for c in recent if str(c.get("status") or "").lower() in {"flagged", "appealed"}]
    reasons = Counter()
    for row in flagged:
        reasons.update(_risk_factors(row))
    total = len(recent)
    rate = (len(flagged) / total) if total else 0.0
    return {
        "total": total,
        "flagged": len(flagged),
        "rate": rate,
        "alert": total >= FLAG_ALERT_MIN_CHECKINS and rate >= FLAG_ALERT_RATE,
        "reasons": reasons.most_common(3),
    }


def _risk_factors(row: dict) -> list:
    import json

    raw = row.get("risk_factors")
    if not raw:
        return []
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


def flag_alert(window: dict) -> None:
    if not window.get("alert"):
        return
    reasons = ", ".join(
        f"{name.replace('_', ' ')} ({n})" for name, n in window.get("reasons") or []
    )
    cause = f" Most common risk signals: {escape(reasons)}." if reasons else ""
    st.markdown(
        f'<div class="alert-strip danger">High flagged rate: {window["flagged"]} of '
        f'{window["total"]} check-ins in the last {FLAG_ALERT_WINDOW_HOURS} h '
        f'({window["rate"]:.0%}, alert at {FLAG_ALERT_RATE:.0%}).{cause}</div>',
        unsafe_allow_html=True,
    )


def render_overview(client) -> None:
    if not require_staff(client):
        return
    page_header("Attendance", "Overview", "Today's sessions, what needs your attention, and recent attendance.")
    live_badge()

    with st.spinner("Pulling attendance…"):
        data = _derive(client)

    flagged_n = int(data["flagged_pending"] or 0)
    flag_alert(data["flag_window"])
    failed = int(data.get("failed_logins") or 0)
    if failed >= FAILED_LOGIN_ALERT:
        st.markdown(
            f'<div class="alert-strip danger">{failed} failed logins in the last 24 hours, '
            f'possible attack. Check the audit log.</div>',
            unsafe_allow_html=True,
        )

    rate = data["checkin_rate"]
    rate_tone = "" if rate is None else (
        "good" if rate >= GOOD_ATTENDANCE else ("warn" if rate < LOW_ATTENDANCE else ""))
    st.markdown(
        stat_row(
            [
                ("Check-in rate today", fmt_pct(rate), rate_tone),
                ("Sessions today", data["today_sessions"], ""),
                ("Open now", int(data["active_sessions"] or 0), ""),
                ("Need review", flagged_n, "warn" if flagged_n else "good"),
            ]
        ),
        unsafe_allow_html=True,
    )

    left, right = st.columns((1.5, 1))
    with left:
        _today_panel(data)
    with right:
        with panel("Needs review", "Flagged and appealed check-ins"):
            _review_queue(client, data)

    with panel("Check-ins over time", "How many students checked in"):
        session_ids, session_labels = _session_options(data["sessions"])
        g1, g2, g3 = st.columns(3)
        graph_session_id = g1.selectbox(
            "Session", session_ids,
            format_func=lambda sid: session_labels.get(sid, sid or "All sessions"),
            key="overview_graph_session_id",
        )
        grain = g2.selectbox("Group by", ["Hour", "Day"], key="overview_graph_grain")
        window = g3.selectbox("Window", ["Last 12 hours", "Last 24 hours", "Last 7 days"],
                              index=2, key="overview_graph_span")
        filtered = _graph_rows(data["checkins"], graph_session_id, window)
        st.caption(f"{len(filtered)} check-in{'s' if len(filtered) != 1 else ''} in this view.")
        _volume_chart(filtered, grain, window, graph_session_id, height=320)

    left, right = st.columns((1.5, 1))
    with left:
        with panel("Attendance by session", "Most recent sessions: checked in vs enrolled"):
            _attendance_chart(data["sessions"], data["checkins"], "", "", limit=6)
    with right:
        with panel("Verification results", "Approved vs flagged vs rejected"):
            _verification_donut(data["checkins"])

    panel_title("More analytics")
    tab_week, tab_filter, tab_latest = st.tabs(
        ["Weekly attendance by course", "Attendance by course & day", "Latest check-ins"])
    with tab_week:
        if "week_offset" not in st.session_state:
            st.session_state.week_offset = 0
        c1, c2, c3 = st.columns([1, 6, 1])
        with c1:
            if st.button("← Previous", key="ghost_prev_week", use_container_width=True):
                st.session_state.week_offset += 7
                st.rerun()
        with c3:
            if st.button("Next →", key="ghost_next_week", use_container_width=True,
                         disabled=st.session_state.week_offset == 0):
                st.session_state.week_offset = max(0, st.session_state.week_offset - 7)
                st.rerun()
        start_date = now_sgt().date() - timedelta(days=st.session_state.week_offset + 6)
        end_date = now_sgt().date() - timedelta(days=st.session_state.week_offset)
        c2.markdown(f'<div class="week-label">{start_date.strftime("%d %b")} – '
                    f'{end_date.strftime("%d %b %Y")}</div>', unsafe_allow_html=True)
        frame, day_order = _heat_frame(data["sessions"], data.get("courses") or [], data["checkins"],
                                       offset=st.session_state.week_offset)
        if not frame.empty:
            worst = frame.groupby("Course")["Rate"].mean().sort_values()
            courses = list(worst.index)
            show_all = len(courses) > 10 and st.checkbox(
                f"Show all {len(courses)} courses", key="overview_heat_all")
            keep = courses if show_all or len(courses) <= 10 else courses[:10]
            frame = frame[frame["Course"].isin(keep)]
            frame = frame.set_index("Course").loc[keep].reset_index()
        heatmap(frame, "Day", "Course", "Rate", x_order=day_order, empty="No attendance in this week")
        st.markdown(
            '<div class="att-legend"><span><i style="background:#FCA5A5"></i>Under 50%</span>'
            '<span><i style="background:#FCD34D"></i>50–80%</span>'
            '<span><i style="background:#4ADE80"></i>80% and above</span></div>',
            unsafe_allow_html=True,
        )
        st.caption("Share of enrolled students who checked in, per course per day. "
                   "Lowest attendance first; blank means no session that day.")
    with tab_filter:
        day_ids, day_labels = _day_options()
        course_ids, course_labels = _course_options(data.get("courses") or [], data["sessions"])
        f1, f2 = st.columns(2)
        att_course_id = f1.selectbox("Course", course_ids,
                                     format_func=lambda cid: course_labels.get(cid, cid or "All courses"),
                                     key="overview_att_course_id")
        att_day = f2.selectbox("Day", day_ids, format_func=lambda d: day_labels.get(d, d or "All days"),
                               key="overview_att_day")
        _attendance_chart(data["sessions"], data["checkins"], att_day, att_course_id, limit=15)
    with tab_latest:
        directory = user_directory(client, [c.get("student_id") for c in data["checkins"]])
        sessions_by_id = {x["id"]: x for x in data["sessions"] if x.get("id")}
        recent = sorted(
            data["checkins"],
            key=lambda r: parse_dt(r.get("checked_in_at")) or now_sgt() - timedelta(days=3650),
            reverse=True,
        )[:20]
        dataframe(checkins_to_frame(recent, directory, sessions_by_id), height=360)


def _counts_for(session: dict, checkins: list) -> dict:
    rank = {"approved": 0, "flagged": 1, "appealed": 1, "pending": 2, "rejected": 3}
    best: dict = {}
    loose: list = []
    for row in checkins:
        if row.get("session_id") != session.get("id"):
            continue
        status = str(row.get("status") or "pending").lower()
        uid = row.get("student_id")
        if not uid:
            loose.append(status)
            continue
        if uid not in best or rank.get(status, 2) < rank.get(best[uid], 2):
            best[uid] = status
    statuses = list(best.values()) + loose
    n = Counter(statuses)
    present = n["approved"]
    flagged = n["flagged"] + n["appealed"]
    rejected = n["rejected"]
    return {
        "present": present,
        "flagged": flagged,
        "pending": len(statuses) - present - flagged - rejected,
        "rejected": rejected,
    }


def _today_panel(data: dict) -> None:
    now = now_sgt()
    today = now.date()
    live = [x for x in data["sessions"] if str(x.get("status")).lower() == "active"]
    later = [
        x for x in data["sessions"]
        if str(x.get("status")).lower() == "scheduled"
        and (start := parse_dt(x.get("scheduled_start"))) is not None and start.date() == today
    ]
    if live or later:
        title, hint = "Today's sessions", f"{len(live)} open now · {len(later)} later today"
    else:
        title, hint = "Recent sessions", "Nothing is on today, so here are the last five"
    with panel(title, hint):
        rows = []
        for session in live[:6]:
            rows.append(_session_row(session, data["checkins"], _window_text(session, now)))
        for session in sorted(later, key=lambda x: str(x.get("scheduled_start")))[:4]:
            start = parse_dt(session.get("scheduled_start"))
            rows.append(_session_row(session, data["checkins"], f"starts {start.strftime('%H:%M')}"))
        if not rows:
            recent = [
                x for x in data["sessions"]
                if str(x.get("status")).lower() in {"closed", "active"}
                and parse_dt(x.get("scheduled_start")) is not None
            ]
            recent.sort(key=lambda x: parse_dt(x.get("scheduled_start")), reverse=True)
            for session in recent[:5]:
                start = parse_dt(session.get("scheduled_start"))
                rows.append(_session_row(session, data["checkins"],
                                         f"{str(session.get('status')).title()} · {start.strftime('%a %d %b, %H:%M')}"))
        if rows:
            st.markdown("".join(rows), unsafe_allow_html=True)
        else:
            empty_state("No sessions yet", "Sessions appear here once they are scheduled.")
        extra = max(0, len(live) - 6) + max(0, len(later) - 4)
        if st.button(f"View all sessions →{f'  (+{extra} more)' if extra else ''}",
                     key="ghost_view_all_sessions"):
            st.session_state["sess_filter_status"] = "active" if live else "all"
            st.session_state.nav = "sessions"
            st.rerun()


def _session_row(session: dict, checkins: list, note: str) -> str:
    n = _counts_for(session, checkins)
    enrolled = enrolled_for(session)
    here = n["present"] + n["flagged"] + n["pending"]
    return (
        '<div class="srow">'
        f'<div class="srow-name">{escape(session_label(session))}<span>{escape(note)}</span></div>'
        f'<div class="srow-time">{escape(str(session.get("group_name") or "All"))}</div>'
        f'{attendance_bar(n["present"], n["flagged"], n["pending"], enrolled, n["rejected"], mini=True)}'
        f'<div class="srow-n">{here} / {enrolled}</div>'
        "</div>"
    )


def _window_text(session: dict, now) -> str:
    closes = parse_dt(session.get("checkin_closes_at"))
    if closes and closes < now:
        window = f"check-in window closed {closes.strftime('%d %b %H:%M')}"
    else:
        window = f"check-in closes {closes.strftime('%H:%M')}" if closes else "no check-in window"
    return " · ".join(part for part in (session.get("venue_name"), window) if part)


def _review_queue(client, data: dict) -> None:
    flagged = data.get("flagged") or []
    if not flagged:
        empty_state("Queue clear", "No flagged or appealed check-ins right now.")
        return
    directory = user_directory(client, [c.get("student_id") for c in flagged])
    items = []
    for row in flagged[:6]:
        when = parse_dt(row.get("checked_in_at"))
        items.append(
            review_item(
                display_name(row, directory),
                row.get("status"),
                pick(row, "risk_score", default=None),
                when.strftime("%d %b %H:%M") if when else "—",
            )
        )
    st.markdown("".join(items), unsafe_allow_html=True)
    if len(flagged) > 6:
        st.caption(f"{len(flagged) - 6} more in Check-ins.")
        
    if st.button("Open check-ins to review", use_container_width=True):
        st.session_state.nav = "checkins"
        st.session_state["filter_status"] = "flagged"
        st.session_state["checkin_session_id"] = ""
        st.session_state["return_to_overview"] = False
        st.rerun()


def _window_hours(window: str) -> int:
    if window == "Last 12 hours":
        return 12
    if window == "Last 24 hours":
        return 24
    return 24 * 7


def _graph_rows(checkins: list, session_id: str, window: str) -> list:
    now = now_sgt()
    start = now - timedelta(hours=_window_hours(window))
    rows = []
    for row in checkins:
        if session_id and row.get("session_id") != session_id:
            continue
        dt = parse_dt(row.get("checked_in_at"))
        if dt is None:
            continue
        if not session_id and dt < start:
            continue
        rows.append(row)
    return rows


def _volume_chart(rows: list, grain: str, window: str, session_id: str = "", height: int = 280) -> None:
    now = now_sgt().replace(minute=0, second=0, microsecond=0)
    stamps_dt = [parse_dt(row.get("checked_in_at")) for row in rows]
    stamps_dt = [dt for dt in stamps_dt if dt is not None]

    if grain == "Day":
        if session_id and stamps_dt:
            first = min(stamps_dt).replace(hour=0, minute=0, second=0, microsecond=0)
            last = max(stamps_dt).replace(hour=0, minute=0, second=0, microsecond=0)
            days = max(1, min(14, (last.date() - first.date()).days + 1))
            stamps = [first + timedelta(days=i) for i in range(days)]
        else:
            days = 7 if window == "Last 7 days" else (2 if window == "Last 24 hours" else 1)
            stamps = [(now - timedelta(days=days - 1 - i)).replace(hour=0) for i in range(days)]
        buckets = Counter()
        for dt in stamps_dt:
            buckets[dt.date()] += 1
        values = [int(buckets.get(s.date(), 0)) for s in stamps]
        index_name = "Day"
    else:
        if session_id and stamps_dt:
            first = min(stamps_dt).replace(minute=0, second=0, microsecond=0)
            last = max(stamps_dt).replace(minute=0, second=0, microsecond=0)
            hours = int((last - first).total_seconds() // 3600) + 1
            hours = max(3, min(hours, 24))
            stamps = [first + timedelta(hours=i) for i in range(hours)]
        else:
            hours = _window_hours(window)
            stamps = [now - timedelta(hours=hours - 1 - i) for i in range(hours)]
        buckets = Counter()
        for dt in stamps_dt:
            buckets[(dt.date(), dt.hour)] += 1
        values = [int(buckets.get((s.date(), s.hour), 0)) for s in stamps]
        index_name = "Hour"

    if not any(values):
        empty_state("No check-ins in this window", "Pick another session or widen the time window.")
        return
    frame = pd.DataFrame({"Time": stamps, "Check-ins": values})
    line_chart(frame, "Time", "Check-ins", f"{index_name} (SGT)", "Check-ins", height=height)


def _session_options(sessions: list) -> tuple[list, dict]:
    ids = [""]
    labels = {"": "All sessions"}
    for session in sessions:
        sid = session.get("id")
        if not sid or sid in labels:
            continue
        ids.append(sid)
        labels[sid] = session_label(session)
    return ids, labels


def _day_options() -> tuple[list, dict]:
    today = now_sgt().date()
    ids = [""]
    labels = {"": "All days"}
    for i in range(7):
        day = today - timedelta(days=i)
        key = day.isoformat()
        ids.append(key)
        labels[key] = day.strftime("%a %d %b")
    return ids, labels


def _course_options(courses: list, sessions: list) -> tuple[list, dict]:
    ids = [""]
    labels = {"": "All courses"}
    for course in courses:
        cid = course.get("id")
        if not cid or cid in labels:
            continue
        ids.append(cid)
        labels[cid] = course_label(course)
    for session in sessions:
        cid = session.get("course_id")
        if not cid or cid in labels:
            continue
        ids.append(cid)
        labels[cid] = session.get("course_code") or str(cid)
    return ids, labels


def _heat_frame(sessions: list, courses: list, checkins: list, days: int = 7, offset: int = 0):
    today = now_sgt().date() - timedelta(days=offset)
    day_keys = [today - timedelta(days=days - 1 - i) for i in range(days)]
    labels = {d: d.strftime("%a %d") for d in day_keys}
    course_map = {c.get("id"): c for c in courses if c.get("id")}
    acc: dict = defaultdict(lambda: [0, 0])
    for session in sessions:
        status = str(session.get("status") or "").lower()
        if status == "cancelled":
            continue
        start = parse_dt(session.get("scheduled_start"))
        if not start or start.date() not in labels:
            continue
        present = attendees_for(session, checkins)
        roster = max(enrolled_for(session), present)
        if roster <= 0 or (status == "scheduled" and present == 0):
            continue
        code = (
            session.get("course_code")
            or (course_map.get(session.get("course_id")) or {}).get("code")
            or "Other"
        )
        cell = acc[(str(code), start.date())]
        cell[0] += present
        cell[1] += roster
    records = [
        {
            "Course": code,
            "Day": labels[day],
            "Rate": min(1.0, present / enrolled),
            "Present": int(present),
            "Enrolled": int(enrolled),
        }
        for (code, day), (present, enrolled) in sorted(acc.items())
    ]
    return pd.DataFrame(records), [labels[d] for d in day_keys]


def _verification_donut(rows: list) -> None:
    from theme import STATUS_COLORS

    counts = Counter(str(row.get("status") or "pending").lower() for row in rows)
    keys = [key for key, _label, _color in VERIFY_ORDER if counts.get(key)]
    keys += [key for key in counts if key not in {k for k, _, _ in VERIFY_ORDER} and counts[key]]
    total = sum(int(counts[key]) for key in keys)
    if total <= 0:
        empty_state("No verifications yet", "Status mix appears after the first check-in.")
        return

    stops = []
    acc = 0.0
    legend = []
    for key in keys:
        n = int(counts[key])
        pct = n / total * 100
        color = STATUS_COLORS.get(key, "#8A94A3")
        stops.append(f"{color} {acc:.3f}% {acc + pct:.3f}%")
        acc += pct
        legend.append(
            f'<li><i style="background:{color}"></i>'
            f"<span>{verification_label(key)}</span>"
            f"<b>{n}</b></li>"
        )
    gradient = ", ".join(stops)
    st.markdown(
        f'<div class="verify-donut">'
        f'<div class="verify-donut-stage">'
        f'<div class="verify-donut-pie" style="background:conic-gradient(from -90deg,{gradient});"></div>'
        f'<div class="verify-donut-hole"><span>{total}</span></div>'
        f"</div>"
        f'<ul class="verify-donut-legend">{"".join(legend)}</ul>'
        f"</div>",
        unsafe_allow_html=True,
    )


def _session_dates(session: dict) -> set:
    first = parse_dt(session.get("scheduled_start"))
    last = parse_dt(session.get("scheduled_end")) or first
    if first is None:
        return set()
    if last is None or last < first:
        last = first
    days = set()
    cursor = first.date()
    end_day = last.date()
    while cursor <= end_day:
        days.add(cursor)
        cursor = cursor + timedelta(days=1)
    return days


def _attendance_chart(sessions: list, checkins: list, day_key: str, course_id: str,
                      limit: int = 0) -> None:
    import altair as alt

    day = None
    if day_key:
        try:
            day = date.fromisoformat(day_key)
        except ValueError:
            day = None

    day_checkins = checkins
    if day is not None:
        day_checkins = [
            row
            for row in checkins
            if (dt := parse_dt(row.get("checked_in_at"))) is not None and dt.date() == day
        ]

    scoped = []
    for session in sessions:
        status = str(session.get("status") or "").lower()
        if status == "cancelled":
            continue
        if course_id and session.get("course_id") != course_id:
            continue
        if day is not None and day not in _session_dates(session):
            continue
        present = attendees_for(session, day_checkins)
        roster = max(enrolled_for(session), present)
        if status == "scheduled" and present == 0:
            continue
        if roster == 0:
            continue
        start = parse_dt(session.get("scheduled_start"))
        rate = (present / roster) if roster else 0
        name = session_label(session)
        scoped.append(
            {
                "Session": name,
                "Checked in": int(present),
                "Enrolled": int(roster),
                "Attendance": f"{present} of {roster}  ({rate:.0%})" if roster else str(present),
                "sort": start.timestamp() if start else 0,
            }
        )
    scoped.sort(key=lambda row: row["sort"], reverse=True)
    if limit:
        scoped = scoped[:limit]
    if not scoped:
        empty_state("No sessions in this filter", "Pick another course or day.")
        return

    order = [row["Session"] for row in scoped]
    frame = pd.DataFrame(scoped)
    height = max(300, 44 * len(scoped) + 24)
    hover = [
        alt.Tooltip("Session:N", title="Session"),
        alt.Tooltip("Checked in:Q", title="Checked in", format="d"),
        alt.Tooltip("Enrolled:Q", title="Enrolled", format="d"),
        alt.Tooltip("Attendance:N", title="Attendance"),
    ]
    base = alt.Chart(frame).encode(
        y=alt.Y(
            "Session:N",
            sort=order,
            title=None,
            axis=alt.Axis(labelLimit=280, labelFontSize=12, ticks=False, domain=False),
        )
    )
    enrolled = base.mark_bar(size=18, color=palette()["track"], cornerRadius=3).encode(
        x=alt.X("Enrolled:Q", title="Students", scale=alt.Scale(zero=True),
                axis=alt.Axis(tickMinStep=1, format="d")),
        tooltip=hover,
    )
    checked = base.mark_bar(size=18, color="#22C55E", cornerRadius=3).encode(
        x=alt.X("Checked in:Q", scale=alt.Scale(zero=True)),
        tooltip=hover,
    )
    chart = style_chart((enrolled + checked).properties(height=height))
    st.markdown(
        '<div class="att-legend">'
        '<span><i class="att-in"></i>Checked in</span>'
        '<span><i class="att-enrolled"></i>Enrolled</span>'
        "</div>",
        unsafe_allow_html=True,
    )
    st.altair_chart(chart, use_container_width=True)