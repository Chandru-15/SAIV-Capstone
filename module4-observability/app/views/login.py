from __future__ import annotations

from typing import Dict, Optional

import streamlit as st

from auth_session import clear_auth, persist_auth
from client import SaivClient
from login_guard import lock_from_backend, lockout_message, record, snapshot
from theme import MARK_SVG
from ui import health_pills, normalize_user


def _reset_state() -> dict:
    return st.session_state.setdefault("_forgot", {"stage": "email", "email": ""})

def _forgot_password_body() -> None:
    state = _reset_state()
    if state["stage"] == "done":
        st.success("Your password has been changed. Close this and log in with it.")
        if st.button("Back to login", use_container_width=True, key="forgot_done"):
            st.session_state.pop("_forgot", None)
            st.session_state.pop("_forgot_inline", None)
            st.rerun()
        return

    if state["stage"] == "email":
        st.caption("Enter the email you sign in with. We'll email you a 6-digit code.")
        email = st.text_input("Email", value=state["email"], placeholder="you@saiv.edu",
                              key="forgot_email")
        if st.button("Send code", use_container_width=True, key="forgot_send"):
            email = (email or "").strip()
            if "@" not in email:
                st.error("Enter the email you sign in with.")
                return
            ok, message = SaivClient().forgot_password(email)
            if not ok:
                st.error(message)
                return
            state.update(stage="code", email=email, notice=message)
            _rerun_dialog()
        return

    st.info(state.get("notice") or "If that email has an account, we've sent it a 6-digit code.")
    st.caption(f"Code sent to {state['email']}. It expires in 10 minutes.")
    code = st.text_input("6-digit code", max_chars=6, key="forgot_code")
    new_pw = st.text_input("New password", type="password", key="forgot_new_pw",
                           help="At least 8 characters.")
    again = st.text_input("Confirm new password", type="password", key="forgot_new_pw2")
    if st.button("Change password", use_container_width=True, key="forgot_confirm"):
        if not code.strip().isdigit() or len(code.strip()) != 6:
            st.error("Enter the 6-digit code from the email.")
            return
        if len(new_pw) < 8:
            st.error("The new password must be at least 8 characters.")
            return
        if new_pw != again:
            st.error("The two passwords don't match.")
            return
        ok, message = SaivClient().confirm_password_reset(state["email"], code.strip(), new_pw)
        if ok:
            state["stage"] = "done"
            _rerun_dialog()
        else:
            st.error(message)
    left, right = st.columns(2)
    with left:
        if st.button("Send a new code", use_container_width=True, key="ghost_forgot_resend"):
            SaivClient().forgot_password(state["email"])
            st.toast("If that email has an account, a new code is on its way.")
    with right:
        if st.button("Use another email", use_container_width=True, key="ghost_forgot_back"):
            state.update(stage="email", notice=None)
            _rerun_dialog()
    st.caption("No email? Your administrator can still reset your password; "
               "your request is already on their list.")

def _rerun_dialog() -> None:
    import inspect

    try:
        scoped = "scope" in inspect.signature(st.rerun).parameters
    except (TypeError, ValueError):
        scoped = False
    if scoped:
        st.rerun(scope="fragment")
    st.rerun()

def _forgot_password_dialog() -> None:
    if _reset_state().get("stage") == "done":
        st.session_state.pop("_forgot", None)
    if hasattr(st, "dialog"):
        @st.dialog("Reset your password")
        def _dialog() -> None:
            _forgot_password_body()

        _dialog()
    else:
        st.session_state["_forgot_inline"] = True

def render_login(health: Optional[Dict[str, bool]] = None) -> None:
    guard = snapshot()
    locked = bool(guard["locked"])

    with st.form("saiv_login"):
        st.markdown(
            f"""
            <div class="login-hero">
                <div class="login-mark">{MARK_SVG}</div>
                <div class="login-title">SAIV</div>
                <div class="login-lead">Secure attendance for courses, sessions, and reviews.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown('<p style="font-size: 0.82rem; font-weight: 500; color: var(--muted); margin-bottom: 0.2rem; margin-top: 0;">Email</p>', unsafe_allow_html=True)
        email = st.text_input("Work email", placeholder="admin@saiv.edu", label_visibility="collapsed")

        st.markdown('<p style="font-size: 0.82rem; font-weight: 500; color: var(--muted); margin-bottom: 0.2rem; margin-top: 0.5rem;">Password</p>', unsafe_allow_html=True)
        password = st.text_input("Password", type="password", placeholder="Enter your password", label_visibility="collapsed")

        st.markdown('<div style="height: 8px;"></div>', unsafe_allow_html=True)

        submitted = st.form_submit_button(
            "Log in",
            use_container_width=True,
            disabled=locked,
        )

    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        if st.button("Forgot password?", use_container_width=True, key="ghost_forgot_pw"):
            _forgot_password_dialog()
    if not hasattr(st, "dialog") and st.session_state.get("_forgot_inline"):
        _forgot_password_body()

    if health:
        st.markdown(
            f'<div class="login-health">{health_pills(health)}</div>',
            unsafe_allow_html=True,
        )

    if locked:
        st.error(lockout_message(guard))
        return

    if not submitted:
        return

    if not email or not password:
        st.error("Enter both email and password.")
        return

    pre = snapshot(email.strip())
    if pre["locked"]:
        st.error(lockout_message(pre))
        return

    client = SaivClient()
    with st.spinner("Logging in…"):
        result = client.login(email.strip(), password)
    state = record(email.strip())

    if not result:
        if client.last_status == 429:
            wait = client.last_retry_after or 3600
            locked_state = lock_from_backend(wait)
            st.error(lockout_message(locked_state, wait))
            return
        if state["locked"]:
            st.error(lockout_message(state))
            return
        if client.last_status is None:
            st.error("Can't reach the SAIV server right now. Please try again in a minute.")
            return
        left = state["remaining"]
        extra = f" {left} of {state['limit']} attempts left this hour."
        if client.last_status in (401, 403):
            message = client.last_error or "Incorrect email or password."
        else:
            message = "Sign-in failed. Please try again."
        st.error(message + extra)
        return

    user = normalize_user(result.get("user") or {})
    role = user.get("role")

    if role not in {"student", "ta", "instructor", "admin"}:
        clear_auth()
        st.error("This account has no dashboard access.")
        return

    st.session_state.access_token = result["access_token"]
    st.session_state.refresh_token = result.get("refresh_token")
    st.session_state.api_prefix = client.api_prefix
    st.session_state.user = user

    if role == "student":
        st.session_state.nav = "my_attendance"
    else:
        st.session_state.nav = "overview"

    persist_auth(sync_url=False)
    st.rerun()