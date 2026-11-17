"""Prometheus metrics for the SAIV backend (Module 2).

``/metrics`` is a *pull* endpoint. It reports the current value of every
counter and histogram in this process; Prometheus fetches that page on a timer
(15s, per ``module4-observability/prometheus.yml``) and keeps the history. This
module stores no history of its own and pushes nothing anywhere.

Only two metric families are defined, on purpose. Prometheus stores one time
series per distinct label combination, so each new label value costs memory and
query time for as long as it is retained:

    saiv_http_requests_total{method,route,status}     -- how many, how they ended
    saiv_http_request_duration_seconds{method,route}  -- how long, in buckets

``route`` is always the route *template* (``/api/v1/sessions/{session_id}``),
never the request path. Labelling with the real path would mint a new time
series for every session id, and for every random URL a scanner tries, which is
the usual way a ``/metrics`` endpoint ends up taking down its own monitoring.
Requests that match no route are collapsed into a single ``unmatched`` bucket
for the same reason.

The names are deliberately NOT prefixed per module: the face service
(Module 3) exposes the same two families, and Prometheus already distinguishes
them by the ``job`` label it attaches when scraping. That is what lets one
dashboard query cover both services.
"""

from __future__ import annotations

import time

from fastapi import FastAPI, Request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

METRICS_PATH = "/metrics"

REQUESTS = Counter(
    "saiv_http_requests_total",
    "HTTP requests handled, by route template and response status.",
    ["method", "route", "status"],
)

LATENCY = Histogram(
    "saiv_http_request_duration_seconds",
    "Wall-clock time to produce a response, by route template.",
    ["method", "route"],
    # The bucket edges have to straddle the latency target or no useful
    # quantile can be estimated near it: module4-observability/app/config.py
    # sets CHECKIN_LATENCY_TARGET_S = 2.0, hence an explicit 2.0 boundary.
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
)


def _observe(request: Request, status: int, elapsed: float) -> None:
    # scope["route"] is set by the router once a path matches, so it is only
    # readable after the request has been handled. Unmatched paths (404) leave
    # it absent.
    route = getattr(request.scope.get("route"), "path", None) or "unmatched"
    REQUESTS.labels(method=request.method, route=route, status=str(status)).inc()
    LATENCY.labels(method=request.method, route=route).observe(elapsed)


def install_metrics(app: FastAPI) -> None:
    """Expose GET /metrics and time every request.

    Kept out of main.py so the cardinality rule documented above sits next to
    the code that depends on it.
    """

    @app.middleware("http")
    async def record_request(request: Request, call_next):
        if request.url.path == METRICS_PATH:
            # Prometheus scraping us is not application traffic.
            return await call_next(request)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # The catch-all handler in main.py runs in Starlette's
            # ServerErrorMiddleware, which is OUTSIDE this middleware, so an
            # unhandled exception reaches us as an exception rather than as a
            # 500 response. Without this branch the requests that matter most
            # would be the only ones never counted.
            _observe(request, 500, time.perf_counter() - started)
            raise
        _observe(request, response.status_code, time.perf_counter() - started)
        return response

    @app.get(METRICS_PATH, include_in_schema=False)
    async def metrics() -> Response:
        # Unauthenticated by design: a scraper cannot log in. It therefore
        # exposes route names and timings only, never payloads or identifiers.
        # Set Content-Type as a header rather than via media_type: Starlette
        # 0.35 appends "; charset=utf-8" to every text/* media type, and
        # CONTENT_TYPE_LATEST already carries one, giving a duplicated
        # parameter.
        return Response(generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST})
