from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

st._config.set_option("theme.base", "light")

from access import refresh_actor, require_admin, token_role
from auth_session import clear_auth, hydrate_auth, persist_auth
from client import SaivClient, probe_all_health
from theme import inject_css
from ui import (
    health_list,
    is_admin,
    is_staff,
    is_student,
    nav_label,
    normalize_nav,
    normalize_user,
    pages_for,
    render_nav_tabs,
    sidebar_brand,
    sidebar_user,
    user_role,
    workspace_label,
)
from views.admin import render_admin_assignments
from views.audit import render_audit
from views.checkins import render_checkins, render_student_attendance
from views.face_requests import render_face_requests
from views.login import render_login
from views.metrics import render_metrics
from views.overview import render_overview
from views.sessions import render_sessions

st.set_page_config(
    page_title="SAIV",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

def _client() -> SaivClient:
    user = st.session_state.get("user") or {}
    return SaivClient(
        token=st.session_state.get("access_token"),
        refresh_token=st.session_state.get("refresh_token"),
        api_prefix=st.session_state.get("api_prefix"),
        role=token_role() or user.get("role"),
    )

@st.cache_data(ttl=12, show_spinner=False)
def _health() -> dict:
    return probe_all_health()

def _logout() -> None:
    client = _client()
    try:
        client.logout()
    except Exception:
        pass
    clear_auth()
    st.rerun()

def _live_refresh() -> None:
    st.session_state.live = True
    st.session_state["_live_stamp"] = now_sgt_label()
    try:
        from streamlit_autorefresh import st_autorefresh
        from config import REFRESH_SECONDS
        tick = st_autorefresh(interval=REFRESH_SECONDS * 1000, key="saiv-live-refresh")
        if tick:
            try:
                _health.clear()
            except Exception:
                pass
    except Exception:
        pass

def now_sgt_label() -> str:
    from client import now_sgt
    return now_sgt().strftime("%H:%M:%S")

def main() -> None:
    hydrate_auth()
    inject_css()
    st.session_state.live = True
    health = _health()
    token = st.session_state.get("access_token")
    user = st.session_state.get("user") or {}

    if not token:
        render_login(health)
        return

    with st.sidebar:
        client = _client()
        user = normalize_user(user)
        if not user_role(user):
            user = normalize_user(client.me() or user)
        st.session_state.user = user
        persist_auth(sync_url=True)
        sidebar_brand(user)
        
        role = user_role(user)
        if role in {"ta", "instructor", "admin", "student"}:
            render_nav_tabs(user)
            
            if is_admin(user):
                nav_label("Services")
                st.markdown(health_list(health), unsafe_allow_html=True)
                
            st.markdown('<div class="workspace-tools"></div>', unsafe_allow_html=True)
            do_refresh = st.button("Refresh", key="sidebar_refresh", use_container_width=True)
            do_logout = st.button("Sign out", key="sidebar_logout", use_container_width=True)
            if do_refresh:
                st.session_state.pop("_user_dir", None)
                try:
                    _health.clear()
                except Exception:
                    pass
                try:
                    st.cache_data.clear()
                except Exception:
                    pass
                st.rerun()
            if do_logout:
                _logout()
                
            st.markdown("<hr style='margin: 1rem 0; border-color: var(--line);'/>", unsafe_allow_html=True)
            sidebar_user(user)
        else:
            nav_label("Access")
            st.caption(f"Unknown role. {workspace_label(user)}")
            if st.button("Sign out", use_container_width=True):
                _logout()

    client = _client()
    user = refresh_actor(client)
    if not user_role(user):
        user = normalize_user(client.me() or user)
    st.session_state.user = user
    persist_auth()

    if not is_staff(user) and not is_student(user):
        st.error("Sign in with a valid SAIV account.")
        return

    _live_refresh()

    allowed_pages = pages_for(user)
    allowed = {key for _, key in allowed_pages}
    page = normalize_nav()
    if page not in allowed:
        page = allowed_pages[0][1] if allowed_pages else "overview"
        st.session_state.nav = page

    if page == "metrics":
        if not require_admin(client):
            return
        render_metrics(client, health)
        return
    if page == "audit":
        if not require_admin(client):
            return
        render_audit(client)
        return
    if page == "admin_ops":
        if not require_admin(client):
            return
        render_admin_assignments(client)
        return
    if page == "face_requests":
        if not require_admin(client):
            return
        render_face_requests(client)
        return
    if page == "overview":
        render_overview(client)
    elif page == "sessions":
        render_sessions(client)
    elif page == "my_attendance":
        render_student_attendance(client)
    else:
        render_checkins(client)

main()