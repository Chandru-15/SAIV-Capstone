from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from html import escape

import pandas as pd
import streamlit as st

from access import require_staff
from charts import STATUS_COLORS, bar_chart
from client import SGT, CSV_COLUMNS, backend_supports, iso, now_sgt, parse_dt, pick
from config import GOOD_ATTENDANCE, LOW_ATTENDANCE
from data import attendees_for, enrolled_for, load_bundle, owns_course
from ui import (
    can_cancel_session,
    can_edit_session,
    can_manage_sessions,
    can_operate_session,
    checkins_to_frame,
    course_label,
    dataframe,
    empty_state,
    export_buttons,
    fmt_pct,
    gradebook_csv,
    live_badge,
    now_card,
    page_header,
    panel,
    panel_title,
    plain_name,
    queue_confirmation,
    risk_threshold_for,
    show_confirmation,
    session_label,
    stat_row,
    status_tag,
    user_directory,
    user_role,
    verification_label,
)

SESSION_TYPES = ["lecture", "tutorial", "lab", "exam"]
TIME_STEP = 300

def _flash(message: str, title: str = "Done", kind: str = "success") -> None:
    queue_confirmation(title, message, kind)

def _section(title: str, hint: str = "") -> None:
    st.markdown(f'<div class="form-sec">{escape(title)}</div>', unsafe_allow_html=True)
    if hint:
        st.markdown(f'<p class="form-hint">{escape(hint)}</p>', unsafe_allow_html=True)

def _snap(dt: datetime):
    return dt.time().replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)

def _combine(day, moment) -> datetime:
    return datetime.combine(day, moment).replace(tzinfo=SGT)

def _styled_button(column, kind: str, label: str, key: str) -> bool:
    with column:
        st.markdown(f'<div class="btn-mark btn-{kind}"></div>', unsafe_allow_html=True)
        return st.button(label, use_container_width=True, key=key)

def _check_times(start: datetime, end: datetime, opens: datetime, closes: datetime) -> str:
    if end <= start:
        return "The end time must be after the start time."
    if closes <= opens:
        return "Check-in must close after it opens."
    if opens >= end:
        return "Check-in must open before the session ends."
    return ""

def _scope_note(user: dict) -> str:
    role = user_role(user)
    if role == "instructor":
        return "Showing only the courses you teach and the sessions you created."
    if role == "ta":
        return "Showing only the classes you are assigned to conduct."
    if role == "admin":
        return "Showing every course and session."
    return ""

def _session_form(prefix: str, d: dict) -> dict:
    _section("Details")
    d1, d2 = st.columns(2)
    name = d1.text_input("Session name", value=d["name"], placeholder="e.g. Neural networks", key=f"{prefix}_name")
    venue = d2.text_input("Venue", value=d["venue"], placeholder="e.g. LT1", key=f"{prefix}_venue")
    types = SESSION_TYPES if d["type"] in SESSION_TYPES else SESSION_TYPES + [d["type"]]
    session_type = st.selectbox("Type", types, index=types.index(d["type"]), key=f"{prefix}_type",
                                disabled=prefix.startswith("edit_"),
                                help="The type cannot be changed after creation." if prefix.startswith("edit_") else None)

    _section("Schedule", "All times are Singapore time (SGT).")
    
    s1, s2 = st.columns(2)
    start_d = s1.date_input("Start date", value=d["start"].date(), key=f"{prefix}_start_d")
    start_t = s2.time_input("Start time", value=_snap(d["start"]), step=TIME_STEP, key=f"{prefix}_start_t")
    
    s3, s4 = st.columns(2)
    end_d = s3.date_input("End date", value=d["end"].date(), key=f"{prefix}_end_d")
    end_t = s4.time_input("End time", value=_snap(d["end"]), step=TIME_STEP, key=f"{prefix}_end_t")

    _section("Check-in window", "Students can only check in between these times.")
    
    w1, w2 = st.columns(2)
    open_d = w1.date_input("Opens date", value=d["opens"].date(), key=f"{prefix}_open_d")
    open_t = w2.time_input("Opens time", value=_snap(d["opens"]), step=TIME_STEP, key=f"{prefix}_open_t")
    
    w3, w4 = st.columns(2)
    close_d = w3.date_input("Closes date", value=d["closes"].date(), key=f"{prefix}_close_d")
    close_t = w4.time_input("Closes time", value=_snap(d["closes"]), step=TIME_STEP, key=f"{prefix}_close_t")

    _section("Location", "Leave blank to use the course venue. With no venue coordinates "
                         "anywhere, check-ins are not location-verified.")
    l1, l2, l3 = st.columns(3)
    lat_txt = l1.text_input("Latitude", value=d.get("lat", ""), key=f"{prefix}_lat")
    lon_txt = l2.text_input("Longitude", value=d.get("lon", ""), key=f"{prefix}_lon")
    radius_txt = l3.text_input("Radius (m)", value=d.get("radius", ""), key=f"{prefix}_radius")

    geo: dict = {}
    geo_error = ""
    for key, txt, low, high, label in (
        ("venue_latitude", lat_txt, -90, 90, "Latitude"),
        ("venue_longitude", lon_txt, -180, 180, "Longitude"),
        ("geofence_radius_meters", radius_txt, 1, 5000, "Radius"),
    ):
        txt = (txt or "").strip()
        if not txt:
            continue
        try:
            number = float(txt)
        except ValueError:
            geo_error = f"{label} must be a number."
            break
        if not low <= number <= high:
            geo_error = f"{label} must be between {low} and {high}."
            break
        geo[key] = number
    if not geo_error and ("venue_latitude" in geo) != ("venue_longitude" in geo):
        geo_error = "Give both latitude and longitude, or neither."

    _section("Verification")
    v1, v2 = st.columns(2)
    liveness = v1.checkbox("Require liveness check", value=d["liveness"], key=f"{prefix}_liveness")
    face = v2.checkbox("Require face match", value=d["face"], key=f"{prefix}_face")
    risk = st.slider(
        "Risk threshold (flag at or above)",
        0.0,
        1.0,
        d["risk"],
        0.05,
        format="%.2f",
        key=f"{prefix}_risk",
    )

    start = _combine(start_d, start_t)
    end = _combine(end_d, end_t)
    opens = _combine(open_d, open_t)
    closes = _combine(close_d, close_t)
    return {
        "name": name.strip(),
        "session_type": session_type,
        "scheduled_start": start,
        "scheduled_end": end,
        "checkin_opens_at": opens,
        "checkin_closes_at": closes,
        "venue_name": venue.strip(),
        "require_liveness_check": liveness,
        "require_face_match": face,
        "risk_threshold": risk,
        **geo,
        "_error": geo_error,
    }

def _to_payload(values: dict) -> dict:
    payload = {
        k: v for k, v in values.items()
        if not k.startswith("_") and not (k == "venue_name" and v == "")
    }
    for key in ("scheduled_start", "scheduled_end", "checkin_opens_at", "checkin_closes_at"):
        payload[key] = iso(values[key])
    return payload

def _risk_default(source: dict) -> float:
    try:
        value = source.get("risk_threshold")
        return float(value) if value is not None else 0.5
    except (TypeError, ValueError):
        return 0.5

def render_sessions(client) -> None:
    if not require_staff(client):
        return
    page_header("Schedule", "Sessions", "Browse class sessions, then pick one below to see who has checked in and to open, close or edit it.")
    live_badge()

    show_confirmation()
    if st.session_state.pop("_sess_reset_filters", False):
        for key in ("sess_filter_course", "sess_filter_status", "sess_filter_type", "sess_filter_query"):
            st.session_state.pop(key, None)
    pending = st.session_state.pop("_sess_pending_detail", None)
    if pending:
        st.session_state["detail_choose"] = pending

    bundle = load_bundle(client)
    all_courses = bundle["courses"]
    user = st.session_state.get("user") or {}
    role = user_role(user)

    if role == "instructor":
        courses = [c for c in all_courses if owns_course(user, c)]
        if not courses and backend_supports("POST", "/admin/assignments/instructor") is False:
            courses = list(all_courses)
            st.caption("Course assignment isn't available on this backend yet, so you can "
                       "schedule sessions in any course.")
    else:
        courses = all_courses

    course_map = {c["id"]: c for c in courses if c.get("id")}
    sessions = bundle["sessions"]
    checkins = bundle["checkins"]
    manage = can_manage_sessions(user)

    note = _scope_note(user)
    if note:
        st.markdown(f'<p class="scope-note">{escape(note)}</p>', unsafe_allow_html=True)

    if not manage:
        _sessions_tab(client, sessions, course_map, checkins)
        return

    tabs = st.tabs(["All sessions", "Course students", "New session"])
    with tabs[0]:
        _sessions_tab(client, sessions, course_map, checkins)
    with tabs[1]:
        _course_students_tab(client, courses)
    with tabs[2]:
        _create_tab(client, courses)


def _course_students_tab(client, courses: list) -> None:
    if not courses:
        empty_state("No courses", "Courses you teach appear here.")
        return
    by_id = {c["id"]: c for c in sorted(courses, key=lambda c: str(c.get("code") or "")) if c.get("id")}
    if st.session_state.get("cs_course") not in by_id:
        st.session_state.pop("cs_course", None)
    course_id = st.selectbox("Course", list(by_id), format_func=lambda cid: course_label(by_id[cid]),
                             key="cs_course")
    data = client.course_students(course_id)
    if data is None:
        st.error(client.last_error or "Couldn't load the students for this course.")
        return
    students = [s for s in data.get("students") or [] if s.get("is_active", True)]
    st.caption(f"{len(students)} student(s) enrolled in {by_id[course_id].get('code')}.")
    rows = []
    for s in students:
        enrolled = parse_dt(s.get("enrolled_at"))
        rows.append({
            "Student": plain_name(s.get("full_name") or ""),
            "Email": s.get("email") or "",
            "Enrolled": enrolled.strftime("%d %b %Y") if enrolled else "—",
        })
    dataframe(pd.DataFrame(rows), height=420, empty_title="No students enrolled",
              empty_body="An admin enrols students under Administration → Enrol students.")


def _session_students_section(client, session_id: str) -> None:
    _section("Students in this session")
    records = client.session_students(session_id)
    if records is None:
        st.caption("The student list isn't available right now.")
        return
    present = sum(1 for r in records
                  if str(r.get("status") or "absent").lower() not in {"absent", "rejected"})
    st.caption(f"{present} of {len(records)} expected student(s) have checked in. "
               "Tutorial and lab sessions list only their group.")
    rows = []
    for r in records:
        status = str(r.get("status") or "absent").lower()
        checked = parse_dt(r.get("checked_in_at"))
        rows.append({
            "Student": plain_name(r.get("student_name") or ""),
            "Email": r.get("student_email") or "",
            "Status": "Absent" if status == "absent" else status,
            "Checked in": checked.strftime("%d %b %H:%M") if checked else "—",
        })
    dataframe(pd.DataFrame(rows), height=360, empty_title="No students expected",
              empty_body="Enrol students in this course to see them here.")

def _sessions_tab(client, sessions: list, course_map: dict, checkins: list) -> None:
    if not sessions:
        empty_state("No sessions yet", "Sessions you are responsible for will appear here.")
        return

    counts = Counter(str(s.get("status") or "").lower() for s in sessions)
    st.markdown(
        stat_row(
            [
                ("Sessions", len(sessions), ""),
                ("Open now", counts.get("active", 0), "good" if counts.get("active") else ""),
                ("Scheduled", counts.get("scheduled", 0), ""),
                ("Closed", counts.get("closed", 0), ""),
            ]
        ),
        unsafe_allow_html=True,
    )

    filtered = _filters_and_table(sessions, course_map, checkins)
    _detail_section(client, filtered, course_map, checkins)

def _filters_and_table(sessions: list, course_map: dict, checkins: list) -> list:
    panel_title("Find a session")
    course_ids = [""]
    course_names = {"": "All courses"}
    for session in sessions:
        cid = session.get("course_id")
        if cid and cid not in course_names:
            course_ids.append(cid)
            course = course_map.get(cid) or {}
            course_names[cid] = course.get("code") or session.get("course_code") or str(cid)[:8]

    f1, f2, f3, f4 = st.columns((1.2, 1, 1, 1.8))
    course_id = f1.selectbox("Course", course_ids, format_func=lambda c: course_names.get(c, c), key="sess_filter_course")
    status = f2.selectbox(
        "Status",
        ["all", "scheduled", "active", "closed", "cancelled"],
        format_func=str.title,
        key="sess_filter_status",
    )
    types = ["all"] + sorted({str(s.get("session_type") or "lecture") for s in sessions})
    stype = f3.selectbox("Type", types, format_func=str.title, key="sess_filter_type")
    query = f4.text_input("Search", placeholder="Session name or venue", key="sess_filter_query")

    filtered = []
    rows = []
    for session in sessions:
        if course_id and session.get("course_id") != course_id:
            continue
        if status != "all" and str(session.get("status") or "").lower() != status:
            continue
        if stype != "all" and str(session.get("session_type") or "lecture").lower() != stype:
            continue
        label = session_label(session, course_map)
        hay = " ".join(str(x or "") for x in (label, session.get("venue_name"), session.get("name"))).lower()
        if query and query.strip().lower() not in hay:
            continue
        filtered.append(session)
        enrolled = enrolled_for(session)
        attendees = attendees_for(session, checkins)
        rows.append(
            {
                "Session": label,
                "Type": str(session.get("session_type") or "lecture").title(),
                "Status": status_tag(session.get("status")),
                "Starts": _fmt(session.get("scheduled_start")),
                "Ends": _fmt(session.get("scheduled_end")),
                "Group": session.get("group_name") or "All students",
                "Venue": session.get("venue_name") or "—",
                "Checked in": f"{attendees} / {enrolled}" if enrolled else str(attendees),
                "Attendance": round((attendees / enrolled) * 100) if enrolled else 0,
            }
        )

    if not rows:
        empty_state("No sessions match", "Loosen the filters to see more sessions.")
        return []

    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
        height=min(420, 38 * (len(rows) + 1) + 3),
        column_config={
            "Type": st.column_config.TextColumn("Type", width="small"),
            "Status": st.column_config.TextColumn("Status", width="small"),
            "Starts": st.column_config.TextColumn("Starts", width="small"),
            "Ends": st.column_config.TextColumn("Ends", width="small"),
            "Checked in": st.column_config.TextColumn("Checked in", width="small"),
            "Attendance": st.column_config.ProgressColumn(
                "Attendance", min_value=0, max_value=100, format="%d%%", width="small"
            ),
        },
    )
    st.caption(f"{len(rows)} session{'s' if len(rows) != 1 else ''} shown.")
    return filtered

def _create_tab(client, courses: list) -> None:
    user = st.session_state.get("user") or {}
    if not can_manage_sessions(user):
        st.info("Only instructors and admins can create sessions.")
        return
    if not courses:
        empty_state(
            "No courses assigned to you",
            "An admin assigns instructors to courses under Administration → ② Instructors. "
            "Once you're assigned, your courses appear here.",
        )
        return

    now = now_sgt()
    first = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

    with panel("Create a session", "Schedule a class and set its check-in rules."):
        _section("Course")
        course = st.selectbox("Course", courses, format_func=course_label, key="create_course")
        defaults = {
            "name": "",
            "venue": course.get("venue_name") or "",
            "type": "lecture",
            "start": first,
            "end": first + timedelta(hours=2),
            "opens": first - timedelta(minutes=15),
            "closes": first + timedelta(minutes=30),
            "liveness": True,
            "face": False,
            "risk": _risk_default(course),
        }
        values = _session_form("create", defaults)
        group_id, group_error = _group_picker(client, course, values["session_type"], "create_group")
        st.markdown('<div style="margin-top: 1.25rem;"></div>', unsafe_allow_html=True)
        
        submitted = st.button("Create session", use_container_width=True, key="ok_create_session")

    if not submitted:
        return
    if not values["name"]:
        st.error("Give the session a name.")
        return
    if values.get("_error"):
        st.error(values["_error"])
        return
    problem = _check_times(
        values["scheduled_start"], values["scheduled_end"], values["checkin_opens_at"], values["checkin_closes_at"]
    )
    if problem:
        st.error(problem)
        return

    if group_error:
        st.error(group_error)
        return
    payload = {"course_id": course["id"], **_to_payload(values), "status": "scheduled"}
    if group_id:
        payload["group_id"] = group_id
    with st.spinner("Creating session…"):
        ok, result = client.create_session(payload)
    if not ok:
        st.error(result)
        return

    for key in [k for k in st.session_state.keys() if str(k).startswith("create_") and k != "create_course"]:
        st.session_state.pop(key, None)
    st.cache_data.clear()
    st.session_state["_sess_reset_filters"] = True
    if isinstance(result, dict) and result.get("id"):
        st.session_state["_sess_pending_detail"] = result["id"]
    _flash(f"“{values['name']}” has been scheduled. It's now selected under "
           f"All sessions → Manage a session. Next, assign its TAs there.",
           title="Session created")
    st.rerun()

def _detail_section(client, sessions: list, course_map: dict, all_checkins: list) -> None:
    panel_title("Manage a session")
    if user_role(st.session_state.get("user")) == "ta":
        st.caption("Choose one of your sessions to see who has checked in, and to open or close it.")
    else:
        st.caption("Choose a session to see who has checked in, and to open, close, edit or cancel it.")
    if not sessions:
        empty_state("Nothing to open", "No session matches the filters above.")
        return

    user = st.session_state.get("user") or {}
    by_id = {s["id"]: s for s in sessions if s.get("id")}
    ids = list(by_id)
    if st.session_state.get("detail_choose") not in ids:
        st.session_state.pop("detail_choose", None)
    session_id = st.selectbox(
        "Choose a session",
        ids,
        format_func=lambda sid: session_label(by_id[sid], course_map),
        key="detail_choose",
    )
    selected = by_id[session_id]
    editable = can_edit_session(user, selected, course_map)
    operable = can_operate_session(user, selected, course_map)

    detail = client.get_session(session_id) or selected
    stats = client.session_statistics(session_id) or {}
    checkins = [c for c in all_checkins if c.get("session_id") == session_id]
    if not checkins:
        checkins = client.session_checkins(session_id)

    if editable and st.session_state.get("edit_session_id") == session_id:
        _edit_panel(client, detail, session_id)
        return

    enrolled = int(pick(stats, "total_enrolled", "enrolled", default=0) or 0) or enrolled_for(selected)
    by_status = pick(stats, "by_status", default={}) or {}
    if not by_status:
        by_status = dict(Counter(str(c.get("status") or "pending").lower() for c in checkins))
    checked = attendees_for(detail, checkins)
    flagged = pick(stats, "flagged_count", default=by_status.get("flagged", 0)) or by_status.get("flagged", 0)
    approved = pick(stats, "approved_count", default=by_status.get("approved", 0)) or by_status.get("approved", 0)
    rate = (checked / enrolled) if enrolled else 0
    avg_risk = pick(stats, "average_risk_score", default=None)
    if avg_risk is None:
        scores = [float(c["risk_score"]) for c in checkins if c.get("risk_score") is not None]
        avg_risk = sum(scores) / len(scores) if scores else 0

    rejected_n = int(by_status.get("rejected", 0) or 0)
    flagged_n = int(by_status.get("flagged", 0) or 0) + int(by_status.get("appealed", 0) or 0)
    pending_n = int(by_status.get("pending", 0) or 0)
    approved_n = int(approved or 0)
    start_dt = parse_dt(detail.get("scheduled_start"))
    meta_line = " · ".join(
        part
        for part in (
            str(detail.get("status") or "").title(),
            detail.get("venue_name") or "",
            f"starts {start_dt.strftime('%d %b %H:%M')}" if start_dt else "",
        )
        if part
    )

    st.markdown(
        now_card(
            session_label(detail, course_map),
            meta_line,
            approved_n,
            flagged_n,
            pending_n,
            enrolled,
            rejected_n,
        ),
        unsafe_allow_html=True,
    )

    st.markdown(
        stat_row(
            [
                ("Attendance", fmt_pct(rate), "good" if rate >= GOOD_ATTENDANCE else ("warn" if enrolled and rate < LOW_ATTENDANCE else "")),
                ("Enrolled", enrolled, ""),
                ("Flagged", flagged, "warn" if flagged else ""),
                ("Average risk", f"{float(avg_risk):.2f}", ""),
            ]
        ),
        unsafe_allow_html=True,
    )

    meta = st.columns(3)
    meta[0].markdown(f"**Check-in window**  \n{_fmt(detail.get('checkin_opens_at'))} → {_fmt(detail.get('checkin_closes_at'))}")
    group = detail.get("group_name") or selected.get("group_name")
    meta[1].markdown(f"**Venue · who attends**  \n{detail.get('venue_name') or '—'} · "
                     f"{'group ' + group if group else 'all enrolled students'}")
    threshold = risk_threshold_for(detail, course_map.get(detail.get("course_id")))
    checks = [name for flag, name in ((detail.get("require_liveness_check"), "liveness"),
                                      (detail.get("require_face_match"), "face match")) if flag]
    meta[2].markdown(
        f"**Verification**  \n"
        f"{' + '.join(checks).capitalize() + ' required' if checks else 'No biometric checks'}"
        f" · flagged at risk ≥ {threshold:.2f}"
    )

    current = str(detail.get("status") or "scheduled").lower()
    if editable or operable:
        _section("Actions")
        st.caption("Open check-in lets students check in during the check-in window. "
                   "Close session ends it. Cancel marks it as not happening.")
        actions = st.columns(4)
        if editable and actions[0].button("Edit session", use_container_width=True, key="ghost_edit_session"):
            st.session_state["edit_session_id"] = session_id
            st.rerun()
        if operable and current == "scheduled":
            if _styled_button(actions[1], "info", "Open check-in", "sess_open_checkin"):
                _set_status(client, session_id, "active", detail, course_map)
        if operable and current == "active":
            if _styled_button(actions[1], "warn", "Close session", "sess_close_session"):
                _set_status(client, session_id, "closed", detail, course_map)
        if editable and can_cancel_session(user) and current in {"scheduled", "active", "closed"}:
            if _styled_button(actions[2], "danger", "Cancel session", "sess_cancel_session"):
                _set_status(client, session_id, "cancelled", detail, course_map)

    _ta_section(client, detail, session_id, editable)

    if by_status:
        _section("Check-in results")
        frame = pd.DataFrame(
            {
                "Status": [verification_label(key) for key in by_status],
                "Check-ins": list(by_status.values()),
            }
        )
        bar_chart(frame, "Status", "Check-ins", "Status", "Check-ins", color_map=STATUS_COLORS)

    _session_students_section(client, session_id)

    _section("Who has checked in")
    directory = user_directory(client, [c.get("student_id") for c in checkins])
    dataframe(checkins_to_frame(checkins, directory, {session_id: detail}), height=320)

    _section("Export attendance")
    stem = f"saiv-session-{str(session_id)[:8]}-attendance"
    cache_key = f"_register_{session_id}"
    st.caption("The register lists every enrolled student, including those marked Absent. "
               "Preparing it is recorded in the audit log as an export.")
    if st.button("Prepare attendance register", key=f"ghost_prepare_{session_id}"):
        frame = _register_frame(client, session_id, detail, course_map)
        if frame is None:
            frame = gradebook_csv(checkins, directory, {session_id: detail}, course_map)
        st.session_state[cache_key] = frame
    frame = st.session_state.get(cache_key)
    if frame is not None:
        st.caption(f"Register ready: {len(frame)} student(s). Download it below.")
        export_buttons(frame, stem, f"session_{str(session_id)[:8]}")


def _group_picker(client, course: dict, session_type: str, key: str, current=None):
    if session_type not in ("tutorial", "lab"):
        st.caption("Lectures and exams are for every enrolled student.")
        return None, None
    flag = "has_tutorials" if session_type == "tutorial" else "has_labs"
    data = client.course_groups(course["id"]) or {}
    if not data.get(flag, course.get(flag)):
        st.caption(f"This course has no {session_type} groups, so every enrolled student can attend.")
        return None, None
    groups = data.get(f"{session_type}_groups") or []
    if not groups:
        return None, (f"This course's students haven't been split into {session_type} groups yet. "
                      f"An admin does this in Administration → ④ Tutorials & labs.")
    ids = [g["id"] for g in groups]
    names = {g["id"]: f"{g['name']} · {g['size']} students" for g in groups}
    index = ids.index(current) if current in ids else 0
    picked = st.selectbox(f"{session_type.title()} group (only these students can see and attend it)",
                          ids, index=index, format_func=lambda g: names[g], key=key)
    return picked, None


def _register_frame(client, session_id: str, session: dict, course_map: dict):
    payload = client.export_session(session_id)
    if not payload or not isinstance(payload.get("records"), list):
        return None
    course = course_map.get(session.get("course_id")) or {}
    code = session.get("course_code") or course.get("code") or ""
    start = parse_dt(session.get("scheduled_start"))
    rows = []
    for rec in payload["records"]:
        checked = parse_dt(rec.get("checked_in_at"))
        status = str(rec.get("status") or "absent")
        rows.append({
            "Student Name": plain_name(rec.get("student_name") or ""),
            "Student Email": rec.get("student_email") or "",
            "Course": code,
            "Session Date": start.strftime("%Y-%m-%d") if start else "",
            "Check-in Time": checked.strftime("%Y-%m-%d %H:%M:%S") if checked else "",
            "Status": verification_label(status),
            "Risk Score": "" if rec.get("risk_score") is None else rec.get("risk_score"),
            "Student ID": rec.get("student_id") or "",
            "Session ID": session_id,
        })
    return pd.DataFrame(rows, columns=CSV_COLUMNS)


def _ta_section(client, session: dict, session_id: str, can_assign: bool) -> None:
    if backend_supports("POST", "/admin/assignments/ta") is False:
        return
    _section("Teaching assistants")
    current = client.list_tas(session_id)
    current_ids = [t["id"] for t in current if t.get("id")]
    if not can_assign:
        names = ", ".join(t.get("full_name") or t.get("email") or "" for t in current)
        st.caption(f"Assigned TAs: {names}" if names else "No TAs assigned to this session.")
        return

    directory = client.staff_directory("ta")
    by_id = {t["id"]: t for t in directory + current if t.get("id")}
    if not by_id:
        st.caption("There are no TA accounts yet. An admin can create them under Administration → Users.")
        return
    label = lambda tid: f"{plain_name(by_id[tid].get('full_name'))} · {by_id[tid].get('email')}"
    picked = st.multiselect(
        "TAs who run this session (they can open and close it)",
        list(by_id), default=current_ids, format_func=label, key=f"ta_pick_{session_id}",
    )
    if st.button("Save TAs", key=f"ok_save_tas_{session_id}"):
        errors = []
        for tid in set(picked) - set(current_ids):
            ok, err = client.assign_ta(session_id, tid)
            if not ok:
                errors.append(f"{label(tid)}: {err}")
        for tid in set(current_ids) - set(picked):
            ok, err = client.unassign_ta(session_id, tid)
            if not ok:
                errors.append(f"{label(tid)}: {err}")
        if errors:
            st.error("Some changes failed: " + "; ".join(errors))
        else:
            names = lambda ids: ", ".join(label(t).split(" · ")[0] for t in ids) or "nobody"
            added, removed = set(picked) - set(current_ids), set(current_ids) - set(picked)
            if not added and not removed:
                _flash("No changes: the TA list was already like this.", title="Nothing to save")
            else:
                lines = []
                if added:
                    lines.append(f"Added: {names(added)}.")
                if removed:
                    lines.append(f"Removed: {names(removed)}.")
                lines.append(f"Assigned TAs for {session_label(session)} are now: {names(picked)}. "
                             "They can open and close this session.")
                _flash(" ".join(lines), title="TAs saved")
            st.rerun()

def _set_status(client, session_id: str, status: str, session: dict, course_map: dict) -> None:
    ok, err = client.set_session_status(session_id, status)
    if not ok:
        st.error(err)
        return
    st.cache_data.clear()
    name = session_label(session, course_map)
    closes = parse_dt(session.get("checkin_closes_at"))
    opens = parse_dt(session.get("checkin_opens_at"))
    now = now_sgt()
    if status == "active":
        if closes and closes < now:
            _flash(f"{name} is now Active, but its check-in window closed at "
                   f"{closes.strftime('%d %b %H:%M')}, so students cannot check in. "
                   "Edit the session to extend the window.",
                   title="Session open — window already closed", kind="warning")
        elif opens and opens > now:
            _flash(f"{name} is now Active. Students can check in from "
                   f"{opens.strftime('%d %b %H:%M')} until {closes.strftime('%d %b %H:%M') if closes else '—'}.",
                   title="Check-in opened")
        else:
            _flash(f"{name} is now Active. Students can check in until "
                   f"{closes.strftime('%d %b %H:%M') if closes else 'the window closes'}.",
                   title="Check-in opened")
    elif status == "closed":
        _flash(f"{name} is closed. No more check-ins are accepted; the attendance "
               "register below is final apart from reviews.", title="Session closed")
    else:
        _flash(f"{name} has been cancelled and will not run.", title="Session cancelled",
               kind="warning")
    st.rerun()


def _edit_panel(client, detail: dict, session_id: str) -> None:
    start = parse_dt(detail.get("scheduled_start")) or now_sgt()
    end = parse_dt(detail.get("scheduled_end")) or start + timedelta(hours=2)
    opens = parse_dt(detail.get("checkin_opens_at")) or start - timedelta(minutes=15)
    closes = parse_dt(detail.get("checkin_closes_at")) or start + timedelta(minutes=30)
    defaults = {
        "name": detail.get("name") or "",
        "venue": detail.get("venue_name") or "",
        "type": str(detail.get("session_type") or "lecture").lower(),
        "start": start,
        "end": end,
        "opens": opens,
        "closes": closes,
        "liveness": bool(detail.get("require_liveness_check", True)),
        "face": bool(detail.get("require_face_match", True)),
        "risk": _risk_default(detail),
        "lat": "" if detail.get("venue_latitude") is None else str(detail["venue_latitude"]),
        "lon": "" if detail.get("venue_longitude") is None else str(detail["venue_longitude"]),
        "radius": "" if detail.get("geofence_radius_meters") is None else str(detail["geofence_radius_meters"]),
    }

    with panel("Edit session", "Change the details, schedule, and verification rules."):
        values = _session_form(f"edit_{session_id}", defaults)
        course = {"id": detail.get("course_id")}
        group_id, group_error = _group_picker(client, course, values["session_type"],
                                              f"edit_group_{session_id}", detail.get("group_id"))
        st.markdown('<div style="margin-top: 1.25rem;"></div>', unsafe_allow_html=True)
        b1, b2 = st.columns(2)
        
        save = b1.button("Save changes", use_container_width=True, key="ok_save_session")
        cancel = b2.button("Cancel", use_container_width=True, key="ghost_cancel_edit")

    if cancel:
        st.session_state.pop("edit_session_id", None)
        st.rerun()
    if not save:
        return
    if not values["name"]:
        st.error("Give the session a name.")
        return
    if values.get("_error"):
        st.error(values["_error"])
        return
    problem = _check_times(
        values["scheduled_start"], values["scheduled_end"], values["checkin_opens_at"], values["checkin_closes_at"]
    )
    if problem:
        st.error(problem)
        return

    if group_error:
        st.error(group_error)
        return
    with st.spinner("Saving changes…"):
        payload = _to_payload(values)
        payload.pop("session_type", None)
        if group_id:
            payload["group_id"] = group_id
        ok, result = client.update_session(session_id, payload)
    if not ok:
        st.error(result)
        return
    st.session_state.pop("edit_session_id", None)
    st.cache_data.clear()
    _flash("Your changes to the session have been saved.", title="Session updated")
    st.rerun()

def _fmt(value) -> str:
    dt = parse_dt(value)
    return dt.strftime("%d %b %H:%M") if dt else "—"