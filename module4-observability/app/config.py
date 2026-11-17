from __future__ import annotations

import os


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
API_PREFIX = os.getenv("API_PREFIX", "/api/v1")
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090").rstrip("/")
GRAFANA_PUBLIC_URL = os.getenv("GRAFANA_PUBLIC_URL", "http://localhost:3001").rstrip("/")
GRAFANA_INTERNAL_URL = os.getenv("GRAFANA_INTERNAL_URL", "http://grafana:3000").rstrip("/")
GRAFANA_DASHBOARD_UID = os.getenv("GRAFANA_DASHBOARD_UID", "saiv-ops")
GRAFANA_DASHBOARD_SLUG = os.getenv("GRAFANA_DASHBOARD_SLUG", "saiv-operations")
GRAFANA_PANEL_IDS = [int(x) for x in os.getenv("GRAFANA_PANEL_IDS", "").replace(" ", "").split(",") if x.isdigit()]

REFRESH_SECONDS = _int("SAIV_REFRESH_SECONDS", 30)

FLAG_ALERT_RATE = _float("SAIV_FLAG_ALERT_RATE", 0.30)
FLAG_ALERT_WINDOW_HOURS = _int("SAIV_FLAG_ALERT_WINDOW_HOURS", 24)
FLAG_ALERT_MIN_CHECKINS = _int("SAIV_FLAG_ALERT_MIN_CHECKINS", 10)
FAILED_LOGIN_ALERT = _int("SAIV_FAILED_LOGIN_ALERT", 5)

DEFAULT_RISK_THRESHOLD = _float("SAIV_DEFAULT_RISK_THRESHOLD", 0.5)
GOOD_ATTENDANCE = _float("SAIV_GOOD_ATTENDANCE", 0.80)
LOW_ATTENDANCE = _float("SAIV_LOW_ATTENDANCE", 0.50)
CHECKIN_LATENCY_TARGET_S = _float("SAIV_LATENCY_TARGET_S", 2.0)
CHECKIN_SLO_TARGET = _float("SAIV_SLO_TARGET", 0.95)
GRAFANA_PANEL_HOURS = _int("SAIV_GRAFANA_HOURS", 6)