# Phase 3A isolated development deployment

This deployment uses the existing application at baseline `df8f460` on
`feature/frontend-improvements`. It does not implement the Phase 3B interface.
Use **only** `compose.phase3a.yml` for this environment; do not merge it with the
root `docker-compose.yml` or start both stacks on the same ports.

## Windows PowerShell 5.1

Docker Desktop must be running with Linux containers. Run from the official repo:

```powershell
Set-Location C:\Users\user\Capstone-dev
git branch --show-current
git status --short
& .\tools\phase3a\saiv.ps1 init
& .\tools\phase3a\saiv.ps1 start
& .\tools\phase3a\saiv.ps1 status
```

`init` generates random database/JWT/Grafana credentials in ignored `.env.phase3a`
and prepares the ignored model directory. Re-running it preserves credentials.
Do not remove or regenerate that file while retaining the PostgreSQL volume:
Postgres initialization variables do not change an existing database password.
The helper guards the repository and designated branch before running commands.
It creates no branches, commits or worktrees.

Infrastructure and application base images are pinned by verified registry digest.
Python Dockerfiles install the original direct requirements with resolved
`requirements.lock.txt` constraints; Next.js retains `npm ci` and its existing
package lock. The lock files record the tested Linux/Python 3.11 environment, not a
native Windows Python installation. Review/rebuild/test them deliberately when
updating dependencies. Debian apt repositories are not snapshot-pinned, so this
is a repeatable application baseline rather than a bit-for-bit hermetic build.

Normal operations:

```powershell
# Safe shutdown: keeps containers and named volumes/data
& .\tools\phase3a\saiv.ps1 stop
# Start/rebuild current branch images; preserves database and monitoring volumes
& .\tools\phase3a\saiv.ps1 rebuild
# Build only, without starting
& .\tools\phase3a\saiv.ps1 build
# Logs (omit Service for the complete stack)
& .\tools\phase3a\saiv.ps1 logs -Service backend
& .\tools\phase3a\saiv.ps1 logs -Service face-recognition
```

If execution policy blocks a local helper, invoke the same file using
`powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\phase3a\saiv.ps1 status`
(replace `status` with the intended action). This does not change machine policy.
Do not use volume removal, prune, seed-reset scripts or shared-database fixtures.

## Topology and addresses

All services share only the Compose-created `saiv-phase3a_default` network.
There are no fixed global container names. Volumes are scoped to this project:
`saiv-phase3a_postgres_data`, `saiv-phase3a_prometheus_data`,
`saiv-phase3a_grafana_data`. Redis challenges are intentionally ephemeral.

| Service | Internal address | Published loopback address |
|---|---|---|
| Gateway | `gateway:8080` | http://localhost:8080 |
| Next.js | `frontend:3000` | http://localhost:3000 |
| API | `backend:8000` | http://localhost:8000/docs |
| Streamlit | `dashboard:8501/portal/` | http://localhost:8501/portal/ |
| PostgreSQL | `postgres:5432` | None |
| Redis | `redis:6379` | None |
| Face service | `face-recognition:8001` | None |
| Exporter | `metrics-exporter:9101` | None |
| Prometheus | `prometheus:9090` | http://localhost:9090 |
| Grafana | `grafana:3000` | http://localhost:3001 |
| OTel Collector | `otel-collector:4317/4318`, metrics `8889` | None |

Grafana requires login (`admin` and the local `SAIV_GRAFANA_PASSWORD` in
`.env.phase3a`). Do not share that file or publish it in screenshots.

Gateway `/api/v1/*` preserves method, body, path and bearer headers to the backend.
Other application paths, including `/api/config`, go to Next.js. Public API and
portal URLs are read at runtime. Direct frontend access still uses the configured
gateway for API requests. The gateway accepts the configured hostname and loopback hosts,
overwrites forwarded scheme according to local configuration, and does not trust
arbitrary incoming forwarded client-IP headers. A Dev Tunnel peer address is not
proof of the student's original IP; network risk accuracy requires Module 2 review.

### Streamlit forwarding is intentionally blocked

`/portal` and `/portal/*` return **503** at the student gateway. Local Streamlit
inspection works at port 8501, with CORS and XSRF protections enabled and base path
`portal`. The existing app writes `?s=...` containing a bearer-equivalent lookup
credential after login. It has not been modified or declared secure in Phase 3A.
Do not forward port 8501 or share authenticated Streamlit URLs.

Coordinate a minimal Module 4 patch: remove URL credential minting/redemption,
use session-state authentication with documented reload re-login behavior (or a
separately reviewed persistent-cookie design), and retire legacy lookup records.
Only then enable a prefix-preserving reverse proxy to `dashboard:8501`, including
`/portal/_stcore/stream` WebSocket upgrades, assets and exact configured origins.
Do not disable CORS/XSRF or use a token-bearing navigation URL. Portal WebSocket
acceptance testing remains blocked; the current student attendance/appeal views
should be reused rather than rebuilt.

## Database initialization

The one-shot `migrate` service applies the real `db/schema.sql` with
`ON_ERROR_STOP=1` after PostgreSQL becomes healthy. Backend startup calls its
existing `_ensure_assignment_tables()` for seven additional ORM-managed tables.
There are 15 application tables. No migration or schema architecture was invented.
The schema script is idempotent; it recreates its declared triggers/functions but
does not erase rows. A basic backend `/health` alone does not verify these tables.

## Model provisioning: enabled for isolated development

The merged service expects ArcFace MobileFaceNet `w600k_mbf.onnx`: aligned RGB
112x112 input, 512-dimensional normalized embedding and the existing 256-bit
SimHash projection. ONNX Runtime 1.20.1 is pinned with NumPy 1.26.4, MediaPipe
0.10.18 and OpenCV 4.10.0.84. CPU import compatibility was verified in Python 3.11.

The older v0.7 metadata lacks asset digests. After the user supplied the newer
official `model-zoo` release, its API-published SHA-256 was independently verified
against the downloaded `buffalo_sc.zip`. Following the user's explicit approval for
isolated development/testing, both hashes and graph/inference compatibility were
rechecked and the exact candidate was provisioned through the existing read-only
mount. Real fixture enrollment/matching passed. See `PHASE3A-FACE-READINESS.md` for
the current result and `PHASE3A-MODEL-REVIEW.md` for provenance. Final team/production
model approval and prior-template compatibility remain separate Module 3 decisions.
Upstream pretrained-model terms differ from its code license.

To reproduce this specifically authorized development setup from the verified
local candidate, run the guarded provisioner, then the existing verifier:

```powershell
& .\tools\phase3a\provision-model.ps1
& .\tools\phase3a\verify-model.ps1 -ExpectedSha256 '9cc6e4a75f0e2bf0b1aed94578f144d15175f357bdc05e815e5c4a02b319eb4f'
```

The script compares SHA-256 and loads ONNX inputs/outputs. Follow with finite-output
and actual enrollment/verification checks; successful session loading alone does
not validate recognition quality. Model artifacts are excluded from Git and the
face image build context. Never change model, preprocessing, hash seed/projection
or thresholds to make a test pass. Existing templates are compatible only with
the identical model and transformation; prior-template compatibility is unverified.
This fresh isolated database initially had no biometric templates.

The running model-load healthcheck is now **healthy**. A missing model would make
that check unhealthy even if HTTP `/health` and the legacy exporter still report
process reachability. No container restart was needed: the existing directory bind
mount immediately exposed the verified file. No database data was reset.

## Private VS Code HTTPS Dev Tunnel

The user has now reported successful private HTTPS phone connectivity, authentication,
dashboard/layout and setup-dialog tests at
`https://gxcs7n6m-8080.asse.devtunnels.ms`. No tunnel was created by the agent. Keep
that existing origin/private forwarding intact; the instructions below are for
reproducing or changing it deliberately. Do not introduce Tailscale or a public tunnel.

1. Start the local stack and verify http://localhost:8080/login.
2. In VS Code, open the **Ports** panel (Command Palette: **Ports: Focus on Ports
   View**), select **Forward a Port**, and enter **8080**.
3. Sign in to the account requested by VS Code. Keep **Port Visibility: Private**.
   Copy the HTTPS **Forwarded Address**. The gateway upstream is HTTP; do not
   configure VS Code to expect local HTTPS on port 8080. External access is HTTPS.
4. Configure exactly the copied origin (no `/login`, query or fragment):

```powershell
& .\tools\phase3a\saiv.ps1 origin -PublicOrigin 'https://<copied-host>.devtunnels.ms'
& .\tools\phase3a\saiv.ps1 start
```

5. Open that HTTPS address on the laptop and confirm `/api/config` returns API and
   portal URLs on the same HTTPS origin, with no browser-facing localhost values.
6. On iPhone Safari or Android Chrome, open the same address and sign in with the
   same GitHub account used for private VS Code forwarding. Then sign in to SAIV
   separately. Check for a valid certificate and no mixed content/CORS errors.
7. Verify an unauthenticated browser cannot access the private tunnel. Never switch
   visibility to Public just to avoid the tunnel login.

Forward **only 8080**. Do not forward database, Redis, face, API, dashboard or
monitoring ports separately. The private tunnel has not been created or tested by
the agent. Authorization cookies, camera/geolocation prompts and WebSocket behavior
must be verified in the actual phone browser. Keep one stable hostname while
testing: origin changes affect browser identifiers and authentication storage.

To return to local mode, run `origin -PublicOrigin 'http://localhost:8080'`, then
`start`. Stop forwarding in the Ports panel when mobile testing is finished.

## First physical attendance test

The authorized development model is now provisioned and real fixture matching passes.
Live camera enrollment, successful blink/head-turn capture and full attendance still
need manual confirmation; model readiness alone does not establish those outcomes.

1. Create a clearly labelled student account in the isolated stack; use your own
   consenting face, not another person's downloaded photograph. Use normal SAIV
   registration/login. No test script should mark that account face-enrolled.
2. In the HTTPS phone browser, review and save camera/biometric and location consent,
   then request browser permissions through the existing controls. Test denial and
   recovery as well as granting. Do not assume saved consent means permission exists.
3. Enroll through the existing camera flow; check the actual API result. Confirm no
   raw image storage/logging is introduced. Enrollment can still expose existing
   cross-module issues after model provisioning; do not bypass those validations.
4. Using the existing local staff portal, have an authorized test admin/instructor
   create a labelled course, enrollment and active session at the real test venue,
   with an appropriate window and the intended face/liveness/device requirements.
   Do not lower policy or risk thresholds. Keep staff credentials on the laptop.
5. Refresh the student's dashboard. Confirm course/session eligibility. Let the
   existing preflight register this browser; do not generate/copy identifiers by hand.
6. Obtain a fresh accurate location and complete the server-issued blink/head-turn
   challenge. Submit once. Inspect the recorded status and evidence-derived result;
   HTTP 201 alone is not attendance approval. Exercise both challenge types across
   fresh server-issued attempts rather than choosing a client-side challenge.
7. Confirm the same record in the instructor view. If flagged/rejected, investigate
   its real cause. Exercise permitted retry/appeal/review behavior without bypasses.
8. Test portrait/landscape, background/resume, network interruption, stale location,
   permission revocation, expired challenge, logout/account switching and camera
   shutdown. Record phone/OS/browser version and observed outcomes without tokens,
   biometric frames or precise coordinates in the report.

## Automated verification

Existing frontend checks (disposable container, no database fixtures):

```powershell
docker compose --env-file .env.phase3a -p saiv-phase3a -f compose.phase3a.yml run --rm --no-deps frontend sh -c 'npm test && npm run typecheck && npm run lint && npm run test:production'
Get-Content .\tools\phase3a\baseline-smoke.py -Raw | docker compose --env-file .env.phase3a -p saiv-phase3a -f compose.phase3a.yml exec -T backend python -
docker compose --env-file .env.phase3a -p saiv-phase3a -f compose.phase3a.yml run --rm --no-deps -v "${PWD}/tools/phase3a:/checks:ro" -v "${PWD}/db:/db:ro" backend python /checks/infra-smoke.py
```

The runtime smoke adds one labelled synthetic student/browser per successful run;
it deletes nothing. It is for local-origin baseline mode, not private-tunnel login
automation. The infrastructure probe reads schema/monitoring and creates/consumes
one expiring random Redis probe key. Do not restart Postgres during backend tests.

For backend tests, create `saiv_phase3a_tests` **once if absent** in the isolated
Postgres service, then use the guarded runner. It redirects both application and
fixture URLs to this test DB; existing fixtures truncate only that DB. Face and
challenge stubs remain test-only, so a passing suite does not prove real inference.

```powershell
# First use only; if it already exists, preserve it and omit this command.
docker compose --env-file .env.phase3a -p saiv-phase3a -f compose.phase3a.yml exec -T postgres psql -U saiv -d saiv -c 'CREATE DATABASE saiv_phase3a_tests'
docker compose --env-file .env.phase3a -p saiv-phase3a -f compose.phase3a.yml run --rm --no-deps -v "${PWD}/tools/phase3a:/checks:ro" -v "${PWD}/db:/db:ro" -v "${PWD}/module1-frontend:/module1-frontend:ro" -v "${PWD}/.local/phase3a:/results" backend python /checks/backend-tests.py
```

The public integration/performance suite is not automatically run against the
development database: it writes data and has different evidence requirements.
Phase 3A logs and local credentials are under ignored `.local/phase3a` / `.env.phase3a`.
See `PHASE3A-BASELINE-RESULTS.md` for actual outcomes and limitations.

## Primary references consulted

- VS Code private port forwarding: https://code.visualstudio.com/docs/debugtest/port-forwarding
- ONNX Runtime 1.20.1 distributions (including CPython 3.11 Linux): https://pypi.org/project/onnxruntime/1.20.1/
- InsightFace model/source licensing: https://github.com/deepinsight/insightface
- Official model pack metadata: https://api.github.com/repos/deepinsight/insightface/releases/tags/model-zoo
- Collector logging-to-debug exporter change: https://github.com/open-telemetry/opentelemetry-collector/issues/11337

Collector config uses `debug` rather than the removed `logging` exporter and a
separate Prometheus-export endpoint at 8889. This does not add application tracing.
