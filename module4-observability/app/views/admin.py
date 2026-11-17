from __future__ import annotations

import secrets
from datetime import datetime

import pandas as pd
import streamlit as st

from access import require_admin
from client import backend_supports
from ui import (
    course_label,
    empty_state,
    live_badge,
    page_header,
    panel,
    plain_name,
    render_html_table,
    stat_row,
)

ROLES = ["student", "instructor", "ta", "admin"]
ROLE_LABELS = {"student": "Student", "instructor": "Instructor", "ta": "TA", "admin": "Admin"}
RESULT_LABELS = {
    "enrolled": "Enrolled",
    "already_enrolled": "Already enrolled",
    "not_found": "Not found",
}

def _btn(kind: str, label: str, **kwargs) -> bool:
    st.markdown(f'<div class="btn-mark btn-{kind}"></div>', unsafe_allow_html=True)
    return st.button(label, **kwargs)

def _popup(message: str, title: str = "Success") -> None:
    if hasattr(st, "dialog"):
        @st.dialog(title)
        def _dialog() -> None:
            st.success(message)
            _btn("ok", "OK", use_container_width=True, key="popup_ok")

        _dialog()
    else:
        st.toast(message, icon="✅")

def _reset_dialog(client, user_id: str, email: str) -> None:
    enabled = client.email_enabled()

    def _body() -> None:
        st.markdown(f"Reset the password for **{email}**.")
        if enabled is False:
            st.caption("Email isn't set up on this server, so a code can't be sent. "
                       "Give the user a temporary password instead.")
        else:
            st.caption("Recommended: email the user a one-time code. They choose their own "
                       "new password, so nobody else ever knows it.")
            if _btn("ok", "Email a reset code", use_container_width=True,
                    key=f"reset_mail_{user_id}"):
                ok, message = client.send_reset_email(user_id)
                if ok:
                    st.success(message + " They enter it under \"Forgot password?\" "
                               "on the login page.")
                else:
                    st.error(message)
            st.caption("Can't get the email? Use a temporary password and hand it over in person.")
        if _btn("ghost", "Show a temporary password", use_container_width=True,
                key=f"ghost_reset_temp_{user_id}"):
            ok, result = client.reset_password(user_id)
            if ok:
                st.success(f"The password for {email} has been reset.")
                st.code(result, language=None)
                st.caption("Shown only once. Give it to the user in person; they can sign in "
                           "with it straight away.")
            else:
                st.error(f"Failed: {result}")

    if hasattr(st, "dialog"):
        st.dialog("Reset password")(_body)()
    else:
        _body()

def _when(value) -> str:
    raw = str(value or "")[:16].replace("T", " ")
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M").strftime("%d %b, %H:%M UTC")
    except ValueError:
        return raw or "—"

def _reset_requests_panel(client) -> None:
    if backend_supports("GET", "/admin/password-reset-requests") is False:
        return
    requests_ = client.password_reset_requests()
    if not requests_:
        return
    with panel("Password reset requests",
               f"{len(requests_)} user(s) clicked \"Forgot password?\" and haven't reset it yet. "
               "Each was emailed a code; use Reset only if they can't get the email."):
        for row in requests_:
            left, right = st.columns([4, 1])
            with left:
                st.markdown(
                    f"**{row.get('email')}** · "
                    f"{ROLE_LABELS.get(str(row.get('role')), str(row.get('role')).title())} · "
                    f"requested {_when(row.get('requested_at'))}"
                    + ("" if row.get("is_active") else " · *inactive account*")
                )
            with right:
                if _btn("dark", "Reset", use_container_width=True,
                        key=f"req_reset_{row.get('user_id')}"):
                    _reset_dialog(client, row.get("user_id"), row.get("email"))

def _pending(user: dict) -> bool:
    return bool(user.get("scheduled_deletion_at"))

def _erase_date(user: dict) -> str:
    raw = str(user.get("scheduled_deletion_at") or "")[:10]
    try:
        return datetime.strptime(raw, "%Y-%m-%d").strftime("%d %b %Y")
    except ValueError:
        return raw or "a later date"

def _account_state(user: dict) -> str:
    if _pending(user):
        return "Pending deletion"
    return "Active" if user.get("is_active") else "Inactive"

def _confirm_delete(client, target: dict) -> None:
    email = target.get("email")

    def _do_delete() -> None:
        ok, err = client.schedule_user_deletion(target["id"])
        if ok:
            _flash(f"{email} deleted. The account is disabled now and its data is erased "
                   "after the 30-day retention period.")
            st.rerun()
        st.error(f"Failed: {err}")

    if hasattr(st, "dialog"):
        @st.dialog("Delete account?")
        def _dialog() -> None:
            st.markdown(f"**{email}** will be signed out, disabled and removed from the "
                        "user list. Its data is erased after the 30-day retention period; "
                        "until then an admin can restore it with Activate.")
            left, right = st.columns(2)
            with left:
                if _btn("ghost", "Cancel", use_container_width=True, key="ghost_del_cancel"):
                    st.rerun()
            with right:
                if _btn("danger", "Delete", use_container_width=True, key="del_confirm_btn"):
                    _do_delete()

        _dialog()
    else:
        st.session_state["_del_pending"] = target["id"]

def _structure_label(course: dict) -> str:
    parts = ["Lectures"]
    if course.get("has_tutorials"):
        parts.append("tutorials")
    if course.get("has_labs"):
        parts.append("labs")
    return " + ".join(parts) if len(parts) > 1 else "Lectures only"


def _groups_tab(client, courses: list) -> None:
    grouped = [c for c in courses if c.get("has_tutorials") or c.get("has_labs")]
    if not grouped:
        empty_state("No courses with tutorials or labs",
                    "Tick Tutorials or Labs when creating a course in ① Courses. "
                    "Lecture-only courses need no groups: every enrolled student attends.")
        return
    by_id = {c["id"]: c for c in grouped}
    if st.session_state.get("grp_course") not in by_id:
        st.session_state.pop("grp_course", None)
    course_id = st.selectbox("Course", list(by_id), format_func=lambda cid: course_label(by_id[cid]),
                             key="grp_course")
    data = client.course_groups(course_id)
    if not data:
        st.error(client.last_error or "Could not load groups.")
        return
    enrolled = int(data.get("enrolled") or 0)
    st.caption(f"{enrolled} student(s) enrolled. Each student is placed in exactly one "
               "tutorial group and one lab group, chosen at random and split evenly.")
    if not enrolled:
        st.info("Enrol students in ③ Enrol students first.")
        return
    kinds = [k for k, flag in (("tutorial", data.get("has_tutorials")), ("lab", data.get("has_labs"))) if flag]
    cols = st.columns(len(kinds))
    for col, kind in zip(cols, kinds):
        with col:
            _allocation_panel(client, course_id, kind, data, enrolled)
    _move_panel(client, course_id, data, kinds)


def _allocation_panel(client, course_id: str, kind: str, data: dict, enrolled: int) -> None:
    title = "Tutorials" if kind == "tutorial" else "Labs"
    groups = data.get(f"{kind}_groups") or []
    waiting = data.get(f"{kind}_unallocated") or []
    with panel(title, f"{len(groups)} group(s) · {len(waiting)} student(s) not yet placed"):
        how = st.radio("Split by", ["Number of groups", "Maximum students per group"],
                       key=f"grp_how_{kind}_{course_id}", horizontal=True)
        if how == "Number of groups":
            count = st.number_input(f"How many {kind} groups?", min_value=1, max_value=200,
                                    value=max(1, len(groups) or 8 if kind == "tutorial" else len(groups) or 4),
                                    step=1, key=f"grp_n_{kind}_{course_id}")
            size_hint = f"≈ {enrolled / count:.0f} students per group"
            args = {"group_count": int(count)}
        else:
            cap = st.number_input("Maximum students per group", min_value=1, max_value=1000,
                                  value=30 if kind == "tutorial" else 60, step=1,
                                  key=f"grp_cap_{kind}_{course_id}")
            needed = max(1, -(-enrolled // int(cap)))
            size_hint = f"→ {needed} group(s) of about {enrolled / needed:.0f}"
            args = {"max_per_group": int(cap)}
        st.caption(size_hint)
        mode = "reshuffle"
        if groups and waiting:
            only_new = st.checkbox(f"Only place the {len(waiting)} student(s) not in a group yet",
                                   value=True, key=f"grp_new_{kind}_{course_id}")
            mode = "add_new" if only_new else "reshuffle"
        if groups and mode == "reshuffle":
            st.caption("Re-allocating reshuffles everyone into new groups.")
        label = f"Allocate {title.lower()}" if not groups else f"Re-allocate {title.lower()}"
        if mode == "add_new":
            label = f"Place {len(waiting)} new student(s)"
        if _btn("ok", label, use_container_width=True, key=f"grp_go_{kind}_{course_id}"):
            ok, result = client.allocate_groups(course_id, kind, mode=mode, **args)
            if ok:
                made = result.get(f"{kind}_groups") or []
                sizes = sorted({g.get("size", 0) for g in made})
                span = f"{sizes[0]}" if len(sizes) == 1 else f"{sizes[0]}–{sizes[-1]}"
                _flash(f"{len(made)} {kind} group(s) ready, {span} students each. "
                       f"Instructors can now create {kind} sessions for each group.")
                st.rerun()
            else:
                st.error(result)
        if groups:
            table = pd.DataFrame([{"Group": g["name"], "Students": g["size"],
                                   "Limit": g.get("capacity") or "—"} for g in groups])
            render_html_table(table, height=min(320, 48 + 44 * len(groups)))
            for g in groups:
                with st.expander(f"{g['name']} · {g['size']} students"):
                    names = [f"{plain_name(m.get('full_name'))} ({m.get('email')})" for m in g["members"]]
                    st.write(", ".join(names) or "Empty")


def _move_panel(client, course_id: str, data: dict, kinds: list) -> None:
    groups = [g for kind in kinds for g in (data.get(f"{kind}_groups") or [])]
    if not groups:
        return
    people = {}
    for g in groups:
        for m in g["members"]:
            people.setdefault(m["id"], m)
    for kind in kinds:
        for m in data.get(f"{kind}_unallocated") or []:
            people.setdefault(m["id"], m)
    with panel("Move a student", "For one-off changes, e.g. a timetable clash."):
        a, b, c = st.columns([2, 1.2, 1])
        sid = a.selectbox("Student", sorted(people, key=lambda x: people[x].get("email") or ""),
                          format_func=lambda x: f"{plain_name(people[x].get('full_name'))} · {people[x].get('email')}",
                          key=f"mv_student_{course_id}")
        gid = b.selectbox("Move to", [g["id"] for g in groups],
                          format_func=lambda x: next(g["name"] for g in groups if g["id"] == x),
                          key=f"mv_group_{course_id}")
        c.markdown('<div style="height: 1.75rem;"></div>', unsafe_allow_html=True)
        if _btn("ok", "Move", use_container_width=True, key=f"mv_go_{course_id}"):
            ok, result = client.move_group_member(course_id, sid, gid)
            if ok:
                target = next(g["name"] for g in groups if g["id"] == gid)
                _flash(f"{people[sid].get('email')} is now in {target}.")
                st.rerun()
            else:
                st.error(result)


def _flash(message: str) -> None:
    st.session_state["_admin_flash"] = message

def _show_flash() -> None:
    message = st.session_state.pop("_admin_flash", None)
    if message:
        _popup(message)

def _summary(items: list) -> str:
    cells = "".join(
        f'<div class="adm-card {tone}"><div class="adm-v">{value}</div><div class="adm-l">{label}</div></div>'
        for label, value, tone in items
    )
    return f'<div class="adm-stats">{cells}</div>'

def _seed_password(key: str) -> None:
    if not st.session_state.get(key):
        st.session_state[key] = secrets.token_urlsafe(9)


def _coord(text: str, low: float, high: float):
    text = (text or "").strip()
    if not text:
        return None, None
    try:
        value = float(text)
    except ValueError:
        return None, "must be a number"
    if not low <= value <= high:
        return None, f"must be between {low:g} and {high:g}"
    return value, None


def _role(user: dict) -> str:
    return str(user.get("role") or "").split(".")[-1].strip().lower()

def _yes_no(value) -> str:
    return "Yes" if value else "No"

def _get_semesters() -> list[str]:
    today = datetime.now()
    start_year = today.year if today.month >= 8 else today.year - 1
    semesters = []
    for year in (start_year - 1, start_year, start_year + 1):
        ay = f"AY{str(year)[-2:]}/{str(year + 1)[-2:]}"
        semesters.append(f"{ay}-S1")
        semesters.append(f"{ay}-S2")
    return semesters

def render_admin_assignments(client) -> None:
    if not require_admin(client):
        return
    page_header("Administration", "Administration",
                "Set up a course in order: create it, assign its instructors, enrol its students. "
                "Instructors then schedule sessions and assign TAs from the Sessions page.")
    live_badge()
    _show_flash()

    all_users = client.gather_all(client.list_users, page_size=100, cap=1000)
    users = [u for u in all_users if not _pending(u)]
    courses = client.gather_all(client.list_courses, page_size=100, cap=500)
    sessions = client.gather_all(client.list_sessions, page_size=100, cap=500)

    active_users = sum(1 for u in users if u.get("is_active"))
    students = sum(1 for u in users if _role(u) == "student")
    st.markdown(
        _summary([
            ("Users", len(users), ""),
            ("Active", active_users, "good"),
            ("Students", students, ""),
            ("Courses", len(courses), ""),
            ("Sessions", len(sessions), ""),
        ]),
        unsafe_allow_html=True,
    )
    _setup_checklist(courses)

    tab_courses, tab_instructors, tab_enroll, tab_groups, tab_users = st.tabs([
        "① Courses",
        "② Instructors",
        "③ Enrol students",
        "④ Tutorials & labs",
        "Users",
    ])
    with tab_courses:
        _courses_tab(client, courses, sessions, users)
    with tab_instructors:
        _instructors_tab(client, users, courses)
    with tab_enroll:
        _enroll_tab(client, courses)
    with tab_groups:
        _groups_tab(client, courses)
    with tab_users:
        _users_tab(client, all_users)


def _setup_checklist(courses: list) -> None:
    if not courses:
        st.info("Start with ① Courses: create the first course.")
        return
    no_instructor = [c for c in courses if not c.get("instructor_ids")]
    if no_instructor and backend_supports("POST", "/admin/assignments/instructor") is not False:
        codes = ", ".join(str(c.get("code")) for c in no_instructor[:6])
        more = f" and {len(no_instructor) - 6} more" if len(no_instructor) > 6 else ""
        st.warning(f"{len(no_instructor)} course(s) have no instructor yet: {codes}{more}. "
                   "Assign them in ② Instructors.")


def _instructors_tab(client, users: list, courses: list) -> None:
    if not courses:
        empty_state("No courses", "Create a course in ① Courses first.")
        return
    if backend_supports("POST", "/admin/assignments/instructor") is False:
        st.info("This backend has no instructor-assignment endpoint "
                "(POST /admin/assignments/instructor), so assignments are switched off.")
        return

    instructors = {u["id"]: u for u in users if _role(u) == "instructor" and u.get("id")}
    by_id = {c["id"]: c for c in courses if c.get("id")}
    left, right = st.columns([1, 1.3])
    with left:
        with panel("Assign instructors", "A course can have several instructors. "
                                         "Only they can schedule its sessions."):
            if not instructors:
                st.info("No instructor accounts yet. Create them in the Users tab "
                        "(Add users in bulk → role Instructor).")
                return
            ids = list(by_id)
            if st.session_state.get("asg_course") not in ids:
                st.session_state.pop("asg_course", None)
            course_id = st.selectbox("Course", ids, format_func=lambda cid: course_label(by_id[cid]),
                                     key="asg_course")
            current = [i for i in (by_id[course_id].get("instructor_ids") or []) if i]
            known = {**instructors}
            label = lambda uid: (f"{plain_name(known[uid].get('full_name'))} · {known[uid].get('email')}"
                                 if uid in known else str(uid)[:8])
            picked = st.multiselect("Instructors", list(known) + [u for u in current if u not in known],
                                    default=current, format_func=label,
                                    key=f"asg_instructors_{course_id}")
            if _btn("ok", "Save instructors", use_container_width=True, key="asg_instr_btn"):
                errors = []
                for uid in set(picked) - set(current):
                    ok, err = client.assign_instructor(course_id, uid)
                    if not ok:
                        errors.append(f"{label(uid)}: {err}")
                for uid in set(current) - set(picked):
                    ok, err = client.unassign_instructor(course_id, uid)
                    if not ok:
                        errors.append(f"{label(uid)}: {err}")
                if errors:
                    st.error("Some changes failed: " + "; ".join(errors))
                else:
                    _flash(f"Instructors saved for {course_label(by_id[course_id])}. "
                           "Next: enrol its students in ③ Enrol students.")
                    st.session_state["be_course"] = course_id
                    st.rerun()
    with right:
        with panel("Who teaches what", "TAs are assigned per session on the Sessions page, "
                                      "by you or the course's instructor."):
            rows = []
            for c in sorted(courses, key=lambda x: str(x.get("code") or "")):
                names = [plain_name((instructors.get(i) or {}).get("full_name") or str(i)[:8])
                         for i in (c.get("instructor_ids") or [])]
                rows.append({"Course": course_label(c),
                             "Instructors": ", ".join(names) or "— not assigned —"})
            render_html_table(pd.DataFrame(rows), height=380)


def _users_tab(client, users: list) -> None:
    _reset_requests_panel(client)
    if not users:
        empty_state("No users", "No users found. Check the API connection.")
        return

    with panel("Accounts", "Search and filter, then pick an account to manage below."):
        f1, f2, f3 = st.columns([2, 1, 1])
        search = f1.text_input("Search", placeholder="Email or name", key="usr_search")
        role_filter = f2.selectbox(
            "Role", ["all"] + ROLES, format_func=lambda r: "All roles" if r == "all" else ROLE_LABELS[r],
            key="usr_role",
        )
        state_filter = f3.selectbox("Account", ["All", "Active", "Inactive", "Pending deletion"],
                                    key="usr_state")

        needle = search.strip().lower()
        filtered = []
        for u in users:
            if needle and needle not in str(u.get("email") or "").lower() \
                    and needle not in str(u.get("full_name") or "").lower():
                continue
            if role_filter != "all" and _role(u) != role_filter:
                continue
            if state_filter == "Pending deletion":
                if not _pending(u):
                    continue
            elif _pending(u):
                continue
            if state_filter == "Active" and not u.get("is_active"):
                continue
            if state_filter == "Inactive" and u.get("is_active"):
                continue
            filtered.append(u)

        hidden = sum(1 for u in users if _pending(u))
        note = (f" · {hidden} deleted account(s) hidden; choose \"Pending deletion\" to see them"
                if hidden and state_filter != "Pending deletion" else "")
        st.caption(f"Showing {len(filtered)} of {len(users)} users{note}")
        if filtered:
            table = pd.DataFrame([
                {
                    "Email": u.get("email"),
                    "Name": plain_name(u.get("full_name")),
                    "Role": ROLE_LABELS.get(_role(u), _role(u).title()),
                    "Account": _account_state(u),
                    "Camera consent": _yes_no(u.get("camera_consent")),
                    "Location consent": _yes_no(u.get("geolocation_consent")),
                    "Face enrolled": _yes_no(u.get("face_enrolled")),
                }
                for u in filtered
            ])
            render_html_table(table, height=320)
        else:
            empty_state("No matches", "Try a different search or filter.")

    if not filtered:
        return

    with panel("Manage account", "Changes apply to the selected account."):
        target = st.selectbox(
            "Account",
            filtered,
            format_func=lambda u: f"{u.get('email')} · {ROLE_LABELS.get(_role(u), _role(u))}",
            key="mgmt_user",
        )
        st.caption(f"{plain_name(target.get('full_name')) or '—'} · {_account_state(target)}")
        active = bool(target.get("is_active"))
        pending = _pending(target)
        if pending:
            st.info(f"This account is scheduled for erasure on {_erase_date(target)}. "
                    "Activate it to cancel the deletion.")
        c1, c2, c3, c4 = st.columns(4)
        problem = None
        with c1:
            if _btn("ok", "Activate", use_container_width=True, key="act_btn", disabled=active):
                ok, err = client.activate_user(target["id"])
                if ok:
                    _flash(f"{target.get('email')} activated.")
                    st.rerun()
                problem = err
        with c2:
            if _btn("warn", "Deactivate", use_container_width=True, key="deact_btn",
                    disabled=not active):
                ok, err = client.deactivate_user(target["id"])
                if ok:
                    _flash(f"{target.get('email')} deactivated.")
                    st.rerun()
                problem = err
        with c3:
            reset_clicked = _btn("dark", "Reset password", use_container_width=True,
                                 key="reset_pw_btn", disabled=pending)
        with c4:
            delete_clicked = _btn("danger", "Delete", use_container_width=True, key="del_btn",
                                  disabled=pending)
        if reset_clicked:
            _reset_dialog(client, target["id"], target.get("email"))
        if delete_clicked:
            _confirm_delete(client, target)
        if not hasattr(st, "dialog") and st.session_state.get("_del_pending") == target["id"]:
            st.warning(f"{target.get('email')} will be disabled and erased after the 30-day "
                       "retention period.")
            yes, no = st.columns(2)
            with yes:
                if _btn("danger", "Yes, delete", use_container_width=True, key="del_yes_btn"):
                    st.session_state.pop("_del_pending", None)
                    ok, err = client.schedule_user_deletion(target["id"])
                    if ok:
                        _flash(f"{target.get('email')} deleted.")
                        st.rerun()
                    problem = err
            with no:
                if _btn("ghost", "Cancel", use_container_width=True, key="ghost_del_no"):
                    st.session_state.pop("_del_pending", None)
                    st.rerun()
        if problem:
            st.error(f"Failed: {problem}")

    with st.expander("Add users in bulk"):
        st.caption("Creates accounts with the role you pick. Emails that already exist are skipped.")
        bulk_role = st.selectbox("Role for these accounts", ROLES,
                                 format_func=lambda r: ROLE_LABELS[r], key="bulk_users_role")
        emails_input = st.text_area(
            "Emails (one per line)",
            placeholder="student1@e.ntu.edu.sg\nstudent2@e.ntu.edu.sg",
            key="bulk_users_emails",
        )
        _seed_password("bulk_users_pw")
        default_password = st.text_input("Default password", key="bulk_users_pw")
        st.caption("Random per form. Share it with the new users privately; "
                   "the backend does not force a change at first login yet.")
        if _btn("ok", "Create users", key="btn_cu"):
            emails = list(dict.fromkeys(e.strip() for e in emails_input.splitlines() if e.strip()))
            if not emails:
                st.error("Provide at least one email.")
            elif len(default_password) < 8:
                st.error("Password must be at least 8 characters.")
            else:
                ok, result = client.bulk_create_users(
                    [{"email": e, "role": bulk_role} for e in emails], default_password
                )
                if ok:
                    created = result.get("created", 0)
                    _popup(
                        f"{created} new user(s) created, {len(emails) - created} already existed.",
                        "Users created",
                    )
                else:
                    st.error(result)

def _set_course_mode(mode) -> None:
    if mode is None:
        st.session_state.pop("crs_mode", None)
    else:
        st.session_state["crs_mode"] = mode


def _clear_create_form() -> None:
    for key in ("cc_code", "cc_name", "cc_sem", "cc_venue", "cc_lat", "cc_lon", "cc_radius",
                "cc_risk", "cc_face", "cc_tut", "cc_lab"):
        st.session_state.pop(key, None)


def _courses_tab(client, courses: list, sessions: list, users: list) -> None:
    mode = st.session_state.get("crs_mode")
    if mode in ("edit", "delete") and not courses:
        _set_course_mode(None)
        mode = None

    with panel("All courses", f"{len(courses)} in the system"):
        _, b1, b2, b3 = st.columns([3.4, 1.1, 1.1, 1.1])
        with b1:
            if _btn("ok", "\\+ Add course", use_container_width=True, key="crs_add",
                    disabled=mode is not None):
                _set_course_mode("create")
                st.rerun()
        with b2:
            if _btn("ghost", "Edit course", use_container_width=True, key="crs_edit",
                    disabled=mode is not None or not courses):
                _set_course_mode("edit")
                st.rerun()
        with b3:
            if _btn("danger", "Delete course", use_container_width=True, key="crs_delete",
                    disabled=mode is not None or not courses):
                _set_course_mode("delete")
                st.rerun()
        if not courses:
            empty_state("No courses", "Click + Add course to create the first one.")
        else:
            counts: dict = {}
            for s in sessions:
                counts[s.get("course_id")] = counts.get(s.get("course_id"), 0) + 1
            names = {u.get("id"): plain_name(u.get("full_name") or u.get("email"))
                     for u in users}
            table = pd.DataFrame([
                {
                    "Code": c.get("code"),
                    "Name": c.get("name"),
                    "Semester": c.get("semester"),
                    "Instructors": [names.get(i, str(i)[:8])
                                    for i in c.get("instructor_ids") or []] or "—",
                    "Structure": _structure_label(c),
                    "Sessions": counts.get(c.get("id"), 0),
                    "Active": _yes_no(c.get("is_active")),
                }
                for c in sorted(courses, key=lambda x: str(x.get("code") or ""))
            ])
            render_html_table(table, height=min(420, 88 + 70 * len(table)))

    if mode == "create":
        _create_course_panel(client)
    elif mode == "edit":
        _edit_course_panel(client, courses)
    elif mode == "delete":
        _delete_course_panel(client, courses, sessions)


def _delete_course_panel(client, courses: list, sessions: list) -> None:
    by_id = {c["id"]: c for c in sorted(courses, key=lambda x: str(x.get("code") or "")) if c.get("id")}
    with panel("Delete course", "Only possible before anyone has checked in to the course."):
        if st.session_state.get("dc_course") not in by_id:
            st.session_state.pop("dc_course", None)
        course_id = st.selectbox("Course to delete", list(by_id),
                                 format_func=lambda cid: course_label(by_id[cid]), key="dc_course")
        course = by_id[course_id]
        count = sum(1 for s in sessions if s.get("course_id") == course_id)
        st.caption(f"Deleting {course.get('code')} also removes its {count} session(s), its enrolments, "
                   "tutorial and lab groups, and instructor and TA assignments. "
                   "If any student has checked in, the course is kept and nothing is deleted.")
        sure = st.checkbox(f"I understand {course.get('code')} will be permanently deleted",
                           key=f"dc_sure_{course_id}")
        a1, a2 = st.columns(2)
        with a1:
            cancel = _btn("ghost", "Cancel", use_container_width=True, key="dc_cancel")
        with a2:
            delete = _btn("danger", "Delete course", use_container_width=True, key="dc_confirm",
                          disabled=not sure)
        if cancel:
            for key in [k for k in st.session_state.keys() if str(k).startswith("dc_")]:
                st.session_state.pop(key, None)
            _set_course_mode(None)
            st.rerun()
        if not delete:
            return
        ok, result = client.delete_course(course_id)
        if not ok:
            st.error(result)
            return
        for key in [k for k in st.session_state.keys() if str(k).startswith("dc_")]:
            st.session_state.pop(key, None)
        _set_course_mode(None)
        st.cache_data.clear()
        _flash(f"Course {course.get('code')} deleted.")
        st.rerun()


def _create_course_panel(client) -> None:
    with panel("Add course", "Nothing is created until you click Create course."):
        st.markdown("**Details**")
        c1, c2, c3 = st.columns([1, 2, 1])
        code = c1.text_input("Course code", placeholder="e.g. SC4001", key="cc_code")
        name = c2.text_input("Course name", placeholder="e.g. Neural Networks", key="cc_name")
        semesters = _get_semesters()
        default_index = 2 if datetime.now().month >= 8 else 3
        if len(semesters) <= default_index:
            default_index = 0
        semester = c3.selectbox("Semester", semesters, index=default_index, key="cc_sem")

        st.markdown("**Default location**")
        v0, v1, v2, v3 = st.columns([1.4, 1, 1, 1])
        venue = v0.text_input("Venue", placeholder="e.g. LT1", key="cc_venue")
        lat_txt = v1.text_input("Latitude", placeholder="1.3449", key="cc_lat")
        lon_txt = v2.text_input("Longitude", placeholder="103.6812", key="cc_lon")
        radius = v3.number_input("Radius (m)", min_value=10.0, max_value=2000.0,
                                 value=100.0, step=10.0, key="cc_radius")
        st.caption("Used by every session in this course unless the session sets its own venue, "
                   "location or radius. Without coordinates, check-ins aren't location-checked.")

        st.markdown("**Check-in rules**")
        q1, q2 = st.columns(2)
        risk = q1.number_input("Risk threshold", min_value=0.05, max_value=0.95, value=0.5,
                               step=0.05, key="cc_risk",
                               help="Check-ins scoring at or above this are flagged for review. "
                                    "A session can set its own threshold instead.")
        q2.markdown('<div style="height: 1.9rem"></div>', unsafe_allow_html=True)
        face = q2.checkbox("Require face recognition", key="cc_face",
                           help="Every check-in in this course must match the student's enrolled face.")

        st.markdown("**Course structure**")
        st.caption("Lectures are always included and every enrolled student attends them. "
                   "Tutorials and labs split students into groups.")
        k0, k1, k2 = st.columns(3)
        k0.checkbox("Lectures (always included)", value=True, disabled=True, key="cc_lec")
        has_tut = k1.checkbox("Tutorials", key="cc_tut")
        has_lab = k2.checkbox("Labs", key="cc_lab")

        a1, a2 = st.columns(2)
        with a1:
            cancel = _btn("danger", "Cancel", use_container_width=True, key="cc_cancel")
        with a2:
            create = _btn("ok", "Create course", use_container_width=True, key="btn_cc")
        if cancel:
            _clear_create_form()
            _set_course_mode(None)
            st.rerun()
        if not create:
            return

        lat, lat_err = _coord(lat_txt, -90, 90)
        lon, lon_err = _coord(lon_txt, -180, 180)
        if not code.strip() or not name.strip():
            st.error("Course code and name are required.")
            return
        if lat_err or lon_err:
            st.error("Latitude " + lat_err if lat_err else "Longitude " + lon_err)
            return
        if (lat is None) != (lon is None):
            st.error("Give both latitude and longitude, or neither.")
            return
        payload = {"code": code.strip(), "name": name.strip(), "semester": semester,
                   "geofence_radius_meters": float(radius),
                   "risk_threshold": round(float(risk), 2),
                   "require_face_recognition": bool(face),
                   "has_tutorials": bool(has_tut), "has_labs": bool(has_lab)}
        if venue.strip():
            payload["venue_name"] = venue.strip()
        if lat is not None:
            payload["venue_latitude"], payload["venue_longitude"] = lat, lon
        ok, result = client.create_course(payload)
        if not ok:
            st.error(result)
            return
        if isinstance(result, dict) and result.get("id"):
            st.session_state["asg_course"] = result["id"]
        _clear_create_form()
        _set_course_mode(None)
        st.cache_data.clear()
        _flash(f"Course {code.strip().upper()} created. "
               "Next: assign its instructors in ② Instructors.")
        st.rerun()


def _close_course_editor(course_id: str) -> None:
    suffix = f"_{course_id}"
    for key in [k for k in st.session_state.keys()
                if str(k).startswith("ec_") and str(k).endswith(suffix)]:
        st.session_state.pop(key, None)


def _edit_course_panel(client, courses: list) -> None:
    by_id = {c["id"]: c for c in sorted(courses, key=lambda x: str(x.get("code") or "")) if c.get("id")}
    with panel("Edit course", "Nothing is saved until you click Save changes. "
                              "Enrolled students stay enrolled."):
        if st.session_state.get("ec_course") not in by_id:
            st.session_state.pop("ec_course", None)
        course_id = st.selectbox("Course to edit", list(by_id), format_func=lambda cid: course_label(by_id[cid]),
                                 key="ec_course")
        course = by_id[course_id]
        st.caption(f"Code {course.get('code')} · Semester {course.get('semester')}. "
                   "The code and semester identify the course and can't be changed.")

        def _text(value) -> str:
            return "" if value is None else str(value)

        k = course_id
        st.markdown("**Details**")
        name = st.text_input("Course name", value=_text(course.get("name")), key=f"ec_name_{k}")

        st.markdown("**Default location**")
        v0, v1, v2, v3 = st.columns([1.4, 1, 1, 1])
        venue = v0.text_input("Venue", value=_text(course.get("venue_name")), key=f"ec_venue_{k}")
        lat_txt = v1.text_input("Latitude", value=_text(course.get("venue_latitude")), key=f"ec_lat_{k}")
        lon_txt = v2.text_input("Longitude", value=_text(course.get("venue_longitude")), key=f"ec_lon_{k}")
        radius = v3.number_input("Radius (m)", min_value=10.0, max_value=2000.0,
                                 value=float(min(2000.0, max(10.0, course.get("geofence_radius_meters") or 100.0))),
                                 step=10.0, key=f"ec_radius_{k}")
        st.caption("Used by every session in this course unless the session sets its own venue, "
                   "location or radius.")

        st.markdown("**Check-in rules**")
        r1, r2 = st.columns(2)
        risk = r1.number_input("Risk threshold", min_value=0.05, max_value=0.95,
                               value=float(min(0.95, max(0.05, course.get("risk_threshold") or 0.5))),
                               step=0.05, key=f"ec_risk_{k}",
                               help="Check-ins scoring at or above this are flagged for review. "
                                    "A session can set its own threshold instead.")
        r2.markdown('<div style="height: 1.9rem"></div>', unsafe_allow_html=True)
        face = r2.checkbox("Require face recognition", value=bool(course.get("require_face_recognition")),
                           key=f"ec_face_{k}")

        st.markdown("**Course structure**")
        st.caption("Lectures are always included and every enrolled student attends them. "
                   "Tutorials and labs split students into groups.")
        s0, s1, s2 = st.columns(3)
        s0.checkbox("Lectures (always included)", value=True, disabled=True, key=f"ec_lec_{k}")
        has_tut = s1.checkbox("Tutorials", value=bool(course.get("has_tutorials")), key=f"ec_tut_{k}")
        has_lab = s2.checkbox("Labs", value=bool(course.get("has_labs")), key=f"ec_lab_{k}")
        if (course.get("has_tutorials") and not has_tut) or (course.get("has_labs") and not has_lab):
            st.caption("Existing groups and their past sessions are kept. "
                       "Students stay enrolled in the course.")

        a1, a2 = st.columns(2)
        with a1:
            cancel = _btn("danger", "Cancel", use_container_width=True, key="ec_cancel")
        with a2:
            save = _btn("ok", "Save changes", use_container_width=True, key=f"ec_save_{k}")
        if cancel:
            for cid in by_id:
                _close_course_editor(cid)
            _set_course_mode(None)
            st.rerun()
        if not save:
            return

        lat, lat_err = _coord(lat_txt, -90, 90)
        lon, lon_err = _coord(lon_txt, -180, 180)
        if not name.strip():
            st.error("Course name is required.")
            return
        if lat_err or lon_err:
            st.error("Latitude " + lat_err if lat_err else "Longitude " + lon_err)
            return
        if (lat is None) != (lon is None):
            st.error("Give both latitude and longitude, or neither.")
            return
        payload = {"name": name.strip(), "geofence_radius_meters": float(radius),
                   "risk_threshold": round(float(risk), 2), "require_face_recognition": bool(face)}
        if venue.strip():
            payload["venue_name"] = venue.strip()
        if lat is not None:
            payload["venue_latitude"], payload["venue_longitude"] = lat, lon
        ok, result = client.update_course(course_id, payload)
        if not ok:
            st.error(result)
            return
        if bool(has_tut) != bool(course.get("has_tutorials")) or bool(has_lab) != bool(course.get("has_labs")):
            ok, result = client.set_course_structure(course_id, bool(has_tut), bool(has_lab))
            if not ok:
                st.error(f"Details saved, but the structure couldn't be changed: {result}")
                return
        st.cache_data.clear()
        for cid in by_id:
            _close_course_editor(cid)
        _set_course_mode(None)
        _flash(f"Course {course.get('code')} updated.")
        st.rerun()

def _enroll_tab(client, courses: list) -> None:
    if not courses:
        if client.last_status and client.last_status >= 400:
            st.error(f"Could not load courses (HTTP {client.last_status}): {client.last_error}")
        empty_state("No courses", "Create a course first.")
        return

    options = {c["id"]: course_label(c) for c in courses}
    results = None
    new_accounts: set = set()

    left, right = st.columns(2)
    with left:
        with panel("Enroll students", "Only student accounts can be enrolled."):
            if st.session_state.get("be_course") not in options:
                st.session_state.pop("be_course", None)
            course_id = st.selectbox(
                "Course", list(options.keys()), format_func=lambda x: options[x], key="be_course"
            )
            semester = next((c.get("semester") for c in courses if c["id"] == course_id), "")
            if semester:
                st.caption(f"Semester {semester}")
            emails_input = st.text_area(
                "Emails (one per line)",
                placeholder="student1@e.ntu.edu.sg\nstudent2@e.ntu.edu.sg",
                height=160,
                key="be_emails",
            )
            create_missing = st.checkbox(
                "Create accounts for emails that don't have one yet", key="be_create"
            )
            new_password = ""
            if create_missing:
                _seed_password("be_pw")
                new_password = st.text_input("Password for new accounts", key="be_pw")
                st.caption("New accounts are created as students. Share this password with them.")

            if _btn("ok", "Enroll students", use_container_width=True, key="btn_be"):
                emails = list(dict.fromkeys(e.strip() for e in emails_input.splitlines() if e.strip()))
                invalid = [e for e in emails if "@" not in e or "." not in e.split("@")[-1]]
                if not emails:
                    st.error("Provide at least one email.")
                elif invalid:
                    st.error("These don't look like valid emails: " + ", ".join(invalid))
                elif create_missing and len(new_password) < 8:
                    st.error("Password must be at least 8 characters.")
                else:
                    proceed = True
                    if create_missing:
                        ok, created = client.bulk_create_users(
                            [{"email": e, "role": "student"} for e in emails], new_password
                        )
                        if ok:
                            new_accounts = {str(u.get("email") or "").lower() for u in created.get("users", [])}
                        else:
                            proceed = False
                            st.error(created)
                    if proceed:
                        ok, result = client.bulk_enroll(course_id, emails)
                        if ok:
                            results = result.get("results", [])
                            enrolled = sum(1 for r in results if r.get("status") == "enrolled")
                            message = f"{enrolled} of {len(results)} student(s) enrolled."
                            if new_accounts:
                                message += f" {len(new_accounts)} new account(s) created."
                            course = next((c for c in courses if c["id"] == course_id), {})
                            if course.get("has_tutorials") or course.get("has_labs"):
                                message += (" Next: split them into tutorial and lab groups "
                                            "in ④ Tutorials & labs.")
                                st.session_state["grp_course"] = course_id
                            _popup(message, "Enrollment complete")
                        else:
                            st.error(f"Backend error: {result}. Ensure the backend supports bulk enrollment.")

    with right:
        with panel("Results", "Shown after you enroll."):
            if not results:
                st.caption("No enrollment run yet.")
            else:
                counts = {key: sum(1 for r in results if r.get("status") == key) for key in RESULT_LABELS}
                st.markdown(
                    stat_row([
                        ("Enrolled", counts["enrolled"], "good"),
                        ("Already enrolled", counts["already_enrolled"], ""),
                        ("Not found", counts["not_found"], "bad" if counts["not_found"] else "good"),
                    ]),
                    unsafe_allow_html=True,
                )

                def _label(row: dict) -> str:
                    label = RESULT_LABELS.get(row.get("status"), row.get("status"))
                    if row.get("status") == "enrolled" and str(row.get("email") or "").lower() in new_accounts:
                        return f"{label} (new account)"
                    return label

                render_html_table(
                    pd.DataFrame([{"Email": r.get("email"), "Result": _label(r)} for r in results]),
                    height=300,
                )