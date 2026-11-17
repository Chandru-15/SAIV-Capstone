from __future__ import annotations

import base64
import json
from typing import Optional

import streamlit as st

from auth_session import persist_auth
from ui import empty_state, normalize_user, user_role


def token_role(token: Optional[str] = None) -> str:
    raw = token if token is not None else st.session_state.get("access_token")
    if not raw or raw.count(".") < 2:
        return ""
    try:
        payload = raw.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except Exception:
        return ""
    return str(data.get("role") or "").split(".")[-1].strip().lower()


def refresh_actor(client) -> dict:
    live = client.me()
    if isinstance(live, dict) and (live.get("role") or live.get("email")):
        user = normalize_user(live)
        st.session_state.user = user
        persist_auth()
        try:
            client.role = user_role(user)
        except Exception:
            pass
        return user
    return normalize_user(st.session_state.get("user") or {})


def is_admin_actor(user: Optional[dict] = None, token: Optional[str] = None) -> bool:
    session_role = user_role(user if user is not None else st.session_state.get("user"))
    jwt_role = token_role(token)
    if jwt_role and session_role and jwt_role != session_role:
        return False
    role = jwt_role or session_role
    return role == "admin"


def deny_page(title: str = "Access denied") -> None:
    empty_state(title, "This page is limited to authorized accounts.")


def require_admin(client) -> bool:
    user = refresh_actor(client)
    if is_admin_actor(user, st.session_state.get("access_token")):
        return True
    deny_page("Access denied")
    return False


def require_staff(client) -> bool:
    user = refresh_actor(client)
    role = user_role(user)
    if role in {"instructor", "ta", "admin"}:
        return True
    deny_page("Access denied")
    return False