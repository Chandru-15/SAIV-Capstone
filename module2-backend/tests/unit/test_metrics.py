"""The /metrics contract, and the one mistake that makes it dangerous.

A Prometheus endpoint is cheap until a label value is unbounded: Prometheus
keeps one time series per distinct label combination, so labelling a request
with its real URL path mints a series per session id, and another for every URL
a scanner tries. These tests pin the route *template* as the label and a single
bucket for unmatched paths, because that property is invisible in review -- the
endpoint looks correct either way until the series count starts growing.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.unit

# Any parameterised route would do; a session id is the realistic case.
SESSION_ID = "0b5f6bd8-3d33-4a0f-9f0e-5d1a2c3e4f50"

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@pytest.fixture
def probe():
    """A client at the server root.

    The shared ``client`` fixture is based at /api/v1 and needs a database.
    /metrics and /health are deliberately unprefixed infrastructure probes, and
    neither touches one.
    """
    return TestClient(app, raise_server_exceptions=False)


def _series(body: str, name: str) -> list[str]:
    return [line for line in body.splitlines() if line.startswith(name + "{")]


def test_metrics_is_served_in_prometheus_text_format(probe):
    response = probe.get("/metrics")
    assert response.status_code == 200
    # Exactly one charset parameter. Passing CONTENT_TYPE_LATEST as media_type
    # rather than as a header makes Starlette 0.35 append a second one.
    assert response.headers["content-type"] == PROMETHEUS_CONTENT_TYPE
    assert "saiv_http_requests_total" in response.text


def test_a_path_parameter_never_becomes_a_label_value(probe):
    probe.get(f"/api/v1/sessions/{SESSION_ID}")
    body = probe.get("/metrics").text
    assert SESSION_ID not in body
    assert any(
        'route="/api/v1/sessions/{session_id}"' in line
        for line in _series(body, "saiv_http_requests_total")
    )


def test_unmatched_paths_all_share_one_series(probe):
    for suffix in ("alpha", "beta", "gamma"):
        probe.get(f"/no-such-route/{suffix}")
    body = probe.get("/metrics").text
    assert "no-such-route" not in body
    matching = [
        line for line in _series(body, "saiv_http_requests_total")
        if 'route="unmatched"' in line and 'status="404"' in line and 'method="GET"' in line
    ]
    assert len(matching) == 1, matching
    assert float(matching[0].rsplit(" ", 1)[1]) >= 3


def test_scraping_is_not_counted_as_application_traffic(probe):
    probe.get("/metrics")
    body = probe.get("/metrics").text
    assert not any(
        'route="/metrics"' in line
        for line in _series(body, "saiv_http_requests_total")
    )


def test_a_latency_bucket_boundary_sits_on_the_dashboard_target(probe):
    """CHECKIN_LATENCY_TARGET_S is 2.0 in module4-observability/app/config.py.

    A quantile near the target cannot be estimated unless a bucket boundary
    sits exactly on it, so this is part of the contract with module 4 rather
    than an arbitrary number.
    """
    probe.get("/health")
    body = probe.get("/metrics").text
    assert any(
        'le="2.0"' in line
        for line in _series(body, "saiv_http_request_duration_seconds_bucket")
    )
