from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Optional

from client import as_items, parse_dt

_ENROLLED_TTL = 30.0


def _ids(record: dict, plural: str, singular: str) -> set:
    found = set()
    many = record.get(plural)
    if isinstance(many, (list, tuple, set)):
        found.update(str(v) for v in many if v)
    one = record.get(singular)
    if one:
        found.add(str(one))
    return found


def owns_course(user: dict, record: dict, course_map: Optional[dict] = None) -> bool:
    if not user or not record:
        return False
    uid = user.get("id")
    if not uid:
        return False
    uid = str(uid)
    if uid in _ids(record, "instructor_ids", "instructor_id"):
        return True
    cid = record.get("course_id")
    if cid and course_map:
        course = course_map.get(cid)
        if course and course is not record:
            return uid in _ids(course, "instructor_ids", "instructor_id")
    return False


def is_assigned_session(user: dict, session: dict) -> bool:
    if not user or not session:
        return False
    uid = user.get("id")
    if not uid:
        return False
    uid = str(uid)
    return uid in _ids(session, "ta_ids", "ta_id") or str(session.get("instructor_id") or "") == uid


def enrolled_for(session: dict) -> int:
    for key in ("enrolled_count", "total_enrolled", "enrollment_count"):
        raw = session.get(key)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0


def attendees_for(session: dict, checkins: Iterable[dict]) -> int:
    sid = session.get("id")
    students = set()
    count = 0
    for row in checkins:
        if row.get("session_id") != sid:
            continue
        count += 1
        status = str(row.get("status") or "").lower()
        if status == "rejected":
            continue
        uid = row.get("student_id")
        if uid:
            students.add(uid)
    return len(students) if students else count


def _course_enrolment(client, course_ids) -> Dict[str, int]:
    try:
        import streamlit as st
        cache = st.session_state.setdefault("_enrolled_cache", {})
    except Exception:
        cache = {}
    now = time.monotonic()
    out: Dict[str, int] = {}
    for cid in course_ids:
        hit = cache.get(cid)
        if hit and now - hit[0] < _ENROLLED_TTL:
            out[cid] = hit[1]
            continue
        count = client.course_enrolled_count(cid)
        if count is not None:
            cache[cid] = (now, count)
            out[cid] = count
    return out


def attach_course_codes(client, sessions: list, courses: Optional[list] = None) -> list:
    if courses is None:
        courses = as_items(client.list_courses(limit=100))
    codes = {c.get("id"): c.get("code") for c in courses if c.get("id")}
    out = []
    for session in sessions:
        if session.get("course_code") or session.get("course_id") not in codes:
            out.append(session)
        else:
            out.append({**session, "course_code": codes[session["course_id"]]})
    return out


def load_bundle(client) -> Dict[str, Any]:
    try:
        import streamlit as st
        user = st.session_state.get("user") or {}
    except Exception:
        user = {}
    role = str(user.get("role") or "").split(".")[-1].strip().lower()

    sessions: List[dict] = []
    if role in {"instructor", "ta"}:
        sessions = client.my_sessions()
    elif role == "admin":
        sessions = client.gather_all(lambda **p: client.list_sessions(**p), cap=500)

    checkins: List[dict] = []
    courses: List[dict] = []
    if role in {"instructor", "ta", "admin"}:
        checkins = client.gather_all(lambda **p: client.list_checkins(**p), cap=500)
        courses = client.gather_all(lambda **p: client.list_courses(**p), cap=500)

    if role in {"instructor", "ta"}:
        allowed = {s.get("id") for s in sessions if s.get("id")}
        checkins = [row for row in checkins if row.get("session_id") in allowed]
        course_ids = {s.get("course_id") for s in sessions if s.get("course_id")}
        if role == "instructor":
            course_ids |= {c.get("id") for c in courses if owns_course(user, c)}
        courses = [c for c in courses if c.get("id") in course_ids]

    enrolled = _course_enrolment(client, {s.get("course_id") for s in sessions if s.get("course_id")})
    sessions = [
        {**s, "enrolled_count": s["group_size"] if s.get("group_size") is not None
         else enrolled.get(s.get("course_id"), 0)}
        for s in sessions
    ]
    sessions = attach_course_codes(client, sessions, courses)

    def sort_key(s):
        dt = parse_dt(s.get("scheduled_start"))
        return dt.timestamp() if dt else 0

    sessions.sort(key=sort_key, reverse=True)

    flagged = [row for row in checkins if str(row.get("status") or "").lower() in {"flagged", "appealed"}]
    return {
        "sessions": sessions,
        "checkins": checkins,
        "courses": courses,
        "flagged": flagged,
        "live_window": list(checkins),
    }