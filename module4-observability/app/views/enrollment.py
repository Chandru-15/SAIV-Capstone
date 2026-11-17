from __future__ import annotations

import pandas as pd
import streamlit as st

from access import deny_page, refresh_actor
from client import backend_supports
from demo import blend, is_demo, owns_course
from ui import (
    course_label,
    empty_state,
    live_badge,
    page_header,
    panel,
    panel_title,
    render_html_table,
    session_label,
    user_directory,
    user_role,
)

def _student_label(student_id: str, directory: dict) -> str:
    person = directory.get(student_id) or {}
    email = person.get("email")
    name = person.get("full_name")
    if email and name:
        return f"{email} ({name})"
    return str(email or name or student_id)[:40]

def render_enrollment(client) -> None:
    user = refresh_actor(client)
    role = user_role(user)
    if role not in {"instructor", "admin"}:
        deny_page("Access denied")
        return

    page_header("Manage", "Session Rosters", "Choose which students may check in to each session. Everyone enrolled in the course can unless you narrow it here.")
    live_badge()
    
    if (backend_supports("GET", "/sessions/{session_id}/roster") is False
            or backend_supports("POST", "/sessions/{session_id}/roster") is False):
        empty_state(
            "Per-session rosters are not available",
            "The backend has no /sessions/{id}/roster endpoint, and check-in currently "
            "only requires course enrolment, so a session roster would not restrict "
            "anyone. Use Administration → Enroll Students to manage who is in a course.",
        )
        return

    bundle = blend(client)
    courses = [c for c in bundle["courses"] if not is_demo(c)]
    if role == "instructor":
        courses = [c for c in courses if owns_course(user, c)]
    if not courses:
        if role == "admin":
            empty_state("No courses", "Create a course under Administration → Courses to get started.")
        else:
            empty_state("No courses", "No courses are assigned to you yet. Ask an admin to assign one.")
        return

    panel_title("1 · Choose a course")
    course = st.selectbox("Course", courses, format_func=course_label, key="ros_course")
    sessions = [
        s for s in bundle["sessions"]
        if not is_demo(s) and s.get("course_id") == course.get("id")
    ]
    if not sessions:
        empty_state("No sessions", "This course has no sessions yet. Create one from the Sessions page.")
        return

    enrollments = client.gather_all(
        lambda **p: client.course_roster(course["id"], **p), page_size=100, cap=500
    )
    
    if not enrollments and client.last_status == 404:
        empty_state("Backend missing endpoint", "The backend does not support fetching course rosters yet.")
        return
        
    student_ids = [str(r.get("student_id")) for r in enrollments if r.get("is_active", True) and r.get("student_id")]
    
    if not student_ids:
        if role == "admin":
            empty_state(
                "No students enrolled",
                "Enroll students under Administration → Enroll Students."
            )
        else:
            empty_state(
                "No students enrolled",
                "Nobody is enrolled in this course yet. Ask an admin to enroll students."
            )
        return

    directory = user_directory(client, student_ids)
    labels = {sid: _student_label(sid, directory) for sid in student_ids}

    panel_title("2 · Choose sessions and students")
    with panel("Change who can check in", "Select sessions and students, then add or remove them."):
        every = st.checkbox("Apply to every session in this course", key="ros_every")
        if every:
            picked_sessions = list(sessions)
        else:
            picked_sessions = st.multiselect(
                "Sessions to change", sessions, format_func=session_label, key="ros_sessions"
            )
        picked_students = st.multiselect(
            "Students to add or remove", student_ids, format_func=lambda sid: labels[sid], key="ros_students"
        )

        c1, c2 = st.columns(2)
        with c1:
            if st.button("Add to selected sessions", use_container_width=True, key="ok_ros_add"):
                if not picked_sessions or not picked_students:
                    st.error("Pick at least one session and one student.")
                else:
                    success = True
                    for s in picked_sessions:
                        ok, err = client.update_session_roster(s["id"], picked_students, "add")
                        if not ok:
                            success = False
                            st.error(f"Failed for {session_label(s)}: {err}")
                    if success:
                        st.success(f"{len(picked_students)} student(s) added to {len(picked_sessions)} session(s).")
                        st.rerun()
        with c2:
            if st.button("Remove from selected sessions", use_container_width=True, key="danger_ros_remove"):
                if not picked_sessions or not picked_students:
                    st.error("Pick at least one session and one student.")
                else:
                    success = True
                    for s in picked_sessions:
                        ok, err = client.update_session_roster(s["id"], picked_students, "remove")
                        if not ok:
                            success = False
                            st.error(f"Failed for {session_label(s)}: {err}")
                    if success:
                        st.success(f"{len(picked_students)} student(s) removed from {len(picked_sessions)} session(s).")
                        st.rerun()

    rows = []
    for s in sorted(sessions, key=lambda x: str(x.get("scheduled_start") or "")):
        res = client.list_session_roster(s["id"])
        if isinstance(res, list):
            ids = [r.get("student_id") for r in res if r.get("student_id")]
            names = [labels.get(sid, sid[:8]) for sid in ids]
            shown = ", ".join(names[:3]) + (f" +{len(names) - 3} more" if len(names) > 3 else "")
            rows.append({
                "Session": session_label(s),
                "Allowed to check in": len(ids) if ids else f"All ({len(student_ids)})",
                "Names": shown or "Everyone enrolled in the course",
            })
        else:
            rows.append({
                "Session": session_label(s),
                "Allowed to check in": "Error",
                "Names": "Could not fetch roster from backend.",
            })
            
    panel_title("3 · Who can check in now")
    render_html_table(pd.DataFrame(rows), height=260)