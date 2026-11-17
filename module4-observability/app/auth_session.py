from __future__ import annotations

import json
import secrets
import time
from typing import Optional

import streamlit as st

AUTH_KEYS = ("access_token", "refresh_token", "api_prefix", "user", "nav", "live", "review_history")

# How long a stored session may sit untouched. Slid forward on every
# persist_auth, which runs on each rerun, so an active user is never cut off;
# the client auto-refreshes its access token (client.py), so a fixed lifetime
# here would sign people out while their refresh token was still valid.
SID_IDLE_SECONDS = 12 * 60 * 60


@st.cache_resource
def _auth_store() -> dict:
    return {}


def _sid_from_url() -> Optional[str]:
    try:
        value = st.query_params.get("s")
    except Exception:
        try:
            value = st.experimental_get_query_params().get("s")
        except Exception:
            return None
    if isinstance(value, list):
        return value[0] if value else None
    if isinstance(value, str) and value:
        return value
    return None


def _replace_url_sid(sid: Optional[str]) -> None:
    try:
        import streamlit.components.v1 as components
    except Exception:
        return
    payload = json.dumps(sid)
    components.html(
        f"""<!DOCTYPE html><html><body><script>
        (function() {{
          var sid = {payload};
          var w = window;
          try {{
            while (w.parent && w.parent !== w) {{ w = w.parent; }}
          }} catch (e) {{}}
          try {{
            var u = new URL(w.location.href);
            if (sid) {{ u.searchParams.set('s', sid); }}
            else {{ u.searchParams.delete('s'); }}
            w.history.replaceState({{}}, '', u.pathname + u.search + u.hash);
          }} catch (e) {{}}
        }})();
        </script></body></html>""",
        height=0,
        width=0,
    )


def _write_sid(sid: str) -> None:
    if st.session_state.get("_url_sid") == sid:
        return
    if _sid_from_url() == sid:
        st.session_state._url_sid = sid
        return
    st.session_state._url_sid = sid
    _replace_url_sid(sid)


def _clear_sid() -> None:
    st.session_state.pop("_url_sid", None)


def hydrate_auth() -> None:
    if st.session_state.get("access_token"):
        persist_auth(sync_url=False)
        return
    sid = _sid_from_url()
    if not sid:
        return
    blob = _auth_store().get(sid)
    if not isinstance(blob, dict) or not blob.get("access_token"):
        return
    expires_at = blob.get("_expires_at")
    if isinstance(expires_at, (int, float)) and time.time() >= expires_at:
        _auth_store().pop(sid, None)
        return
    # Safe to adopt: the lookup above proves this sid was minted here earlier.
    # That is the whole difference from the mint path, which must never take a
    # value the browser chose.
    st.session_state.auth_sid = sid
    for key in AUTH_KEYS:
        if key in blob:
            st.session_state[key] = blob[key]


def persist_auth(*, sync_url: bool = False) -> None:
    if not st.session_state.get("access_token"):
        return
    # Reuse the sid we already hold, otherwise mint one here. Deliberately NOT
    # _sid_from_url(): at login there is no sid in memory yet, so consulting the
    # URL let an attacker who sent the victim a ?s=<value> link decide the key
    # this login got filed under, then redeem the victim's tokens with the same
    # link (session fixation, CWE-384). A sid the browser presents may look up an
    # existing entry in hydrate_auth; it may never create one.
    sid = st.session_state.get("auth_sid") or secrets.token_urlsafe(32)
    st.session_state.auth_sid = sid
    blob = {key: st.session_state.get(key) for key in AUTH_KEYS}
    blob["_expires_at"] = time.time() + SID_IDLE_SECONDS
    _auth_store()[sid] = blob
    if sync_url:
        _write_sid(sid)


def restore_auth() -> None:
    hydrate_auth()


def clear_auth() -> None:
    sid = st.session_state.get("auth_sid")
    if sid:
        _auth_store().pop(sid, None)
    for key in (*AUTH_KEYS, "auth_sid", "_url_sid"):
        st.session_state.pop(key, None)
    _clear_sid()