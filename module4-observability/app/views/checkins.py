from __future__ import annotations

from datetime import timedelta

import pandas as pd
import streamlit as st

from access import require_staff
from auth_session import persist_auth
from client import now_sgt, parse_dt
from data import attach_course_codes, load_bundle
from ui import (
    VERIFY_LABELS,
    can_review_checkins,
    can_view_flagged_checkins,
    checkins_to_frame,
    dataframe,
    display_name,
    empty_state,
    export_buttons,
    fmt_dt,
    fmt_score,
    gradebook_csv,
    live_badge,
    page_header,
    panel_title,
    render_html_table,
    risk_meter,
    session_label,
    stat_row,
    user_directory,
    verification_label,
    enable_click_outside,
    scroll_to_top,
)

def render_checkins(client) -> None:
    if not require_staff(client):
        return
    enable_click_outside()
    scroll_to_top()

    page_header("Attendance", "Check-ins", "Every check-in with its verification result. Flagged ones can be reviewed below.")
    live_badge()

    bundle = load_bundle(client)
    sessions = bundle["sessions"]
    session_map = {s["id"]: s for s in sessions if s.get("id")}
    rows = list(bundle["checkins"])

    default_status_idx = 0
    if st.session_state.get("filter_status") == "flagged":
        default_status_idx = 3
        del st.session_state["filter_status"]

    verify_opts = ["all", "pending", "approved", "flagged", "rejected", "appealed"]
    
    f1, f2, f3, f4 = st.columns((2, 1.2, 1.3, 1.6))
    session_opts = [{"id": "", "name": "All sessions"}] + sessions

    default_session_idx = 0
    target_sid = st.session_state.get("checkin_session_id")
    if target_sid:
        for i, s in enumerate(session_opts):
            if s.get("id") == target_sid:
                default_session_idx = i
                break
        del st.session_state["checkin_session_id"]

    session = f1.selectbox(
        "Session",
        session_opts,
        index=default_session_idx,
        format_func=lambda s: session_label(s) if s.get("id") else "All sessions",
    )
    status = f2.selectbox(
        "Verification",
        verify_opts,
        index=default_status_idx,
        format_func=lambda s: "All" if s == "all" else VERIFY_LABELS.get(s, s.title()),
    )
    risk_min = f3.slider("Min risk", 0.0, 1.0, 0.0, 0.05)
    
    queue_only = f4.checkbox("Review queue only", value=False, key="checkins_queue_only")

    if session.get("id"):
        rows = [r for r in rows if r.get("session_id") == session["id"]]
    if status != "all":
        rows = [r for r in rows if str(r.get("status") or "").lower() == status]
    if risk_min:
        rows = [r for r in rows if (r.get("risk_score") or 0) >= risk_min]
    if queue_only:
        rows = [r for r in rows if str(r.get("status") or "").lower() in {"flagged", "appealed"}]

    directory = user_directory(
        client,
        [r.get("student_id") for r in rows] + [r.get("reviewed_by_id") for r in bundle["checkins"]],
    )
    status_n = {k: sum(1 for r in rows if str(r.get("status") or "").lower() == k) for k in ("approved", "flagged", "appealed", "rejected")}
    queue_n = status_n["flagged"] + status_n["appealed"]
    st.markdown(
        stat_row(
            [
                ("Check-ins shown", len(rows), ""),
                ("Approved", status_n["approved"], "good" if status_n["approved"] else ""),
                ("Need review", queue_n, "warn" if queue_n else ""),
                ("Rejected", status_n["rejected"], "bad" if status_n["rejected"] else ""),
            ]
        ),
        unsafe_allow_html=True,
    )

    dataframe(
        checkins_to_frame(rows, directory, session_map),
        height=420,
        empty_title="No check-ins yet",
        empty_body="Students check in with the camera app. Results show face match and liveness here.",
    )

    course_map = {c["id"]: c for c in bundle["courses"] if c.get("id")}
    csv = gradebook_csv(rows, directory, session_map, course_map)
    export_buttons(csv, "saiv-checkins", "checkins")

    can_review = can_review_checkins(st.session_state.get("user"))
    panel_title("Review a flagged check-in" if can_review else "Flagged check-ins")
    if not can_review:
        if not can_view_flagged_checkins(st.session_state.get("user")):
            empty_state("No access", "Your role cannot view flagged check-ins.")
        else:
            flagged_rows = [r for r in rows if str(r.get("status") or "").lower() in {"flagged", "appealed"}]
            if not flagged_rows:
                empty_state("No flagged check-ins in this filter", "Nothing is waiting on an instructor right now.")
            else:
                st.caption("View only. Instructors and admins approve or reject flagged check-ins. TAs may view appeals but cannot resolve them.")
                dataframe(
                    checkins_to_frame(flagged_rows, directory, session_map),
                    height=min(320, 88 + 36 * len(flagged_rows)),
                )
    else:
        reviewable = [r for r in rows if str(r.get("status") or "").lower() in {"flagged", "appealed"}]
        if not reviewable:
            empty_state(
                "Nothing to review in this filter",
                "Switch on “Review queue only” to load flagged and appealed rows.",
            )
        else:
            reviewable = sorted(
                reviewable,
                key=lambda r: (str(r.get("status") or "").lower() != "appealed",
                               str(r.get("appealed_at") or r.get("checked_in_at") or "")),
            )
            appeals = sum(1 for r in reviewable if str(r.get("status") or "").lower() == "appealed")
            if appeals:
                st.caption(f"{appeals} appeal(s) waiting, listed first. Read the student's reason, "
                           "then approve or reject.")
            by_id = {str(r.get("id")): r for r in reviewable if r.get("id")}
            review_ids = [str(r["id"]) for r in reviewable if r.get("id")]
            if st.session_state.get("review_target_id") not in by_id:
                st.session_state.pop("review_target_id", None)
            chosen_id = st.selectbox(
                "Appeals and flagged check-ins",
                review_ids,
                format_func=lambda cid: (
                    f"{'APPEAL · ' if str(by_id[cid].get('status')).lower() == 'appealed' else ''}"
                    f"{display_name(by_id[cid], directory)} · "
                    f"{(session_map.get(by_id[cid].get('session_id')) or {}).get('name') or 'session'} · "
                    f"risk {fmt_score(by_id[cid].get('risk_score'))}"
                ),
                key="review_target_id",
            )
            target = by_id[chosen_id]
            detail = client.get_checkin(target["id"]) or target

            c1, c2, c3 = st.columns(3)
            c1.markdown(f"**Student**  \n{display_name(detail, directory)}")
            c2.markdown(
                f'<div class="kv"><span>Risk</span>{risk_meter(detail.get("risk_score"))}</div>'
                f'<div class="kv"><span>Liveness</span><b>{fmt_score(detail.get("liveness_score"))}</b></div>'
                f'<div class="kv"><span>Face match</span><b>{fmt_score(detail.get("face_match_score"))}</b></div>',
                unsafe_allow_html=True,
            )
            c3.markdown(f"**When**  \n{fmt_dt(detail.get('checked_in_at'))}")

            dist = detail.get("distance_from_venue_meters")
            dist_txt = f"{float(dist):.0f} m" if dist is not None else "No location (not verified)"
            net_flags = {"vpn_detected", "proxy_detected", "tor_detected"}
            net_txt = (
                "VPN / proxy signal"
                if any(s.get("signal_type") in net_flags for s in detail.get("risk_signals", []))
                else "No VPN signal detected"
            )
            trust = detail.get("device_trust_score")
            device_txt = (
                f"Registered device · {str(trust).title()} trust" if trust
                else ("Registered device" if detail.get("device_id") else "Unregistered device")
            )
            st.markdown(
                f'<div class="kv"><span>Raw image data</span><b>Not stored (privacy compliant)</b></div>'
                f'<div class="kv"><span>Device</span><b>{device_txt}</b></div>'
                f'<div class="kv"><span>Network</span><b>{net_txt}</b></div>'
                f'<div class="kv"><span>Geofence distance</span><b>{dist_txt}</b></div>',
                unsafe_allow_html=True,
            )

            signals = detail.get("risk_signals", [])
            if signals:
                st.caption("Risk factors")
                cols = st.columns(3)
                for idx, sig in enumerate(signals):
                    label = f"**{sig.get('signal_type', '').replace('_', ' ').title()}**  \n"
                    label += f"Severity: {sig.get('severity', '')} | Weight: {sig.get('weight', '')}"
                    cols[idx % 3].markdown(label)

            if detail.get("appeal_reason"):
                when = fmt_dt(detail.get("appealed_at"))
                st.info(f"**Student's appeal** ({when}): {detail['appeal_reason']}")

            with st.form("review_flagged_checkin", clear_on_submit=True):
                notes = st.text_area(
                    "Review notes",
                    placeholder="Face match failed the threshold; student to retry liveness…",
                )
                action_ok, action_no = st.columns(2)
                with action_ok:
                    st.markdown('<div class="review-approve">approve</div>', unsafe_allow_html=True)
                    do_approve = st.form_submit_button(
                        "Approve attendance",
                        use_container_width=True,
                        type="primary",
                    )
                with action_no:
                    st.markdown('<div class="review-reject">reject</div>', unsafe_allow_html=True)
                    do_reject = st.form_submit_button(
                        "Reject check-in",
                        use_container_width=True,
                        type="secondary",
                    )
            if do_approve:
                _review(client, detail, True, notes, directory, session_map)
            elif do_reject:
                _review(client, detail, False, notes, directory, session_map)

    if can_review:
        _render_history(bundle["checkins"], directory, session_map)


def _history_store() -> list:
    history = st.session_state.get("review_history")
    if not isinstance(history, list):
        history = []
        st.session_state.review_history = history
    return history


def _append_history(detail: dict, approve: bool, notes: str, directory: dict, sessions: dict) -> None:
    user = st.session_state.get("user") or {}
    sid = detail.get("session_id")
    session_name = detail.get("session_name")
    if not session_name and sessions and sid in sessions:
        session_name = sessions[sid].get("name")
    _history_store().insert(
        0,
        {
            "When": now_sgt().strftime("%d %b %Y · %H:%M"),
            "Student": display_name(detail, directory),
            "Decision": "Approved" if approve else "Rejected",
            "Reviewer": user.get("full_name") or user.get("email") or "You",
            "Session": session_name or "—",
            "Notes": (notes or "").strip(),
            "at": now_sgt().isoformat(),
            "checkin_id": detail.get("id"),
        },
    )
    persist_auth()


def _reviewer_name(row: dict, directory: dict) -> str:
    me = st.session_state.get("user") or {}
    reviewer_id = row.get("reviewed_by_id")
    if reviewer_id and reviewer_id in directory:
        person = directory[reviewer_id]
        return person.get("full_name") or person.get("email") or str(reviewer_id)[:8]
    if reviewer_id and reviewer_id == me.get("id"):
        return me.get("full_name") or me.get("email") or "You"
    return "—"


def _render_history(all_rows: list, directory: dict, sessions: dict) -> None:
    panel_title("Review history")
    events = list(_history_store())
    seen = {row.get("checkin_id") for row in events if row.get("checkin_id")}

    for row in all_rows:
        cid = row.get("id")
        if cid in seen:
            continue
        status = str(row.get("status") or "").lower()
        if status not in {"approved", "rejected"}:
            continue
        if not (row.get("reviewed_at") or row.get("reviewed_by_id")):
            continue
        sid = row.get("session_id")
        session_name = row.get("session_name")
        if not session_name and sessions and sid in sessions:
            session_name = sessions[sid].get("name")
        events.append(
            {
                "When": fmt_dt(row.get("reviewed_at") or row.get("verified_at"), "%d %b %Y · %H:%M"),
                "Student": display_name(row, directory),
                "Decision": "Approved" if status == "approved" else "Rejected",
                "Reviewer": _reviewer_name(row, directory),
                "Session": session_name or "—",
                "Notes": row.get("review_notes") or "",
            }
        )

    if not events:
        empty_state("No review actions yet", "Approve or reject a flagged check-in to start the history.")
        return

    show = pd.DataFrame(events)
    columns = [c for c in ("When", "Student", "Decision", "Reviewer", "Session", "Notes") if c in show.columns]
    render_html_table(show[columns], height=min(360, 88 + 28 * len(show)))


def _review(client, detail: dict, approve: bool, notes: str, directory: dict, sessions: dict) -> None:
    ok, result = client.review_checkin(detail["id"], approve, notes or None)
    if not ok:
        st.error(result)
        return
    _append_history(detail, approve, notes, directory, sessions)
    st.success("Approved.") if approve else st.success("Rejected.")
    st.rerun()


def render_student_attendance(client) -> None:
    from charts import bar_chart, line_chart
    from client import as_items
    from views.overview import _verification_donut

    page_header("My Attendance", "My Check-ins")
    live_badge()

    user = st.session_state.get("user") or {}
    my_id = user.get("id")

    with st.spinner("Loading your attendance…"):
        payload = client.my_checkins()

    rows = as_items(payload) if isinstance(payload, dict) else [r for r in (payload or []) if isinstance(r, dict)]

    if not rows:
        empty_state("No check-ins found", "You haven't checked in to any sessions yet.")
        return

    directory = user_directory(client, [my_id])
    sessions = attach_course_codes(
        client, client.gather_all(client.list_sessions, page_size=100, cap=500))
    session_map = {s["id"]: s for s in sessions if s.get("id")}

    total = len(rows)
    approved = sum(1 for r in rows if str(r.get("status")).lower() == "approved")
    flagged = sum(1 for r in rows if str(r.get("status")).lower() in {"flagged", "appealed"})
    rejected = sum(1 for r in rows if str(r.get("status")).lower() == "rejected")

    st.markdown(
        stat_row(
            [
                ("Total Check-ins", total, ""),
                ("Approved", approved, "good" if approved else ""),
                ("Needs Review", flagged, "warn" if flagged else ""),
                ("Rejected", rejected, "bad" if rejected else ""),
            ]
        ),
        unsafe_allow_html=True,
    )

    left, right = st.columns(2)
    with left:
        panel_title("Verification mix")
        _verification_donut(rows)
    with right:
        panel_title("Check-ins by course")
        counts: dict = {}
        for row in rows:
            session = session_map.get(row.get("session_id")) or {}
            code = row.get("course_code") or session.get("course_code") or "Other"
            counts[str(code)] = counts.get(str(code), 0) + 1
        ordered = sorted(counts.items(), key=lambda item: item[1], reverse=True)
        bar_chart(
            pd.DataFrame({"Course": [k for k, _ in ordered], "Check-ins": [n for _, n in ordered]}),
            "Course",
            "Check-ins",
            "Course",
            "Check-ins",
            height=220,
        )

    panel_title("Check-ins over the last 14 days")
    today = now_sgt().date()
    days = [today - timedelta(days=13 - i) for i in range(14)]
    per_day = {day: 0 for day in days}
    for row in rows:
        dt = parse_dt(row.get("checked_in_at"))
        if dt is not None and dt.date() in per_day:
            per_day[dt.date()] += 1
    line_chart(
        pd.DataFrame({"Day": [pd.Timestamp(d) for d in days], "Check-ins": [per_day[d] for d in days]}),
        "Day",
        "Check-ins",
        "Day",
        "Check-ins",
        height=220,
    )

    panel_title("Your attendance history")
    ordered_rows = sorted(
        rows,
        key=lambda r: parse_dt(r.get("checked_in_at")) or now_sgt() - timedelta(days=3650),
        reverse=True,
    )
    dataframe(checkins_to_frame(ordered_rows, directory, session_map), height=400, empty_title="No check-ins yet")

    _render_appeal_section(client, ordered_rows, session_map)


APPEAL_WINDOW_DAYS = 7

def _session_label(row: dict, session_map: dict) -> str:
    session = session_map.get(row.get("session_id")) or {}
    code = row.get("course_code") or session.get("course_code") or ""
    name = row.get("session_name") or session.get("name") or str(row.get("session_id") or "")[:8]
    return f"{code} · {name}" if code else name

def _deadline(row: dict):
    explicit = parse_dt(row.get("appeal_deadline"))
    if explicit is not None:
        return explicit
    checked = parse_dt(row.get("checked_in_at"))
    return checked + timedelta(days=APPEAL_WINDOW_DAYS) if checked else None

def _can_appeal(row: dict) -> bool:
    if "can_appeal" in row:
        return bool(row.get("can_appeal"))
    if str(row.get("status") or "").lower() not in {"rejected", "flagged"}:
        return False
    if row.get("appealed_at") or row.get("appeal_reason"):
        return False
    deadline = _deadline(row)
    return deadline is None or now_sgt() <= deadline

def _appeal_outcome(row: dict) -> str:
    status = str(row.get("status") or "").lower()
    if status == "appealed":
        return "Waiting for review"
    if status == "approved":
        return "Approved · marked present"
    if status == "rejected":
        return "Rejected · marked absent"
    return verification_label(row.get("status"))

def _render_appeal_outcomes(rows: list, session_map: dict) -> None:
    appealed = [r for r in rows if r.get("appealed_at") or r.get("appeal_reason")
                or str(r.get("status") or "").lower() == "appealed"]
    if not appealed:
        return
    panel_title("Your appeals")
    waiting = sum(1 for r in appealed if str(r.get("status") or "").lower() == "appealed")
    st.caption(f"{len(appealed)} appeal(s) · {waiting} waiting for your instructor. "
               "Each check-in can be appealed once; the decision is final.")
    table = pd.DataFrame([
        {
            "Session": _session_label(r, session_map),
            "Checked in": fmt_dt(r.get("checked_in_at"), "%d %b %Y · %H:%M"),
            "Your reason": r.get("appeal_reason") or "—",
            "Outcome": _appeal_outcome(r),
            "Instructor's note": r.get("review_notes") or "—",
            "Decided": fmt_dt(r.get("reviewed_at"), "%d %b %Y · %H:%M")
            if str(r.get("status") or "").lower() != "appealed" and r.get("reviewed_at") else "—",
        }
        for r in sorted(appealed, key=lambda r: str(r.get("appealed_at") or ""), reverse=True)
    ])
    render_html_table(table, height=min(360, 88 + 40 * len(table)))

def _render_appeal_section(client, rows: list, session_map: dict | None = None) -> None:
    session_map = session_map or {}
    _render_appeal_outcomes(rows, session_map)

    panel_title("Appeal a check-in")
    disputable = [r for r in rows if str(r.get("status") or "").lower() in {"rejected", "flagged"}]
    appealable = [r for r in disputable if _can_appeal(r)]
    already = sum(1 for r in disputable if r.get("appealed_at") or r.get("appeal_reason"))
    expired = len(disputable) - len(appealable) - already
    notes = []
    if already:
        notes.append(f"{already} already appealed (decision final)")
    if expired > 0:
        notes.append(f"{expired} past the {APPEAL_WINDOW_DAYS}-day appeal window")
    if not appealable:
        empty_state("Nothing to appeal",
                    "Flagged or rejected check-ins can be appealed once, within "
                    f"{APPEAL_WINDOW_DAYS} days of checking in."
                    + (f" ({'; '.join(notes)}.)" if notes else ""))
        return
    st.caption(f"You can appeal a flagged or rejected check-in once, within {APPEAL_WINDOW_DAYS} "
               "days of checking in. Your instructor decides, and that decision is final."
               + (f" ({'; '.join(notes)}.)" if notes else ""))
    by_id = {str(r.get("id")): r for r in appealable if r.get("id")}
    ids = list(by_id)
    chosen = st.selectbox(
        "Choose a check-in",
        ids,
        format_func=lambda cid: (
            f"{_session_label(by_id[cid], session_map)} · "
            f"{verification_label(by_id[cid].get('status'))} · "
            f"{fmt_dt(by_id[cid].get('checked_in_at'))} · "
            f"appeal by {fmt_dt(_deadline(by_id[cid]), '%d %b')}"
        ),
        key="student_appeal_target",
    )
    with st.form("student_appeal_form", clear_on_submit=True):
        reason = st.text_area(
            "Reason for appeal",
            placeholder="Explain why this check-in should be reviewed (e.g. geofence edge case, face match error).",
        )
        submitted = st.form_submit_button("Submit appeal", use_container_width=True, type="primary")
    if not submitted:
        return
    if not reason.strip():
        st.error("Please provide a reason for the appeal.")
        return
    target = by_id[chosen]
    ok, result = client.appeal_checkin(target["id"], reason.strip())
    if not ok:
        st.error(result)
        return
    st.success("Appeal submitted. Your instructor will review it; you'll see the outcome "
               "under \"Your appeals\".")
    st.rerun()