from __future__ import annotations

from collections import Counter

import json

import pandas as pd
import streamlit as st

from access import require_admin
from charts import bar_chart
from client import as_items, as_total, pick
from ui import empty_state, fmt_dt, kpi_card, live_badge, page_header, panel_title, user_directory

KNOWN_ACTIONS = [
    "",
    "login_success",
    "login_failed",
    "logout",
    "user_created",
    "user_updated",
    "checkin_attempted",
    "checkin_approved",
    "checkin_flagged",
    "checkin_rejected",
    "checkin_appealed",
    "checkin_reviewed",
    "session_created",
    "session_updated",
    "session_deleted",
    "enrollment_added",
    "enrollment_removed",
    "device_registered",
    "face_enrolled",
    "data_exported",
    "security_violation",
    "course_created",
    "course_updated",
    "course_deleted",
    "face_unenrolled",
    "liveness_challenge_issued",
    "instructor_assigned",
    "instructor_unassigned",
    "ta_assigned",
    "ta_unassigned",
    "course_structure_updated",
    "groups_allocated",
    "group_member_moved",
    "session_auto_closed",
    "user_activated",
    "user_deactivated",
    "user_deleted",
    "user_restored",
    "password_reset_requested",
    "password_reset",
    "password_reset_emailed",
]

EVENT_TYPES = {
    "All event types": None,
    "Authentication": ["login_success", "login_failed", "logout"],
    "Check-in": [
        "checkin_attempted",
        "checkin_approved",
        "checkin_flagged",
        "checkin_rejected",
        "checkin_appealed",
        "checkin_reviewed",
    ],
    "Session": ["session_created", "session_updated", "session_deleted", "session_auto_closed"],
    "User": ["user_created", "user_updated", "user_activated", "user_deactivated", "user_deleted",
             "user_restored", "password_reset_requested", "password_reset_emailed", "password_reset",
             "enrollment_added", "enrollment_removed"],
    "Course": ["course_created", "course_updated", "course_deleted", "course_structure_updated"],
    "Assignment": ["instructor_assigned", "instructor_unassigned", "ta_assigned", "ta_unassigned",
                   "groups_allocated", "group_member_moved"],
    "Security": [
        "security_violation", "data_exported", "device_registered", "face_enrolled",
        "face_unenrolled", "liveness_challenge_issued",
    ],
}

SEVERITY = {
    "login_failed": "high",
    "security_violation": "critical",
    "checkin_rejected": "high",
    "checkin_flagged": "medium",
    "checkin_appealed": "medium",
    "session_deleted": "medium",
    "user_deleted": "medium",
    "user_deactivated": "medium",
    "password_reset": "medium",
    "data_exported": "low",
    "user_created": "low",
    "login_success": "low",
    "logout": "low",
    "checkin_approved": "low",
    "checkin_attempted": "low",
    "session_created": "low",
    "session_updated": "low",
    "face_enrolled": "low",
}

SEVERITY_BG = {
    "critical": "background-color: rgba(239, 68, 68, 0.28);",
    "high": "background-color: rgba(239, 68, 68, 0.16);",
    "medium": "background-color: rgba(245, 158, 11, 0.18);",
    "low": "",
}


DETAIL_LABELS = {
    "scheduled_deletion_at": lambda v: f"erased after {str(v)[:10]}",
    "deletion_cancelled": lambda v: "deletion cancelled" if v else "",
    "by_admin": lambda v: "temporary password issued by admin" if v else "",
    "self_service": lambda v: "changed with emailed code" if v else "",
    "code_emailed": lambda v: "reset code emailed by admin" if v else "",
    "reason": lambda v: {"no_account": "no account with this email",
                         "account_deleted": "account deleted",
                         "wrong_code": "wrong code"}.get(str(v), str(v).replace("_", " ")),
    "attempts": lambda v: f"attempt {v}",
    "format": lambda v: f"format {v}",
    "records": lambda v: f"{v} records",
}

def _details(row: dict) -> dict:
    raw = row.get("details")
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}

def _detail_text(details: dict) -> str:
    parts = []
    for key, label in DETAIL_LABELS.items():
        if key in details and details[key] not in (None, ""):
            text = label(details[key])
            if text:
                parts.append(text)
    return " · ".join(parts) or "—"

def _account(row: dict, details: dict, directory: dict) -> str:
    if str(row.get("resource_type") or "") != "user":
        return "—"
    target = directory.get(row.get("resource_id"), {}) if row.get("resource_id") else {}
    return target.get("email") or details.get("email") or (str(row.get("resource_id") or "")[:8] or "—")

def _severity(action_name: str, success) -> str:
    if action_name in SEVERITY:
        return SEVERITY[action_name]
    return "high" if success is False else "low"


def _color_rows(frame: pd.DataFrame):
    def apply_row(row):
        style = SEVERITY_BG.get(str(row.get("Severity") or ""), "")
        return [style] * len(row)

    return frame.style.apply(apply_row, axis=1)


def render_audit(client) -> None:
    if not require_admin(client):
        return
    page_header("Monitoring", "Audit Log", "Who did what, and when. Failed actions are highlighted.")
    live_badge()

    f1, f2, f3, f4, f5, f6 = st.columns((1.3, 1.3, 1.4, 1.1, 0.9, 0.7))
    event_type = f1.selectbox("Event type", list(EVENT_TYPES.keys()))
    type_actions = EVENT_TYPES.get(event_type)
    action_opts = [""] + (type_actions or [a for a in KNOWN_ACTIONS if a])
    if st.session_state.get("audit_action") not in action_opts:
        st.session_state.pop("audit_action", None)
    action = f2.selectbox("Action", action_opts, format_func=lambda a: a or "All actions",
                          key="audit_action")
    user_q = f3.text_input("User or account", placeholder="email, name, or user id")
    success = f4.selectbox("Result", ["all", "success", "failure"])
    limit = f5.selectbox("Page size", [25, 50, 100], index=1)
    page_no = int(f6.number_input("Page", min_value=1, value=1, step=1))

    params = {"limit": limit, "offset": (page_no - 1) * limit}
    if action:
        params["action"] = action
    if success == "success":
        params["success"] = True
    elif success == "failure":
        params["success"] = False
    if user_q and len(user_q.strip()) >= 8 and " " not in user_q and "@" not in user_q:
        params["user_id"] = user_q.strip()

    payload = client.audit_logs(**params)
    if client.last_status == 403:
        empty_state("Access denied", "The API refused this audit request.")
        return
    if payload is None:
        empty_state("Audit unavailable", client.last_error or "The audit API did not return a trail.")
        return
    live_rows = as_items(payload)
    rows = live_rows
    total = as_total(payload, live_rows)
    directory = user_directory(
        client,
        [r.get("user_id") for r in rows]
        + [r.get("resource_id") for r in rows if str(r.get("resource_type") or "") == "user"],
    )

    allowed_actions = EVENT_TYPES.get(event_type)
    if allowed_actions and not action:
        rows = [r for r in rows if str(r.get("action") or "") in allowed_actions]

    if user_q:
        needle = user_q.strip().lower()
        filtered = []
        for row in rows:
            uid = row.get("user_id")
            who = directory.get(uid, {}) if uid else {}
            details = _details(row)
            hay = " ".join(
                str(x or "").lower()
                for x in (uid, who.get("email"), who.get("full_name"), row.get("ip_address"),
                          _account(row, details, directory), details.get("email"))
            )
            if needle in hay:
                filtered.append(row)
        rows = filtered

    summary = client.audit_summary(days=7)
    if not summary:
        summary = {
            "period_days": 7,
            "total_logs": total,
            "by_action": dict(Counter(str(r.get("action") or "unknown") for r in rows)),
        }

    failures = sum(1 for row in rows if row.get("success") is False)
    k1, k2, k3 = st.columns(3)
    k1.markdown(kpi_card("Events", str(total)), unsafe_allow_html=True)
    k2.markdown(kpi_card("In view", str(len(rows))), unsafe_allow_html=True)
    k3.markdown(
        kpi_card("Failures", str(failures), extra_class="kpi-warn" if failures else "kpi-good"),
        unsafe_allow_html=True,
    )

    left, right = st.columns((1.15, 1.7))
    with left:
        panel_title("Events by action")
        by_action = pick(summary, "by_action", default={}) or {}
        if by_action:
            frame = pd.DataFrame(
                {"Action": list(by_action.keys()), "Events": list(by_action.values())}
            )
            bar_chart(frame, "Action", "Events", "Action", "Events", horizontal=True, color_by=True)
        else:
            empty_state("No action mix", "Events will group here once the trail has rows.")
    with right:
        panel_title("Trail")
        if not rows:
            empty_state("No audit events", "The backend returned an empty trail for these filters.")
        else:
            table = []
            for row in rows:
                action_name = str(row.get("action") or "")
                uid = row.get("user_id")
                who = directory.get(uid, {}) if uid else {}
                details = _details(row)
                table.append(
                    {
                        "When": fmt_dt(row.get("timestamp"), "%d %b %H:%M:%S"),
                        "Action": action_name,
                        "Done by": who.get("email") or who.get("full_name")
                        or (str(uid)[:8] if uid else "anonymous"),
                        "Account": _account(row, details, directory),
                        "Details": _detail_text(details),
                        "Event type": (row.get("resource_type") or action_name.split("_")[0] or "system"),
                        "OK": "yes" if row.get("success") else "no",
                        "Severity": _severity(action_name, row.get("success")),
                        "IP": row.get("ip_address") or "—",
                    }
                )
            frame = pd.DataFrame(table)
            st.dataframe(_color_rows(frame), use_container_width=True, hide_index=True, height=360)

    export_rows = rows or as_items(payload)
    if export_rows:
        frame = pd.json_normalize(export_rows)
        st.download_button(
            "Export audit trail CSV",
            data=frame.to_csv(index=False).encode("utf-8-sig"),
            file_name="saiv-audit-trail.csv",
            mime="text/csv; charset=utf-8",
            use_container_width=True,
        )