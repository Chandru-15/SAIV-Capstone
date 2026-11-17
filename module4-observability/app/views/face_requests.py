"""Face re-enrolment requests — the admin's decision queue.

A student cannot replace their own face template. Their template is what every
later check-in is matched against, and the password this used to need was
something the student always knows, so it proved nothing about anyone else
agreeing. They ask instead, and somebody responsible for them decides.

Nothing biometric passes through this page: a request carries only a reason.
The student captures the replacement themselves, afterwards, in the portal.
"""

from __future__ import annotations

import streamlit as st

from client import as_items, parse_dt
from ui import empty_state, page_header, panel, plain_name

STATUS_LABELS = {
    "pending": "Awaiting a decision",
    "approved": "Approved",
    "denied": "Denied",
}


def _btn(kind: str, label: str, **kwargs) -> bool:
    st.markdown(f'<div class="btn-mark btn-{kind}"></div>', unsafe_allow_html=True)
    return st.button(label, **kwargs)


def _when(value) -> str:
    stamp = parse_dt(value)
    return stamp.strftime("%d %b %Y, %H:%M") if stamp else "—"


def _people(client, rows: list) -> dict:
    """Resolve student ids to accounts.

    The API returns ``user_id`` rather than an embedded student object, so that
    reviewing a request is not also a second route to account details. The
    names are fetched separately and only for the rows on screen.
    """
    ids = {str(row.get("user_id")) for row in rows if row.get("user_id")}
    try:
        return client.get_users(ids) if ids else {}
    except Exception:
        return {}


def _who(people: dict, row: dict) -> str:
    person = people.get(str(row.get("user_id"))) or {}
    name = plain_name(person.get("full_name"))
    email = person.get("email") or ""
    if name and email:
        return f"{name} ({email})"
    return name or email or str(row.get("user_id") or "Unknown student")


def _pending(client, rows: list, people: dict) -> None:
    for row in rows:
        request_id = str(row.get("id") or "")
        if not request_id:
            continue
        with panel(_who(people, row), f"Asked {_when(row.get('created_at'))}"):
            reason = (row.get("reason") or "").strip()
            if reason:
                st.markdown("**Their reason**")
                st.write(reason)
            else:
                st.caption("No reason given.")
            notes = st.text_area(
                "Note for the student (optional)",
                key=f"fr_notes_{request_id}",
                help="Shown to the student either way. Say why, especially on a refusal.",
            )
            approve, deny = st.columns(2)
            with approve:
                if _btn("ok", "Approve", use_container_width=True,
                        key=f"fr_ok_{request_id}"):
                    ok, result = client.review_face_reenroll(request_id, True, notes)
                    if ok:
                        st.success("Approved. They can now capture one new photo.")
                        st.rerun()
                    else:
                        st.error(result)
            with deny:
                if _btn("danger", "Deny", use_container_width=True,
                        key=f"fr_no_{request_id}"):
                    ok, result = client.review_face_reenroll(request_id, False, notes)
                    if ok:
                        st.warning("Denied. They can ask again.")
                        st.rerun()
                    else:
                        st.error(result)


def _decided(rows: list, people: dict) -> None:
    table = []
    for row in rows:
        status = str(row.get("status") or "")
        table.append({
            "Student": _who(people, row),
            "Asked": _when(row.get("created_at")),
            "Decision": STATUS_LABELS.get(status, status or "—"),
            "Decided": _when(row.get("reviewed_at")),
            # An approval stays "approved" after it is spent, so this column is
            # the only thing that distinguishes one still worth using.
            "Used": _when(row.get("used_at")) if row.get("used_at") else "Not yet",
            "Note": (row.get("review_notes") or "").strip() or "—",
        })
    st.dataframe(table, use_container_width=True, hide_index=True)


def render_face_requests(client) -> None:
    page_header(
        "Verification",
        "Face re-enrollment requests",
        "A student cannot replace their own face template. They ask, an admin decides, "
        "and one approval covers exactly one new photo.",
    )

    payload = client.face_reenroll_requests(limit=100)
    if payload is None:
        empty_state("Could not load the queue",
                    client.last_error or "The backend did not answer. Try again shortly.")
        return

    rows = as_items(payload)
    pending = [r for r in rows if str(r.get("status")) == "pending"]
    decided = [r for r in rows if str(r.get("status")) != "pending"]
    people = _people(client, rows)

    waiting, history = st.tabs([
        f"Awaiting a decision ({len(pending)})",
        f"Already decided ({len(decided)})",
    ])
    with waiting:
        if not pending:
            empty_state(
                "Nothing waiting",
                "No student is asking to re-enrol right now. "
                "New requests from any student appear here.",
            )
        else:
            _pending(client, pending, people)
    with history:
        if not decided:
            empty_state("No decisions yet",
                        "Approvals and refusals are listed here once you make them.")
        else:
            _decided(decided, people)