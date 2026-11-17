"""Prometheus exporter for SAIV attendance (Module 4).

The backend does not currently expose /metrics. This process polls health
endpoints and Postgres (read-only) and publishes gauges Prometheus can scrape.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone, timedelta

import requests
from prometheus_client import Gauge, start_http_server
from sqlalchemy import create_engine, text

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
FACE_URL = os.getenv("FACE_SERVICE_URL", "http://localhost:8001").rstrip("/")
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://saiv:saiv_password@localhost:5434/saiv",
)
PORT = int(os.getenv("EXPORTER_PORT", "9101"))
INTERVAL = int(os.getenv("EXPORTER_INTERVAL", "15"))
SGT = timezone(timedelta(hours=8))

BACKEND_UP = Gauge("saiv_backend_up", "1 if the Module 2 API health check succeeds")
FACE_UP = Gauge("saiv_face_up", "1 if the face-recognition health check succeeds")
DB_UP = Gauge("saiv_db_up", "1 if Postgres accepts a read")
BACKEND_LATENCY = Gauge("saiv_backend_latency_seconds", "Backend /health response time in seconds")
FACE_LATENCY = Gauge("saiv_face_latency_seconds", "Face service /health response time in seconds")
CHECKINS = Gauge("saiv_checkins", "Check-in rows by verification status", ["status"])
SESSIONS = Gauge("saiv_sessions", "Attendance sessions by status", ["status"])
FLAGGED = Gauge("saiv_flagged_pending", "Flagged or appealed check-ins waiting review")
CHECKINS_TODAY = Gauge("saiv_checkins_today", "Check-ins whose timestamp falls on the current SGT date")
AVG_RISK = Gauge("saiv_avg_risk", "Mean risk score across all check-ins")
LOGIN_FAIL = Gauge("saiv_login_failures_24h", "login_failed audit rows in the last 24 hours")
SCRAPE_OK = Gauge("saiv_exporter_scrape_ok", "1 if the last exporter cycle finished")


def _probe(url: str) -> bool:
    ok, _elapsed = _probe_timed(url)
    return ok


def _probe_timed(url: str) -> tuple[bool, float]:
    started = time.perf_counter()
    try:
        response = requests.get(url, timeout=3)
        ok = response.status_code < 500
    except requests.RequestException:
        ok = False
    return ok, time.perf_counter() - started


def _engine():
    return create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 3},
    )


def _counts(conn, sql: str) -> dict:
    rows = conn.execute(text(sql))
    return {str(status or "unknown"): int(count) for status, count in rows}


def collect() -> None:
    backend_ok, backend_ms = _probe_timed(f"{BACKEND_URL}/health")
    if not backend_ok:
        backend_ok, backend_ms = _probe_timed(f"{BACKEND_URL}/api/v1/health")
    BACKEND_UP.set(1 if backend_ok else 0)
    BACKEND_LATENCY.set(backend_ms)

    face_ok, face_ms = _probe_timed(f"{FACE_URL}/health")
    FACE_UP.set(1 if face_ok else 0)
    FACE_LATENCY.set(face_ms)

    engine = _engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            DB_UP.set(1)

            checkins = _counts(conn, "SELECT status, COUNT(*) FROM checkins GROUP BY status")
            for status in ("pending", "approved", "flagged", "rejected", "appealed"):
                CHECKINS.labels(status=status).set(checkins.get(status, 0))
            for status, count in checkins.items():
                if status not in {"pending", "approved", "flagged", "rejected", "appealed"}:
                    CHECKINS.labels(status=status).set(count)

            sessions = _counts(conn, "SELECT status, COUNT(*) FROM sessions GROUP BY status")
            for status in ("scheduled", "active", "closed", "cancelled"):
                SESSIONS.labels(status=status).set(sessions.get(status, 0))

            flagged = conn.execute(
                text("SELECT COUNT(*) FROM checkins WHERE status IN ('flagged', 'appealed')")
            ).scalar()
            FLAGGED.set(int(flagged or 0))

            start = datetime.now(SGT).replace(hour=0, minute=0, second=0, microsecond=0)
            start_utc = start.astimezone(timezone.utc).replace(tzinfo=None)
            end_utc = start_utc + timedelta(days=1)
            today_n = conn.execute(
                text(
                    "SELECT COUNT(*) FROM checkins "
                    "WHERE checked_in_at >= :start AND checked_in_at < :end"
                ),
                {"start": start_utc, "end": end_utc},
            ).scalar()
            CHECKINS_TODAY.set(int(today_n or 0))

            avg = conn.execute(text("SELECT AVG(risk_score) FROM checkins")).scalar()
            AVG_RISK.set(float(avg or 0))

            fails = conn.execute(
                text(
                    "SELECT COUNT(*) FROM audit_logs "
                    "WHERE action = 'login_failed' "
                    "AND timestamp > (NOW() AT TIME ZONE 'utc') - INTERVAL '24 hours'"
                )
            ).scalar()
            LOGIN_FAIL.set(int(fails or 0))
        SCRAPE_OK.set(1)
    except Exception:
        DB_UP.set(0)
        SCRAPE_OK.set(0)
    finally:
        engine.dispose()


def main() -> None:
    start_http_server(PORT)
    while True:
        collect()
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
