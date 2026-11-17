from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Optional

import streamlit as st

from client import SGT

LOGIN_LIMIT = 60
LOGIN_WINDOW_SECONDS = 3600
DASHBOARD_IP_KEY = "ip:dashboard"


@st.cache_resource
def _buckets() -> dict:
    return {}


def _prune(stamps: list, now: float) -> list:
    cutoff = now - LOGIN_WINDOW_SECONDS
    return [t for t in stamps if t > cutoff]


def _email_key(email: str) -> str:
    return f"email:{(email or '').strip().lower()}"


def snapshot(email: str = "") -> dict:
    now = time.time()
    store = _buckets()
    ip = _prune(list(store.get(DASHBOARD_IP_KEY, [])), now)
    store[DASHBOARD_IP_KEY] = ip
    mail: list = []
    if email.strip():
        mail = _prune(list(store.get(_email_key(email), [])), now)
        store[_email_key(email)] = mail

    remaining = max(0, LOGIN_LIMIT - len(ip))
    if email.strip():
        remaining = min(remaining, max(0, LOGIN_LIMIT - len(mail)))

    locked_for = 0.0
    if len(ip) >= LOGIN_LIMIT:
        locked_for = max(locked_for, ip[0] + LOGIN_WINDOW_SECONDS - now)
    if email.strip() and len(mail) >= LOGIN_LIMIT:
        locked_for = max(locked_for, mail[0] + LOGIN_WINDOW_SECONDS - now)

    extra = float(store.get("backend_lock_until") or 0)
    if extra > now:
        locked_for = max(locked_for, extra - now)

    until = None
    if locked_for > 0:
        until = datetime.now(SGT) + timedelta(seconds=int(locked_for) + 1)
    return {
        "remaining": remaining,
        "locked": locked_for > 0,
        "retry_in": max(0, int(locked_for)),
        "until": until,
        "limit": LOGIN_LIMIT,
    }


def record(email: str) -> dict:
    state = snapshot(email)
    if state["locked"]:
        return state
    now = time.time()
    store = _buckets()
    store.setdefault(DASHBOARD_IP_KEY, []).append(now)
    if email.strip():
        store.setdefault(_email_key(email), []).append(now)
    return snapshot(email)


def lock_from_backend(seconds: int) -> dict:
    store = _buckets()
    store["backend_lock_until"] = time.time() + max(1, int(seconds))
    return snapshot()


def lockout_message(state: dict, backend_retry: Optional[int] = None) -> str:
    seconds = max(int(state.get("retry_in") or 0), int(backend_retry or 0))
    until = state.get("until")
    if backend_retry and not until:
        until = datetime.now(SGT) + timedelta(seconds=int(backend_retry))
    when = until.strftime("%H:%M") if until else "later"
    minutes = max(1, (seconds + 59) // 60)
    return (
        f"Too many sign-in attempts. This console allows {LOGIN_LIMIT} tries per hour. "
        f"Wait {minutes} min, then try again at {when} SGT."
    )