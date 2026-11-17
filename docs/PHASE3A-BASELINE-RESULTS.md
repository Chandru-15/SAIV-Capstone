# Phase 3A baseline results

Follow-up status: the user has authorized the verified model for isolated testing;
it is now provisioned and real fixture recognition passes. The user also reports
successful mobile HTTPS/authentication/dashboard/layout/setup-dialog checks. See
`PHASE3A-FACE-READINESS.md` for current results. The original baseline below is retained
as a historical record; its missing-model/browser-only limitations have been superseded
where explicitly covered by those follow-ups. The Module 4 portal boundary remains.

Review date: 5 November 2026 (Singapore). Repository:
`C:\Users\user\Capstone-dev`. Branch: `feature/frontend-improvements`.
Baseline/HEAD: `df8f4608df0b854fe8c7b6940983a2610c32b471`.
The checkout was clean before work. Changes are uncommitted; no branch switch,
worktree, pull, merge, reset, commit or push was performed. `dev` was not modified.

## Outcome

The independently testable integrated deployment is running as `saiv-phase3a`.
It is **not yet a fully accepted attendance baseline**: the verified recognition
candidate awaits Module 3 approval and is not installed, and gateway Streamlit
forwarding remains blocked pending its URL-session security change.

## Final verification

| Check | Result / evidence |
|---|---|
| Application images | All five Compose application image targets built; immutable base-image digests and Python constraints used |
| Next.js production build | Passed; existing routes generated |
| Frontend existing tests | **310 passed**, 0 failed, 0 skipped |
| TypeScript / lint | Passed; no lint warnings/errors |
| Frontend production smoke | Passed: runtime config, no-store, route initial states and PWA manifest |
| Backend unit/flow tests | **605 passed**, 1 deprecation warning, no failures/skips; 395.74 seconds |
| Backend test isolation | Both app and fixture URLs target `saiv_phase3a_tests`; development DB was not truncated |
| Real gateway/API smoke | **34 HTTP checks passed** on final deployed backend/frontend; no face stubs used in this smoke |
| Database | 15 tables; all 37 explicitly declared base-schema indexes and 31 named constraints present |
| Data persistence | Development rows preserved across normal Postgres restart and final container recreation |
| Redis | Actual application Redis store selected; TTL and atomic single-use take behavior passed |
| Streamlit | HTML and base-path health respond 200 locally; gateway `/portal/` intentionally returns 503 |
| Gateway | Real API/frontend routing and configured/loopback host handling passed; unconfigured Host rejected with 400 |
| Prometheus | Exporter, self and Collector targets up; DB/backend/exporter-success gauges equal 1 |
| Grafana | Health responds; anonymous datasource API rejected; authenticated provisioned Prometheus datasource health OK |
| Collector | Started with supported config; synthetic OTLP HTTP trace accepted and logged as one span |
| Face runtime | Python 3.11 + ORT 1.20.1 + NumPy 1.26.4 + OpenCV 4.10.0 + MediaPipe 0.10.18 import successfully |
| Python dependency consistency | Final backend, face and dashboard `pip check` pass; Streamlit packaging conflict resolved with wheel 0.45.1 |
| Candidate model | Official archive digest matched; graph and synthetic tensor/hash checks passed in disposable inspection only |
| Running recognition readiness | **Blocked/failed**: `/app/models/w600k_mbf.onnx` absent; actual loader returns HTTPException 503; Docker health unhealthy |
| Camera/location/biometrics | Valid enrollment, face matching and real liveness capture not tested |
| Laptop browser | Chrome unauthenticated dashboard redirected to sign-in; user stopped Computer Use with Escape before authenticated browser testing |
| HTTPS tunnel / physical phone | Not created or tested; manual setup and iOS/Android checklist documented |

Backend flow tests use their existing face and challenge stubs. They verify backend
contracts and policy logic, not real recognition quality or Redis integration; the
separate infrastructure probe verifies the real Redis store. `/health`, including
the exporter's `saiv_face_up`, is process reachability rather than model readiness.
Synthetic candidate inference is not a biometric accuracy or performance benchmark.
Full public/performance/end-to-end attendance suites were not run against the dev DB.

The first backend run was invalidated by a Postgres restart during tests and a
missing read-only frontend-policy mount. Both harness conditions were corrected;
the clean complete rerun above passed. The first npm download failed transiently;
the retry and final builds succeeded. No application/security check was bypassed.

Development fixtures are additive and labelled synthetic: five students and four
browser registrations were present after smoke runs, with **zero enrolled faces,
courses, sessions or attendance records**. One unused synthetic browser-login
account has random credentials only in ignored local state. No personal biometric
input was supplied. Existing unrelated Docker containers/volumes/networks were not
removed. Docker test containers were disposable; project data volumes were retained.

## Modified/new tracked files and reasons

| File(s) | Reason |
|---|---|
| `.gitignore` | Exclude local deployment credentials/state and ONNX model artifacts |
| `compose.phase3a.yml` | Separate project/network/volumes, loopback-only publications, dependencies, real model-load probe and pinned infrastructure images |
| `deploy/phase3a/Caddyfile` | One gateway for Next/API; configured scheme/host handling; block unsafe portal forwarding |
| `deploy/phase3a/prometheus.yml` | Scrape the implemented exporter and Collector rather than nonexistent app metrics endpoints |
| `deploy/phase3a/otel.yml` | Supported debug exporter and separate Prometheus endpoint; no app instrumentation rewrite |
| `deploy/phase3a/phase3a.env.example` | Document public-origin and generated-credential settings without real secrets |
| `module1-frontend/Dockerfile` | Pin the verified Node base digest only; no frontend source/UI change |
| `module2-backend/Dockerfile` and `requirements.lock.txt` | Pin verified Python base and resolved dependencies; no backend behavior change |
| `module3-face-recognition/Dockerfile` | Pin base/dependencies; retain binary-wheel runtime libraries without unnecessary C++/OpenCV development packages |
| `module3-face-recognition/requirements.txt`, `requirements.lock.txt` | Declare missing ONNX Runtime and pin compatible CV/NumPy/resolved packages |
| `module3-face-recognition/.dockerignore` | Keep model artifacts, local environments and caches out of builds |
| `module4-observability/Dockerfile`, `requirements.lock.txt` | Pin base/resolved packages and compatible wheel build tool for Streamlit's packaging constraint |
| `tools/phase3a/saiv.ps1` | PowerShell 5.1 branch guard, secret initialization and safe project operations; no destructive cleanup |
| `tools/phase3a/backend-tests.py` | Guarded dedicated test-DB runner and local JUnit results |
| `tools/phase3a/baseline-smoke.py` | Additive real HTTP/auth/device/validation checks without personal images |
| `tools/phase3a/infra-smoke.py` | Read-only DB/monitoring inspection plus a transient random Redis probe |
| `tools/phase3a/verify-model.ps1` | Trusted-checksum/load/shape helper for future approved provisioning |
| `tools/phase3a/inspect-model-candidate.py` | Isolated candidate graph/tensor inspection, no application integration |
| `docs/PHASE3A-DEPLOYMENT.md` | Exact PowerShell operations, topology, tunnel setup and first-device checklist |
| `docs/PHASE3A-MODEL-REVIEW.md` | Archive/file hash distinction, provenance, compatibility evidence and pending approval |
| `docs/FRONTEND-ROADMAP.md` | Retain the user's Phase 3B requirements without implementing them |
| This report | Record verified outcomes, scope and limitations |

Ignored local artifacts include `.env.phase3a`, build/test logs, JUnit XML, package
inventories and the quarantined model archive/file. No credentials or model weights
are intended for Git. Original root `docker-compose.yml`, database schema, API
contracts, authentication/RBAC, consent, risk, retry, audit and liveness/face source
functions were left unchanged. The pixel-coordinate blink call remains present.

## Remaining coordination and risks

- **Module 3:** approve the exact verified candidate, confirm model/license and prior
  template compatibility, then authorize provisioning and real enrollment/matching/
  blink/head-turn checks. Keep all thresholds and the hash transformation unchanged.
- **Module 4:** remove bearer-equivalent `s` URL-session minting/redemption and agree
  reload behavior before enabling portal routing/WebSockets. Existing student views
  and RBAC are to be reused. This phase does not claim remote portal security.
- **Module 2:** validate Dev Tunnel/proxy network-signal interpretation during real
  attendance, plus any evidence/face contract failure discovered with real captures.
  Browser UUID registration still does not constitute cryptographic attestation.
- **Frontend dependencies:** npm reported 22 audit findings (11 moderate, 10 high,
  1 critical) in the preserved dependency baseline. No `audit fix`, framework upgrade
  or security-policy weakening was performed. Review before broader deployment.
- **Observability:** exporter/Prometheus/Grafana and Collector intake work; passing
  a synthetic trace does not establish application-level instrumentation. No app
  `/metrics` endpoint was added. A green process probe can conceal missing model
  readiness, so inspect the dedicated model health separately.
- **Reproducibility:** base/infrastructure digests and Python/npm resolutions are
  pinned; apt repositories are not snapshot-pinned. Native Windows/ARM builds and
  long-duration/performance behavior remain unverified.
- **Manual acceptance:** private tunnel authentication, valid phone HTTPS, camera/GPS,
  actual face enrollment, attendance status, retry/appeal/instructor review and portal
  WebSockets remain pending. Never infer approval from HTTP 201 alone.

No Phase 3B redesign, `/profile` route, browser information panel, portal button,
device mutation control, commit or push was implemented. Stop at this Phase 3A handoff.
