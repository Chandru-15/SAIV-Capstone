from __future__ import annotations

from html import escape, unescape
from typing import Any, Dict, Iterable, Optional

import pandas as pd
import streamlit as st

from client import (
    CSV_COLUMNS,
    REPORT_COLUMNS,
    STAFF_ROLES,
    grafana_dashboard_url,
    grafana_panel_url,
    now_sgt,
    parse_dt,
    pick,
)
from config import CHECKIN_LATENCY_TARGET_S, DEFAULT_RISK_THRESHOLD, GRAFANA_PANEL_HOURS, REFRESH_SECONDS
from data import is_assigned_session, owns_course
from theme import MARK_SVG, STATUS_COLORS


def page_header(kicker: str, title: str, lead: str = "") -> None:
    clock = now_sgt().strftime("%a %d %b · %H:%M")
    left, right = st.columns([3.6, 1.4])
    with left:
        lead_html = f'<p class="page-lead">{escape(lead)}</p>' if lead else ""
        st.markdown(
            f'<div class="page-title">{escape(title)}</div>{lead_html}',
            unsafe_allow_html=True,
        )
    with right:
        st.markdown(f'<div class="clock">{clock}</div>', unsafe_allow_html=True)


def panel_title(text: str) -> None:
    st.markdown(f'<div class="sec">{escape(text)}</div>', unsafe_allow_html=True)


def panel(title: str = "", hint: str = ""):
    box = st.container(border=True)
    if title:
        hint_html = f"<p>{escape(hint)}</p>" if hint else ""
        with box:
            st.markdown(
                f'<div class="panel-head"><h3>{escape(title)}</h3>{hint_html}</div>',
                unsafe_allow_html=True,
            )
    return box


def kpi_card(
    label: str,
    value: Any,
    hint: str = "",
    glow: str = "",
    accent: str = "",
    extra_class: str = "",
) -> str:
    klass = f"kpi {extra_class}".strip()
    hint_html = f'<div class="kpi-hint">{hint}</div>' if hint else ""
    return (
        f'<div class="{klass}">'
        f'<div class="kpi-label">{label}</div>'
        f'<div class="kpi-value">{value}</div>'
        f"{hint_html}"
        f"</div>"
    )


def stat_row(items: list) -> str:
    cells = []
    for item in items:
        label, value = item[0], item[1]
        tone = item[2] if len(item) > 2 else ""
        cells.append(
            f'<div class="stat {tone}"><div class="stat-v">{value}</div>'
            f'<div class="stat-l">{escape(str(label))}</div></div>'
        )
    return f'<div class="stats">{"".join(cells)}</div>'


def attendance_bar(
    present: int,
    flagged: int,
    pending: int,
    enrolled: int,
    rejected: int = 0,
    mini: bool = False,
) -> str:
    present, flagged, pending, rejected = (max(0, int(n or 0)) for n in (present, flagged, pending, rejected))
    used = present + flagged + pending + rejected
    total = max(int(enrolled or 0), used)
    empty = total - used
    parts = [("ok", present), ("flag", flagged), ("wait", pending), ("bad", rejected), ("off", empty)]
    segs = [f'<i class="seg {kind}" style="flex:{n} 1 0"></i>' for kind, n in parts if n > 0]
    if not segs:
        segs = ['<i class="seg off" style="flex:1 1 0"></i>']
    label = (
        f"{present} approved, {flagged} need review, {pending} pending, "
        f"{rejected} rejected, {empty} not yet in, of {total} enrolled"
    )
    klass = "abar mini" if mini else "abar"
    return f'<div class="{klass}" role="img" aria-label="{label}">{"".join(segs)}</div>'


def now_card(
    title: str,
    meta: str,
    present: int,
    flagged: int,
    pending: int,
    enrolled: int,
    rejected: int = 0,
) -> str:
    present, flagged, pending, rejected = (max(0, int(n or 0)) for n in (present, flagged, pending, rejected))
    here = present + flagged + pending
    total = max(int(enrolled or 0), here + rejected)
    share = f"{here / total:.0%}" if total else "0%"
    items = [
        ("ok", "Approved", present),
        ("flag", "Needs review", flagged),
        ("wait", "Pending", pending),
        ("bad", "Rejected", rejected),
        ("off", "Not yet in", max(0, total - here - rejected)),
    ]
    legend = "".join(f'<span><i class="sw {kind}"></i>{label} <b>{n}</b></span>' for kind, label, n in items)
    return (
        '<div class="now">'
        f'<div class="now-head"><span class="now-title">{escape(title)}</span>'
        f'<span class="now-meta">{escape(meta)}</span></div>'
        f'<div class="now-count"><b>{here}</b><span>of {total} checked in · {share}</span></div>'
        f"{attendance_bar(present, flagged, pending, total, rejected)}"
        f'<div class="now-legend">{legend}</div></div>'
    )


def risk_meter(value: Any) -> str:
    try:
        v = max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return '<span class="meter-n">—</span>'
    tone = "bad" if v >= 0.75 else "flag" if v >= 0.5 else "ok"
    return (
        f'<span class="meter"><i class="{tone}" style="width:{v * 100:.0f}%"></i></span>'
        f'<span class="meter-n">{v:.2f}</span>'
    )


def review_item(name: str, status: Any, risk: Any, when: str) -> str:
    return (
        '<div class="rq"><div class="rq-who">'
        f"<b>{escape(str(name))}</b>"
        f"<span>{escape(verification_label(status))} · {escape(when)}</span></div>"
        f'<div class="rq-risk">{risk_meter(risk)}</div></div>'
    )


def latency_bar(value: Optional[float], target: float = CHECKIN_LATENCY_TARGET_S, label: str = "Check-in p95") -> str:
    if value is None:
        return ""
    value = float(value)
    scale = max(target * 1.6, value * 1.15)
    fill = min(100.0, value / scale * 100)
    mark = min(100.0, target / scale * 100)
    tone = "ok" if value < target else "bad"
    return (
        '<div class="lat">'
        f'<div class="lat-top"><span>{escape(label)}</span><b>{value:.2f}s</b></div>'
        f'<div class="lat-track"><i class="{tone}" style="width:{fill:.1f}%"></i>'
        f'<u style="left:{mark:.1f}%"></u></div>'
        f'<div class="lat-foot"><span style="left:0%;transform:none">0s</span>'
        f'<span style="left:{mark:.1f}%">target {target:g}s</span></div>'
        "</div>"
    )


def grafana_panel(panel_id: int, height: int = 320, hours: int = 1) -> None:
    import streamlit.components.v1 as components

    components.iframe(grafana_panel_url(panel_id, hours=hours), height=height, scrolling=False)


def grafana_grid(height: int = 280, hours: int = GRAFANA_PANEL_HOURS) -> bool:
    from client import backend_exports_metrics, grafana_panels

    if not backend_exports_metrics():
        st.caption("Grafana panels appear once Prometheus is scraping SAIV metrics.")
        return False
    panels = grafana_panels()
    if not panels:
        st.caption("Couldn't load the panel list from Grafana. Use the link below to open it.")
    for start in range(0, len(panels), 2):
        cols = st.columns(2)
        for col, (pid, title) in zip(cols, panels[start:start + 2]):
            with col:
                st.markdown(f'<div class="grafana-title">{escape(title)}</div>', unsafe_allow_html=True)
                grafana_panel(pid, height=height, hours=hours)
    st.markdown(
        f'<p class="grafana-open"><a href="{grafana_dashboard_url()}" target="_blank" '
        f'rel="noopener">Open the full Grafana dashboard ↗</a></p>',
        unsafe_allow_html=True,
    )
    return True


def empty_state(title: str, body: str) -> None:
    st.markdown(
        f'<div class="empty"><strong>{escape(str(title))}</strong>{escape(str(body))}</div>',
        unsafe_allow_html=True,
    )


def status_badge(status: Optional[str]) -> str:
    raw = (status or "unknown").lower()
    return f'<span class="badge badge-{raw}">{verification_label(raw)}</span>'


VERIFY_ORDER = [
    ("approved", "Approved", STATUS_COLORS["approved"]),
    ("flagged", "Flagged", STATUS_COLORS["flagged"]),
    ("pending", "Pending", STATUS_COLORS["pending"]),
    ("rejected", "Rejected", STATUS_COLORS["rejected"]),
    ("appealed", "Appealed", STATUS_COLORS["appealed"]),
]
VERIFY_LABELS = {key: label for key, label, _color in VERIFY_ORDER}
STATUS_DOTS = {
    "approved": "🟩",
    "flagged": "🟧",
    "pending": "🟦",
    "rejected": "🟥",
    "appealed": "🟪",
    "active": "🟩",
    "scheduled": "🟦",
    "closed": "⬜",
    "cancelled": "🟥",
}


def status_dot(status: Optional[str]) -> str:
    return STATUS_DOTS.get(str(status or "").strip().lower(), "⚪")


def status_tag(status: Optional[str]) -> str:
    label = verification_label(status)
    return "—" if label == "—" else f"{status_dot(status)} {label}"


def verification_label(status: Optional[str]) -> str:
    raw = str(status or "").strip().lower()
    if not raw or raw in {"—", "-"}:
        return "—"
    return VERIFY_LABELS.get(raw, raw.replace("_", " ").title())


def health_pills(health: Dict[str, bool]) -> str:
    chips = []
    for name, ok in health.items():
        klass = "up" if ok else "down"
        chips.append(
            f'<span class="health-pill {klass}"><span class="dot"></span>{name}</span>'
        )
    return f'<div class="health-row">{"".join(chips)}</div>'


def health_list(health: Dict[str, bool]) -> str:
    rows = []
    for name, ok in health.items():
        klass = "up" if ok else "down"
        state = "Online" if ok else "Offline"
        rows.append(
            f'<div class="sys-row {klass}"><span class="sys-name">{name}</span>'
            f'<span class="sys-state">{state}</span></div>'
        )
    return f'<div class="sys-list">{"".join(rows)}</div>'


def user_role(user: Optional[dict] = None) -> str:
    blob = user or {}
    raw = blob.get("role")
    if raw is None and isinstance(blob.get("user"), dict):
        raw = blob["user"].get("role")
    if raw is None:
        return ""
    return str(raw).split(".")[-1].strip().lower()


def normalize_user(user: Optional[dict] = None) -> dict:
    if not isinstance(user, dict):
        return {}
    out = dict(user)
    nested = out.get("user")
    if isinstance(nested, dict) and not (out.get("email") or out.get("role")):
        out = {**nested, **{k: v for k, v in out.items() if k != "user"}}
    role = user_role(out)
    if role:
        out["role"] = role
    if out.get("full_name"):
        out["full_name"] = plain_name(out["full_name"])
    return out


def plain_name(value: Any) -> str:
    return unescape(str(value)) if value is not None else ""


def workspace_label(user: Optional[dict] = None) -> str:
    return {
        "student": "Student portal",
        "ta": "TA dashboard",
        "instructor": "Instructor dashboard",
        "admin": "Admin dashboard",
    }.get(user_role(user), "SAIV dashboard")


def sidebar_brand(user: Optional[dict] = None) -> None:
    st.markdown(
        f'<div class="saiv-mark">{MARK_SVG}'
        f'<div><div class="saiv-brand">SAIV</div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )


def sidebar_user(user: Optional[dict]) -> None:
    if not user:
        return
    role = escape(user_role(user).title())
    email = user.get("email") or ""
    name = escape(plain_name(user.get("full_name") or workspace_label(user)))
    safe_email = escape(email)
    st.markdown(
        f'<div class="user-chip"><div class="name">{name}</div>'
        f'<div class="meta" title="{escape(email, quote=True)}">'
        f'<span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'
        f'display:block;word-break:keep-all;overflow-wrap:normal;">{safe_email}</span></div>'
        f'<span class="role-pill">{role}</span></div>',
        unsafe_allow_html=True,
    )


NAV_PAGES = [
    ("Overview", "overview"),
    ("Sessions", "sessions"),
    ("Check-ins", "checkins"),
    ("My Attendance", "my_attendance"),
    ("Face Requests", "face_requests"),
    ("Administration", "admin_ops"),
    ("System Metrics", "metrics"),
    ("Audit Log", "audit"),
]
NAV_KEYS = {key for _, key in NAV_PAGES}
NAV_GROUPS = {
    "overview": "Attendance",
    "sessions": "Attendance",
    "checkins": "Attendance",
    "face_requests": "Attendance",
    "admin_ops": "Manage",
    "metrics": "Monitor",
    "audit": "Monitor",
}
NAV_ALIASES = {
    **{label: key for label, key in NAV_PAGES},
    "Session Schedule": "sessions",
    "Audit": "audit",
    "Audit Logs": "audit",
    "Metrics": "metrics",
    "Analytics": "metrics",
    "Admin · Operations": "admin_ops",
    "Admin · Assignments": "admin_ops",
    "Assignments": "admin_ops",
}


def nav_label(text: str) -> None:
    st.markdown(f'<p class="nav-label">{text}</p>', unsafe_allow_html=True)


def live_badge() -> None:
    stamp = st.session_state.get("_live_stamp") or ""
    extra = f" · {stamp} SGT" if stamp else ""
    st.markdown(
        f'<div class="live-dot">Live · {REFRESH_SECONDS}s{extra}</div>',
        unsafe_allow_html=True,
    )


def normalize_nav(value: Optional[str] = None) -> str:
    current = value if value is not None else st.session_state.get("nav", "overview")
    current = NAV_ALIASES.get(current, current)
    return current if current in NAV_KEYS else "overview"


def pages_for(user: Optional[dict] = None) -> list:
    role = user_role(user)

    if role == "student":
        return [("My Attendance", "my_attendance")]

    pages = [
        ("Overview", "overview"),
        ("Sessions", "sessions"),
        ("Check-ins", "checkins"),
    ]
    if role == "ta":
        return pages

    if role == "admin":
        pages.append(("Face Requests", "face_requests"))
        pages.append(("Administration", "admin_ops"))
        pages.append(("System Metrics", "metrics"))
        pages.append(("Audit Log", "audit"))

    return pages


def render_nav_tabs(user: Optional[dict] = None) -> str:
    allowed = pages_for(user)
    allowed_keys = {key for _, key in allowed}
    current = normalize_nav()
    if current not in allowed_keys:
        current = allowed[0][1] if allowed else "overview"
        st.session_state.nav = current
    last_group = None
    for label, key in allowed:
        group = NAV_GROUPS.get(key)
        if group and group != last_group and len(allowed) > 1:
            nav_label(group)
            last_group = group
        active = current == key
        clicked = st.button(
            label,
            key=f"navtab_{key}",
            use_container_width=True,
            type="primary" if active else "secondary",
        )
        if clicked and not active:
            st.session_state.nav = key
            st.rerun()
    st.session_state.nav = current
    return current


def fmt_dt(value: Any, fmt: str = "%d %b %H:%M") -> str:
    dt = parse_dt(value)
    return dt.strftime(fmt) if dt else "—"


def fmt_pct(value: Any) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number <= 1:
        number *= 100
    return f"{number:.0f}%"


def fmt_score(value: Any) -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def user_directory(client, extra_ids: Optional[Iterable[str]] = None) -> Dict[str, dict]:
    cached = st.session_state.get("_user_dir")
    if isinstance(cached, dict) and extra_ids is None:
        return cached

    directory: Dict[str, dict] = dict(cached) if isinstance(cached, dict) else {}
    if user_role(st.session_state.get("user")) == "admin" and not cached:
        for user in client.gather_all(client.list_users, page_size=100, cap=1000):
            uid = user.get("id")
            if uid:
                directory[uid] = normalize_user(user)

    missing = [uid for uid in (extra_ids or []) if uid and uid not in directory]
    if missing:
        for uid, user in client.get_users(missing).items():
            directory[uid] = normalize_user(user)

    st.session_state._user_dir = directory
    return directory


def display_name(record: dict, directory: Optional[dict] = None) -> str:
    name = pick(record, "student_name", "full_name", "user_email", "student_email")
    if name:
        return plain_name(name)
    uid = pick(record, "student_id", "user_id")
    if directory and uid in directory:
        return directory[uid].get("full_name") or directory[uid].get("email") or str(uid)[:8]
    return str(uid)[:8] + "…" if uid else "—"


def risk_threshold_for(session: Optional[dict], course: Optional[dict] = None) -> float:
    for source in (session or {}, course or {}):
        value = source.get("risk_threshold")
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    return DEFAULT_RISK_THRESHOLD


def course_label(course: dict) -> str:
    code = course.get("code") or "COURSE"
    name = course.get("name") or ""
    return f"{code} — {name}" if name else str(code)


def session_label(session: dict, courses: Optional[dict] = None) -> str:
    name = session.get("name") or "Untitled session"
    code = session.get("course_code")
    if not code and courses:
        course = courses.get(session.get("course_id") or "")
        if course:
            code = course.get("code")
    return f"{code} · {name}" if code else name


def checkins_to_frame(
    rows: list,
    directory: Optional[dict] = None,
    sessions: Optional[dict] = None,
) -> pd.DataFrame:
    records = []
    for row in rows:
        sid = row.get("session_id")
        session_name = pick(row, "session_name")
        if not session_name and sessions and sid in sessions:
            session_name = sessions[sid].get("name")
        records.append(
            {
                "Student": display_name(row, directory),
                "Session": session_name or (str(sid)[:8] + "…" if sid else "—"),
                "Verification": status_tag(row.get("status")),
                "Checked in": fmt_dt(row.get("checked_in_at")),
                "Risk": pick(row, "risk_score", default=None),
                "Liveness": pick(row, "liveness_score", default=None),
                "Face match": pick(row, "face_match_score", default=None),
                "id": row.get("id"),
                "student_id": row.get("student_id"),
                "session_id": sid,
            }
        )
    return pd.DataFrame(records)


def student_email(record: dict, directory: Optional[dict] = None) -> str:
    email = pick(record, "student_email", "email", "user_email")
    if email:
        return str(email)
    uid = pick(record, "student_id", "user_id")
    if directory and uid in directory:
        return str(directory[uid].get("email") or "")
    return ""


def course_for_row(row: dict, sessions: Optional[dict] = None, courses: Optional[dict] = None) -> str:
    code = pick(row, "course_code", "course")
    sid = row.get("session_id")
    session = (sessions or {}).get(sid) if sid else None
    if not code and session:
        code = session.get("course_code")
        cid = session.get("course_id")
        if not code and courses and cid in courses:
            course = courses[cid]
            code = course.get("code") or course.get("name")
    return str(code or "")


def gradebook_csv(
    rows: list,
    directory: Optional[dict] = None,
    sessions: Optional[dict] = None,
    courses: Optional[dict] = None,
) -> pd.DataFrame:
    records = []
    for row in rows:
        checked = parse_dt(row.get("checked_in_at"))
        sid = row.get("session_id")
        session = (sessions or {}).get(sid) if sid else None
        session_start = parse_dt((session or {}).get("scheduled_start")) if session else None
        records.append(
            {
                "Student Name": display_name(row, directory),
                "Student Email": student_email(row, directory),
                "Course": course_for_row(row, sessions, courses),
                "Session Date": session_start.strftime("%Y-%m-%d") if session_start else (checked.strftime("%Y-%m-%d") if checked else ""),
                "Check-in Time": checked.strftime("%Y-%m-%d %H:%M:%S") if checked else (row.get("checked_in_at") or ""),
                "Status": verification_label(row.get("status")),
                "Risk Score": pick(row, "risk_score", default=""),
                "Check-in ID": row.get("id") or "",
                "Student ID": row.get("student_id") or "",
                "Session ID": sid or "",
                "Verification Status": verification_label(row.get("status")),
                "Liveness Score": pick(row, "liveness_score", default=""),
                "Face Match Score": pick(row, "face_match_score", default=""),
            }
        )
    return pd.DataFrame(records, columns=CSV_COLUMNS)


def export_buttons(frame: pd.DataFrame, stem: str, key_prefix: str = "export") -> None:
    st.markdown('<div style="margin-top: 16px;"></div>', unsafe_allow_html=True)

    official_cols = [col for col in REPORT_COLUMNS if col in frame.columns]
    official = frame[official_cols] if official_cols else frame
    show_pdf = can_export_pdf(st.session_state.get("user"))
    if show_pdf:
        csv_col, json_col, pdf_col = st.columns(3)
    else:
        csv_col, json_col = st.columns(2)
        pdf_col = None
    with csv_col:
        st.download_button(
            "Export CSV",
            help="CSV for gradebook upload",
            data=official.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"{stem}.csv",
            mime="text/csv; charset=utf-8",
            use_container_width=True,
            key=f"{key_prefix}_csv",
        )
    with json_col:
        st.download_button(
            "Export JSON",
            data=frame.to_json(orient="records", date_format="iso").encode("utf-8"),
            file_name=f"{stem}.json",
            mime="application/json",
            use_container_width=True,
            key=f"{key_prefix}_json",
        )
    if pdf_col is not None:
        with pdf_col:
            from pdf_export import attendance_pdf

            st.download_button(
                "Export PDF",
                help="Official record for filing",
                data=attendance_pdf(official, title="SAIV attendance report"),
                file_name=f"{stem}.pdf",
                mime="application/pdf",
                use_container_width=True,
                key=f"{key_prefix}_pdf",
            )


def render_html_table(df: pd.DataFrame, height: int = 360) -> None:
    if df.empty:
        return

    display_df = df.drop(columns=[c for c in ("id", "student_id", "session_id") if c in df.columns])

    headers = "".join(f"<th>{escape(str(col))}</th>" for col in display_df.columns)

    rows_html = []
    for _, row in display_df.iterrows():
        cells = []
        for col in display_df.columns:
            val = row[col]
            cell_content = ""
            if not isinstance(val, (list, dict, tuple)) and pd.isna(val):
                val = "—"

            if col == "Verification" or col == "Status":
                raw_status = str(val).split()[-1].lower() if val != "—" else ""
                if raw_status in VERIFY_LABELS:
                    cell_content = status_badge(raw_status)
                else:
                    cell_content = str(val)
            elif col == "Risk" or col == "Risk Score":
                cell_content = risk_meter(val)
            elif col in ("Liveness", "Face match", "Liveness Score", "Face Match Score"):
                cell_content = fmt_score(val) if val != "—" else "—"
            elif isinstance(val, (list, tuple)):
                cell_content = "<br>".join(
                    f'<span style="white-space:nowrap">{escape(str(item))}</span>' for item in val
                ) or "—"
            elif isinstance(val, (int, float)):
                cell_content = str(val)
            else:
                cell_content = escape(str(val))

            cells.append(f"<td>{cell_content}</td>")
        rows_html.append(f"<tr>{''.join(cells)}</tr>")

    table_html = f"""
    <div class="table-wrapper" style="max-height: {height}px; overflow-y: auto; overflow-x: auto; border: 1px solid var(--line); border-radius: 10px;">
        <table class="custom-table">
            <thead><tr>{headers}</tr></thead>
            <tbody>{''.join(rows_html)}</tbody>
        </table>
    </div>
    """
    st.markdown(table_html, unsafe_allow_html=True)


def dataframe(
    df: pd.DataFrame,
    height: int = 360,
    empty_title: str = "No check-ins yet",
    empty_body: str = "Open a session or wait for students to check in.",
) -> None:
    if df.empty:
        empty_state(empty_title, empty_body)
        return

    render_html_table(df, height=height)


def is_staff(user: Optional[dict]) -> bool:
    return user_role(user) in STAFF_ROLES


def is_admin(user: Optional[dict]) -> bool:
    return user_role(user) == "admin"


def is_student(user: Optional[dict]) -> bool:
    return user_role(user) == "student"


def can_manage_sessions(user: Optional[dict]) -> bool:
    return user_role(user) in {"instructor", "admin"}


def can_cancel_session(user: Optional[dict]) -> bool:
    return user_role(user) in {"instructor", "admin"}


def can_edit_session(user: Optional[dict], session: Optional[dict], course_map: Optional[dict] = None) -> bool:
    role = user_role(user)
    if role == "admin":
        return True
    if role == "instructor":
        return owns_course(user, session, course_map)
    return False


def can_operate_session(user: Optional[dict], session: Optional[dict], course_map: Optional[dict] = None) -> bool:
    role = user_role(user)
    if role == "admin":
        return True
    if role == "instructor":
        return owns_course(user, session, course_map)
    if role == "ta":
        return is_assigned_session(user, session)
    return False


def can_review_checkins(user: Optional[dict]) -> bool:
    return user_role(user) in {"instructor", "admin"}


def can_view_flagged_checkins(user: Optional[dict]) -> bool:
    return user_role(user) in STAFF_ROLES


def can_export_pdf(user: Optional[dict]) -> bool:
    return user_role(user) in {"instructor", "admin"}


def enable_click_outside() -> None:
    import streamlit.components.v1 as components
    components.html(
        """
        <script>
        const doc = window.parent.document;
        doc.addEventListener('click', function(e) {
            if (!e.target.closest('[data-baseweb="select"]') && !e.target.closest('[data-baseweb="popover"]')) {
                const openDropdown = doc.querySelector('[data-baseweb="select"] [aria-expanded="true"]');
                if (openDropdown) {
                    openDropdown.dispatchEvent(new KeyboardEvent('keydown', {'key': 'Escape', 'bubbles': true}));
                    openDropdown.blur();
                }
            }
        });
        </script>
        """,
        height=0,
        width=0,
    )


def scroll_to_top() -> None:
    import streamlit.components.v1 as components
    components.html(
        """
        <script>
        const w = window.parent;
        w.scrollTo(0, 0);
        </script>
        """,
        height=0,
        width=0,
    )

def queue_confirmation(title: str, message: str, kind: str = "success") -> None:
    st.session_state["_confirmation"] = {"title": title, "message": message, "kind": kind}


def show_confirmation() -> None:
    note = st.session_state.pop("_confirmation", None)
    if not note:
        return
    show = st.warning if note.get("kind") == "warning" else st.success
    if hasattr(st, "dialog"):
        @st.dialog(note["title"])
        def _dialog() -> None:
            show(note["message"])
            if st.button("OK", use_container_width=True, key="confirm_ok"):
                st.rerun()

        _dialog()
    else:
        show(f"**{note['title']}** — {note['message']}")
    st.toast(note["title"], icon="✅" if note.get("kind") != "warning" else "⚠️")