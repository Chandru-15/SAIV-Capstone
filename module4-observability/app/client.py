from __future__ import annotations

import os
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import requests

SGT = timezone(timedelta(hours=8))

from config import (
    API_PREFIX,
    BACKEND_URL,
    CHECKIN_LATENCY_TARGET_S,
    CHECKIN_SLO_TARGET,
    GRAFANA_DASHBOARD_SLUG,
    GRAFANA_DASHBOARD_UID,
    GRAFANA_INTERNAL_URL,
    GRAFANA_PUBLIC_URL,
    PROMETHEUS_URL,
    REFRESH_SECONDS,
)

def _infer_face_url() -> str:
    override = os.getenv("FACE_SERVICE_URL")
    if override:
        return override.rstrip("/")
    host = urlparse(BACKEND_URL).hostname or ""
    if host in {"backend", "saiv-backend"}:
        return "http://face-recognition:8001"
    return "http://localhost:8001"

FACE_URL = _infer_face_url()

STAFF_ROLES = {"instructor", "ta", "admin"}
REPORT_COLUMNS = [
    "Student Name",
    "Student Email",
    "Course",
    "Session Date",
    "Check-in Time",
    "Status",
    "Risk Score",
]
CSV_COLUMNS = REPORT_COLUMNS + [
    "Check-in ID",
    "Student ID",
    "Session ID",
    "Verification Status",
    "Liveness Score",
    "Face Match Score",
]

_CHECKIN_LATENCIES: deque = deque(maxlen=400)
_CHECKIN_ERRORS: deque = deque(maxlen=400)

def _record_checkin_call(seconds: float, ok: bool) -> None:
    _CHECKIN_LATENCIES.append(max(0.0, float(seconds)))
    _CHECKIN_ERRORS.append(0 if ok else 1)

def checkin_slo(threshold: float = CHECKIN_LATENCY_TARGET_S, target: float = CHECKIN_SLO_TARGET) -> Dict[str, Any]:
    samples = list(_CHECKIN_LATENCIES)
    errors = list(_CHECKIN_ERRORS)
    n = len(samples)
    if n == 0:
        return {
            "n": 0,
            "p95": None,
            "pct_under_2s": None,
            "error_rate": None,
            "met": None,
            "threshold": threshold,
            "target": target,
        }
    ordered = sorted(samples)
    p95 = ordered[min(n - 1, max(0, int(round(0.95 * (n - 1)))))]
    pct = sum(1 for value in samples if value < threshold) / n
    err = (sum(errors) / len(errors)) if errors else 0.0
    return {
        "n": n,
        "p95": p95,
        "pct_under_2s": pct,
        "error_rate": err,
        "met": pct >= target,
        "threshold": threshold,
        "target": target,
    }

def now_sgt() -> datetime:
    return datetime.now(SGT)

def parse_dt(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(SGT)

def iso(dt: datetime) -> str:
    return dt.isoformat()

def pick(data: Optional[dict], *keys, default=None):
    if not isinstance(data, dict):
        return default
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default

def as_items(payload: Any) -> List[dict]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("items", "records", "students", "data", "results"):
            val = payload.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
    return []

def as_total(payload: Any, items: Optional[list] = None) -> int:
    if isinstance(payload, dict) and isinstance(payload.get("total"), int):
        return payload["total"]
    if items is not None:
        return len(items)
    return len(as_items(payload))

def error_text(response: Optional[requests.Response], fallback: str = "Request failed") -> str:
    if response is None:
        return fallback
    try:
        data = response.json()
    except ValueError:
        return (response.text or fallback)[:280]
    detail = data.get("message") or data.get("detail") or data.get("code")
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, dict):
                parts.append(str(item.get("msg") or item))
            else:
                parts.append(str(item))
        return "; ".join(parts) or fallback
    if isinstance(detail, dict):
        return str(detail.get("msg") or detail)
    return str(detail or fallback)

def _probe(url: str, timeout: float = 1.2) -> bool:
    try:
        response = requests.get(url, timeout=timeout)
        return response.status_code < 500
    except requests.RequestException:
        return False

_OPENAPI_CACHE: Dict[str, Any] = {"at": 0.0, "paths": None}
_OPENAPI_TTL = 60.0


def _openapi_paths() -> Optional[Dict[str, Any]]:
    now = time.monotonic()
    if _OPENAPI_CACHE["paths"] is not None and now - _OPENAPI_CACHE["at"] < _OPENAPI_TTL:
        return _OPENAPI_CACHE["paths"]
    try:
        response = requests.get(f"{BACKEND_URL}/openapi.json", timeout=2)
        paths = response.json().get("paths") if response.status_code == 200 else None
    except (requests.RequestException, ValueError):
        paths = None
    if isinstance(paths, dict):
        _OPENAPI_CACHE.update(at=now, paths=paths)
        return paths
    return None


def backend_supports(method: str, path: str) -> Optional[bool]:
    paths = _openapi_paths()
    if paths is None:
        return None
    wanted = method.lower()
    for candidate in (f"{API_PREFIX}{path}", f"{API_PREFIX}{path.rstrip('/')}/", path):
        entry = paths.get(candidate)
        if isinstance(entry, dict) and wanted in entry:
            return True
    return False


class SaivClient:
    def __init__(
        self,
        base_url: str = BACKEND_URL,
        token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        api_prefix: Optional[str] = None,
        role: Optional[str] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.refresh_token = refresh_token
        self.api_prefix = API_PREFIX
        self.role = str(role or "").split(".")[-1].strip().lower()
        self.last_error: Optional[str] = None
        self.last_status: Optional[int] = None
        self.last_retry_after: Optional[int] = None

    def _is_admin(self) -> bool:
        jwt_role = ""
        try:
            from access import token_role
            jwt_role = token_role(self.token)
        except Exception:
            jwt_role = ""
        if jwt_role:
            return jwt_role == "admin"
        return self.role == "admin"

    def _deny(self, message: str = "Admin role required") -> None:
        self.last_error = message
        self.last_status = 403

    def _headers(self) -> dict:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _call(
        self,
        method: str,
        paths: Iterable[str],
        *,
        ok: Tuple[int, ...] = (200, 201, 204),
        retry_refresh: bool = True,
        **kwargs,
    ) -> Optional[requests.Response]:
        self.last_error = None
        self.last_status = None
        self.last_retry_after = None
        timeout = kwargs.pop("timeout", 12)

        for path in paths:
            if not path.startswith("/"):
                path = "/" + path
            
            url = f"{self.base_url}{self.api_prefix}{path}"
            checkin_call = "checkin" in path.lower()
            started = time.perf_counter()
            
            try:
                response = requests.request(
                    method,
                    url,
                    headers=self._headers(),
                    timeout=timeout,
                    **kwargs,
                )
            except requests.RequestException as exc:
                self.last_error = str(exc)
                if checkin_call:
                    _record_checkin_call(time.perf_counter() - started, False)
                continue

            elapsed = time.perf_counter() - started
            self.last_status = response.status_code
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    self.last_retry_after = int(float(retry_after))
                except (TypeError, ValueError):
                    self.last_retry_after = None

            if response.status_code in ok:
                if checkin_call:
                    _record_checkin_call(elapsed, True)
                return response

            if response.status_code == 401 and retry_refresh and self.refresh_token:
                if self.refresh():
                    return self._call(
                        method, [path], ok=ok, retry_refresh=False, timeout=timeout, **kwargs
                    )

            if checkin_call:
                _record_checkin_call(elapsed, False)
            self.last_error = error_text(response)
            return response

        if self.last_error is None:
            self.last_error = "Backend unreachable"
        return None

    def _json(self, response: Optional[requests.Response]) -> Any:
        if response is None or response.status_code >= 400:
            return None
        if response.status_code == 204 or not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return None

    def login(self, email: str, password: str) -> Optional[dict]:
        response = self._call(
            "POST",
            ["/auth/login"],
            json={"email": email, "password": password},
            ok=(200,),
            retry_refresh=False,
        )
        data = self._json(response)
        if not isinstance(data, dict) or "access_token" not in data:
            if not self.last_error:
                self.last_error = "Login failed"
            return None
        self.token = data["access_token"]
        self.refresh_token = data.get("refresh_token")
        self._sync_session_tokens()
        user = data.get("user")
        if not isinstance(user, dict):
            user = self.me() or {}
            data["user"] = user
        return data

    def refresh(self) -> bool:
        if not self.refresh_token:
            return False
        response = self._call(
            "POST",
            ["/auth/refresh"],
            json={"refresh_token": self.refresh_token},
            ok=(200,),
            retry_refresh=False,
        )
        data = self._json(response)
        if isinstance(data, dict) and data.get("access_token"):
            self.token = data["access_token"]
            if data.get("refresh_token"):
                self.refresh_token = data["refresh_token"]
            self._sync_session_tokens()
            return True
        return False

    def _sync_session_tokens(self) -> None:
        try:
            import streamlit as st
            if self.token:
                st.session_state.access_token = self.token
            if self.refresh_token:
                st.session_state.refresh_token = self.refresh_token
            st.session_state.api_prefix = self.api_prefix
        except Exception:
            pass

    def me(self) -> Optional[dict]:
        data = self._json(self._call("GET", ["/auth/me", "/users/me"]))
        return data if isinstance(data, dict) else None

    def logout(self) -> None:
        self._call("POST", ["/auth/logout"], ok=(200, 204), retry_refresh=False)
        self.token = None
        self.refresh_token = None

    def list_courses(self, **params) -> Any:
        return self._json(self._call("GET", ["/courses/"], params=self._clean(params)))

    def list_sessions(self, **params) -> Any:
        return self._json(self._call("GET", ["/sessions/"], params=self._clean(params)))

    def my_sessions(self, **params) -> List[dict]:
        data = self._json(
            self._call("GET", ["/sessions/my-sessions"], params=self._clean(params))
        )
        if data is None or self.last_status in (404, 405):
            return []
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return as_items(data)

    def my_checkins(self, **params) -> List[dict]:
        data = self._json(
            self._call("GET", ["/checkins/me"], params=self._clean(params))
        )
        if data is None or self.last_status in (404, 405):
            return []
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return as_items(data)

    def get_session(self, session_id: str) -> Optional[dict]:
        data = self._json(self._call("GET", [f"/sessions/{session_id}"]))
        return data if isinstance(data, dict) else None

    def create_session(self, payload: dict) -> Tuple[bool, Any]:
        response = self._call("POST", ["/sessions/"], json=payload, ok=(201, 200))
        data = self._json(response)
        if response is not None and response.status_code in (200, 201):
            return True, data
        return False, self.last_error or "Could not create session"

    def update_session(self, session_id: str, payload: dict) -> Tuple[bool, Any]:
        response = self._call("PATCH", [f"/sessions/{session_id}"], json=payload)
        data = self._json(response)
        if response is not None and response.status_code == 200:
            return True, data
        return False, self.last_error or "Could not update session"

    def set_session_status(self, session_id: str, status: str) -> Tuple[bool, Any]:
        response = self._call(
            "PATCH",
            [f"/admin/sessions/{session_id}/status"],
            json={"status": status},
        )
        data = self._json(response)
        if response is not None and response.status_code == 200:
            return True, data
        return False, self.last_error or "Could not change session status"

    def session_statistics(self, session_id: str) -> Optional[dict]:
        data = self._json(self._call("GET", [f"/stats/sessions/{session_id}"]))
        if not isinstance(data, dict):
            data = self._json(self._call("GET", [f"/sessions/{session_id}/statistics"]))
        return data if isinstance(data, dict) else None

    def export_session(self, session_id: str) -> Optional[dict]:
        data = self._json(
            self._call("GET", [f"/export/session/{session_id}"], params={"format": "json"}))
        return data if isinstance(data, dict) else None

    def session_students(self, session_id: str) -> Optional[List[dict]]:
        data = self._json(self._call("GET", [f"/sessions/{session_id}/students"]))
        return data if isinstance(data, list) else None

    def course_students(self, course_id: str) -> Optional[dict]:
        data = self._json(self._call("GET", [f"/enrollments/course/{course_id}"]))
        return data if isinstance(data, dict) else None

    def course_enrolled_count(self, course_id: str) -> Optional[int]:
        data = self._json(self._call("GET", [f"/enrollments/course/{course_id}"]))
        if isinstance(data, dict) and isinstance(data.get("total_enrolled"), int):
            return data["total_enrolled"]
        return None

    def list_checkins(self, **params) -> Any:
        return self._json(self._call("GET", ["/checkins/"], params=self._clean(params)))

    def session_checkins(self, session_id: str, **params) -> List[dict]:
        data = self._json(
            self._call("GET", [f"/checkins/session/{session_id}"], params=self._clean(params))
        )
        if data is not None and self.last_status not in (404, 405):
            return as_items(data)
        return as_items(self.list_checkins(session_id=session_id, limit=100, **params))

    def flagged_checkins(self, **params) -> Any:
        return self._json(
            self._call("GET", ["/checkins/review-queue"], params=self._clean(params))
        )

    def get_checkin(self, checkin_id: str) -> Optional[dict]:
        data = self._json(self._call("GET", [f"/checkins/{checkin_id}"]))
        return data if isinstance(data, dict) else None

    def review_checkin(
        self, checkin_id: str, approve: bool, notes: Optional[str] = None
    ) -> Tuple[bool, Any]:
        payload = {"approve": approve, "notes": notes}
        response = self._call("PATCH", [f"/checkins/{checkin_id}/review"], json=payload)
        if response is not None and response.status_code == 200:
            return True, self._json(response)
        return False, self.last_error or "Review failed"

    def appeal_checkin(self, checkin_id: str, reason: str) -> Tuple[bool, Any]:
        response = self._call(
            "POST",
            [f"/checkins/{checkin_id}/appeal"],
            json={"reason": reason},
        )
        data = self._json(response)
        if response is not None and response.status_code in (200, 201):
            return True, data
        return False, self.last_error or "Appeal failed"

    def face_reenroll_requests(self, status: Optional[str] = None, **params) -> Any:
        """The queue of face re-enrolment requests this account may act on.

        Scoped by the backend, not here: an instructor is served only students
        enrolled on courses they teach, and a TA gets an empty list.
        """
        if status:
            params["status"] = status
        return self._json(self._call("GET", ["/face-reenroll-requests"],
                                     params=self._clean(params)))

    def review_face_reenroll(self, request_id: str, approve: bool,
                             notes: Optional[str] = None) -> Tuple[bool, Any]:
        """Approve or deny one request. The decision is final; a second call
        on the same row is refused by the backend rather than flipping it."""
        payload = {"approve": approve, "notes": notes}
        response = self._call("PATCH", [f"/face-reenroll-requests/{request_id}"],
                              json=payload)
        if response is not None and response.status_code == 200:
            return True, self._json(response)
        return False, self.last_error or "Could not record that decision"

    def list_users(self, **params) -> Any:
        return self._json(self._call("GET", ["/users/"], params=self._clean(params)))

    def get_user(self, user_id: str) -> Optional[dict]:
        data = self._json(self._call("GET", [f"/users/{user_id}"]))
        return data if isinstance(data, dict) else None

    def get_users(self, user_ids: Iterable[str]) -> Dict[str, dict]:
        wanted = [uid for uid in dict.fromkeys(str(u) for u in user_ids if u)][:200]
        if not wanted:
            return {}
        found: Dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            for uid, user in zip(wanted, pool.map(self._get_user_quiet, wanted)):
                if user:
                    found[uid] = user
        return found

    def _get_user_quiet(self, user_id: str) -> Optional[dict]:
        twin = SaivClient(self.base_url, token=self.token, role=self.role)
        return twin.get_user(user_id)

    def course_roster(self, course_id: str, **params) -> Any:
        return self._json(
            self._call("GET", [f"/courses/{course_id}/roster"], params=self._clean(params))
        )

    def assign_instructor(self, course_id: str, instructor_id: str) -> Tuple[bool, Any]:
        response = self._call(
            "POST",
            ["/admin/assignments/instructor"],
            json={"course_id": course_id, "instructor_id": instructor_id}
        )
        if response is not None and response.status_code in (200, 201):
            return True, self._json(response)
        return False, self.last_error or "Could not assign instructor"

    def list_instructors(self, course_id: str) -> List[dict]:
        data = self._json(self._call("GET", [f"/admin/assignments/instructor/{course_id}"]))
        return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []

    def unassign_instructor(self, course_id: str, instructor_id: str) -> Tuple[bool, Any]:
        response = self._call(
            "DELETE", [f"/admin/assignments/instructor/{course_id}/{instructor_id}"],
            ok=(200, 204))
        if response is not None and response.status_code in (200, 204):
            return True, {}
        return False, self.last_error or "Could not remove instructor"

    def set_course_structure(self, course_id: str, has_tutorials: bool, has_labs: bool) -> Tuple[bool, Any]:
        response = self._call("PUT", [f"/admin/courses/{course_id}/structure"],
                              json={"has_tutorials": has_tutorials, "has_labs": has_labs})
        if response is not None and response.status_code == 200:
            return True, self._json(response)
        return False, self.last_error or "Could not update the course structure"

    def course_groups(self, course_id: str) -> Optional[dict]:
        data = self._json(self._call("GET", [f"/admin/courses/{course_id}/groups"]))
        return data if isinstance(data, dict) else None

    def allocate_groups(self, course_id: str, kind: str, *, group_count: Optional[int] = None,
                        max_per_group: Optional[int] = None, mode: str = "reshuffle") -> Tuple[bool, Any]:
        payload = {"kind": kind, "mode": mode}
        if group_count:
            payload["group_count"] = int(group_count)
        if max_per_group:
            payload["max_per_group"] = int(max_per_group)
        response = self._call("POST", [f"/admin/courses/{course_id}/groups/allocate"], json=payload)
        if response is not None and response.status_code == 200:
            return True, self._json(response)
        return False, self.last_error or "Allocation failed"

    def move_group_member(self, course_id: str, student_id: str, group_id: str) -> Tuple[bool, Any]:
        response = self._call("POST", [f"/admin/courses/{course_id}/groups/move"],
                              json={"student_id": student_id, "group_id": group_id})
        if response is not None and response.status_code == 200:
            return True, self._json(response)
        return False, self.last_error or "Could not move the student"

    def forgot_password(self, email: str) -> Tuple[bool, str]:
        response = self._call("POST", ["/auth/forgot-password"], json={"email": email},
                              retry_refresh=False)
        data = self._json(response)
        if response is not None and response.status_code == 200:
            message = data.get("message") if isinstance(data, dict) else None
            return True, message or "Your request has been sent to an administrator."
        if self.last_status in (404, 405):
            return False, "Password reset requests aren't available on this server."
        return False, self.last_error or "Could not send the request. Please try again."

    def confirm_password_reset(self, email: str, code: str, new_password: str) -> Tuple[bool, str]:
        response = self._call("POST", ["/auth/reset-password"],
                              json={"email": email, "code": code, "new_password": new_password},
                              retry_refresh=False)
        data = self._json(response)
        if response is not None and response.status_code == 200:
            message = data.get("message") if isinstance(data, dict) else None
            return True, message or "Your password has been changed. You can now log in."
        if self.last_status in (404, 405):
            return False, "Self-service password reset isn't available on this server."
        return False, self.last_error or "That code is invalid or has expired."

    def password_reset_requests(self) -> List[dict]:
        data = self._json(self._call("GET", ["/admin/password-reset-requests"]))
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        return []

    def email_enabled(self) -> Optional[bool]:
        data = self._json(self._call("GET", ["/admin/email-status"]))
        if isinstance(data, dict) and "enabled" in data:
            return bool(data["enabled"])
        return None

    def send_reset_email(self, user_id: str) -> Tuple[bool, str]:
        response = self._call("POST", [f"/admin/users/{user_id}/send-reset-email"])
        data = self._json(response)
        if response is not None and response.status_code == 200 and isinstance(data, dict):
            return True, data.get("message") or "A reset code has been emailed."
        if self.last_status in (404, 405):
            return False, "This server can't email reset codes yet."
        return False, self.last_error or "Could not send the reset email."

    def reset_password(self, user_id: str) -> Tuple[bool, Any]:
        response = self._call("POST", [f"/admin/users/{user_id}/reset-password"])
        data = self._json(response)
        if response is not None and response.status_code == 200 and isinstance(data, dict):
            return True, data.get("temporary_password")
        return False, self.last_error or "Password reset failed"

    def staff_directory(self, role: str) -> List[dict]:
        data = self._json(self._call("GET", [f"/admin/assignments/directory/{role}"]))
        return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []

    def assign_ta(self, session_id: str, ta_id: str) -> Tuple[bool, Any]:
        response = self._call(
            "POST",
            ["/admin/assignments/ta"],
            json={"session_id": session_id, "ta_id": ta_id}
        )
        if response is not None and response.status_code in (200, 201):
            return True, self._json(response)
        return False, self.last_error or "Could not assign TA"

    def list_tas(self, session_id: str) -> List[dict]:
        data = self._json(self._call("GET", [f"/admin/assignments/ta/{session_id}"]))
        return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []

    def unassign_ta(self, session_id: str, ta_id: str) -> Tuple[bool, Any]:
        response = self._call(
            "DELETE", [f"/admin/assignments/ta/{session_id}/{ta_id}"], ok=(200, 204))
        if response is not None and response.status_code in (200, 204):
            return True, {}
        return False, self.last_error or "Could not remove TA"

    def audit_logs(self, **params) -> Any:
        if not self._is_admin():
            self._deny()
            return None
        return self._json(
            self._call("GET", ["/audit/logs"], params=self._clean(params))
        )

    def audit_summary(self, days: int = 7) -> Optional[dict]:
        if not self._is_admin():
            self._deny()
            return None
        data = self._json(self._call("GET", ["/audit/summary"], params={"days": days}))
        return data if isinstance(data, dict) else None

    def update_course(self, course_id: str, payload: dict) -> Tuple[bool, Any]:
        response = self._call("PATCH", [f"/courses/{course_id}"], json=payload, ok=(200,))
        data = self._json(response)
        if response is not None and response.status_code == 200:
            return True, data
        return False, self.last_error or "Could not update the course"

    def delete_course(self, course_id: str) -> Tuple[bool, Any]:
        response = self._call("DELETE", [f"/courses/{course_id}"], ok=(204, 200))
        if response is not None and response.status_code in (200, 204):
            return True, {}
        return False, self.last_error or "Could not delete the course"

    def create_course(self, payload: dict) -> Tuple[bool, Any]:
        response = self._call("POST", ["/courses/"], json=payload, ok=(200, 201))
        data = self._json(response)
        if response is not None and response.status_code in (200, 201):
            return True, data
        return False, self.last_error or "Could not create course"

    def bulk_create_users(self, users: list, default_password: str) -> Tuple[bool, Any]:
        payload = {"users": users, "default_password": default_password}
        response = self._call("POST", ["/admin/users/bulk"], json=payload, ok=(200, 201))
        data = self._json(response)
        if response is not None and response.status_code in (200, 201):
            return True, data
        return False, self.last_error or "Bulk user creation failed"

    def bulk_enroll(self, course_id: str, emails: list) -> Tuple[bool, Any]:
        payload = {"course_id": course_id, "emails": emails}
        response = self._call("POST", ["/admin/enrollments/bulk"], json=payload, ok=(200, 201))
        data = self._json(response)
        if response is not None and response.status_code in (200, 201):
            return True, data
        return False, self.last_error or "Bulk enrollment failed"

    def deactivate_user(self, user_id: str) -> Tuple[bool, Any]:
        response = self._call("PATCH", [f"/admin/users/{user_id}/deactivate"])
        if response is not None and response.status_code == 200:
            return True, self._json(response)
        return False, self.last_error or "Deactivation failed"

    def activate_user(self, user_id: str) -> Tuple[bool, Any]:
        response = self._call("PATCH", [f"/admin/users/{user_id}/activate"])
        if response is not None and response.status_code == 200:
            return True, self._json(response)
        return False, self.last_error or "Activation failed"

    def schedule_user_deletion(self, user_id: str) -> Tuple[bool, Any]:
        response = self._call("DELETE", [f"/users/{user_id}"], ok=(204, 200))
        if response is not None and response.status_code in (200, 204):
            return True, {}
        return False, self.last_error or "Soft delete failed"

    def gather_all(self, fetcher, *, page_size: int = 100, cap: int = 500) -> List[dict]:
        collected: List[dict] = []
        offset = 0
        while offset < cap:
            payload = fetcher(limit=page_size, offset=offset)
            items = as_items(payload)
            collected.extend(items)
            total = as_total(payload, items)
            if not items or len(collected) >= total or len(items) < page_size:
                break
            offset += page_size
        return collected[:cap]

    MAX_PAGE_SIZE = 100

    @staticmethod
    def _clean(params: dict) -> dict:
        cleaned = {k: v for k, v in params.items() if v is not None and v != ""}
        if "limit" in cleaned:
            try:
                cleaned["limit"] = max(1, min(int(cleaned["limit"]), SaivClient.MAX_PAGE_SIZE))
            except (TypeError, ValueError):
                cleaned.pop("limit")
        return cleaned

def backend_health() -> bool:
    return _probe(f"{BACKEND_URL}/health")

def face_health() -> bool:
    return _probe(f"{FACE_URL}/health")

def prometheus_health() -> bool:
    return _probe(f"{PROMETHEUS_URL}/-/ready") or _probe(f"{PROMETHEUS_URL}/api/v1/status/config")

def grafana_health() -> bool:
    return _probe(f"{GRAFANA_INTERNAL_URL}/api/health") or _probe(f"{GRAFANA_PUBLIC_URL}/api/health")

def grafana_dashboard_url() -> str:
    return f"{GRAFANA_PUBLIC_URL}/d/{GRAFANA_DASHBOARD_UID}/{GRAFANA_DASHBOARD_SLUG}"

def grafana_panel_url(panel_id: int, hours: int = 1) -> str:
    return (
        f"{GRAFANA_PUBLIC_URL}/d-solo/{GRAFANA_DASHBOARD_UID}/{GRAFANA_DASHBOARD_SLUG}"
        f"?orgId=1&from=now-{hours}h&to=now&panelId={panel_id}"
        f"&theme=light&refresh={REFRESH_SECONDS}s"
    )

_GRAFANA_PANELS: Dict[str, Any] = {"at": 0.0, "panels": None}


def grafana_panels() -> List[Tuple[int, str]]:
    from config import GRAFANA_PANEL_IDS

    if GRAFANA_PANEL_IDS:
        return [(pid, f"Panel {pid}") for pid in GRAFANA_PANEL_IDS]
    now = time.monotonic()
    if _GRAFANA_PANELS["panels"] is not None and now - _GRAFANA_PANELS["at"] < 300:
        return _GRAFANA_PANELS["panels"]
    found: List[Tuple[int, str]] = []
    for base in (GRAFANA_INTERNAL_URL, GRAFANA_PUBLIC_URL):
        try:
            response = requests.get(f"{base}/api/dashboards/uid/{GRAFANA_DASHBOARD_UID}", timeout=2)
            if response.status_code != 200:
                continue
            stack = list((response.json().get("dashboard") or {}).get("panels") or [])
        except (requests.RequestException, ValueError):
            continue
        while stack:
            panel = stack.pop(0)
            if panel.get("type") == "row":
                stack[0:0] = panel.get("panels") or []
                continue
            if isinstance(panel.get("id"), int):
                found.append((panel["id"], str(panel.get("title") or f"Panel {panel['id']}")))
        break
    _GRAFANA_PANELS.update(at=now, panels=found)
    return found


def prometheus_kpis() -> Dict[str, Optional[float]]:
    return {
        "checkins_total": query_prometheus("sum(saiv_checkins)"),
        "checkins_today": query_prometheus("saiv_checkins_today"),
        "flagged_pending": query_prometheus("saiv_flagged_pending"),
        "avg_risk": query_prometheus("saiv_avg_risk"),
        "backend_up": query_prometheus("saiv_backend_up"),
    }


def backend_exports_metrics() -> bool:
    return query_prometheus(
        'count({__name__=~"saiv_.+|http_requests_total|http_request_duration_seconds_count"})'
    ) is not None

def query_prometheus(query: str) -> Optional[float]:
    try:
        response = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": query},
            timeout=3,
        )
        if response.status_code != 200:
            return None
        payload = response.json()
        results = payload.get("data", {}).get("result", [])
        if not results:
            return None
        return float(results[0]["value"][1])
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        return None

def database_health() -> bool:
    try:
        response = requests.get(f"{BACKEND_URL}{API_PREFIX}/sessions/active", timeout=1.5)
        return response.status_code == 200
    except requests.RequestException:
        return False

def probe_all_health() -> Dict[str, bool]:
    checks = {
        "API": backend_health,
        "Face": face_health,
        "Prometheus": prometheus_health,
        "Grafana": grafana_health,
        "DB": database_health,
    }
    results: Dict[str, bool] = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futs = {pool.submit(fn): name for name, fn in checks.items()}
        for fut, name in futs.items():
            try:
                results[name] = bool(fut.result())
            except Exception:
                results[name] = False
    return results