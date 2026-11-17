# SAIV student frontend

Phase 3B adds the attendance-focused dashboard, protected `/profile` settings,
read-only browser registration and safely disabled student portal action. See
[Phase 3B implementation and verification](../docs/PHASE3B-FRONTEND.md) for the
complete file inventory, security dependencies and PowerShell review commands.

## Node.js runtime

Module 1 requires **Node.js 22.x** for development, automated tests, builds and
production. This overrides the repository README's generic Node.js 18+ prerequisite.
Validation uses Node.js 22.18.0; the frontend Docker image uses `node:22-alpine`.
The package and lockfile declare the same Node 22 engine range.

The liveness tests use Node's timer mocking API with `Date` support and an options
object, which is unavailable in Node 18. Check `node --version` before running the
validation commands below.

## Public service configuration

Set these values in the Next.js server environment (or `.env.local` in development):

- `NEXT_PUBLIC_API_URL`: browser-reachable backend origin or base, default `http://localhost:8000/api/v1`.
- `NEXT_PUBLIC_STAFF_PORTAL_URL`: browser-reachable Streamlit URL, default `http://localhost:8501`.

`GET /api/config` reads these **at runtime**, returns only these two public URLs,
and sends `Cache-Control: no-store`. It is a dynamic route, not a build-time asset.
The browser shares one configuration load per page lifetime; reload after changing
server configuration. Do not use direct `process.env.NEXT_PUBLIC_*` accesses in client
components: Next.js normally inlines those during build.

The API base accepts an origin, an existing `/api/v1` suffix, or repeated trailing
suffixes, and normalizes to one `/api/v1`. A deployment gateway prefix is retained.
Resource functions use paths such as `/auth/login`. URLs must be HTTP(S), without
credentials, query parameters or fragments. No tokens are passed to Streamlit.

The existing root Compose runtime `NEXT_PUBLIC_API_URL` setting is supported without
editing Compose. A custom staff URL can be supplied by the deployment owner. For
standalone frontend Docker builds, set the variables when starting the container;
no public URL build arguments are needed. `.dockerignore` excludes local dependencies,
build output and local environment files from the image build context.

`localhost` is for development on the same machine. Phones need reachable service
URLs, a secure browser context and backend CORS permission for their frontend origin.
Docker-only names such as `backend` are not normally browser-reachable.

## Access and entry routes

`/` redirects to `/dashboard`. Before student components mount, the dashboard requests
`GET /api/v1/auth/me` with the current bearer token. Only an active, validated student
mounts attendance/consent/enrollment components and their effects. TA, instructor and
admin profiles see a simple link to the existing staff portal and sign in there
separately. Unknown/inactive/malformed profiles fail closed; network failures offer
retry. A cached user object is never used to authorize access. Backend authorization
remains authoritative. Login/registration routes and all registration role choices
are preserved.

## Authentication recovery (Batch 2)

Authenticated 401 responses share one refresh operation per browser session identity.
The original request is retried at most once. Late 401s can use an already-rotated
token. Login, registration, refresh and logout never recursively refresh. General
403 responses keep their permission meaning; a 403 on profile validation fails
closed. Both returned tokens are saved; the backend currently issues a fresh pair
but does not implement a refresh-token revocation list.

Request identity is captured before asynchronous dispatch. Login/account replacement
and local logout advance an in-memory identity version; token rotation does not.
Old refresh/login/profile/consent responses cannot restore or overwrite a different
session. The protected view subscribes to identity changes, unmounting student state
and camera streams on logout. Browser back/forward cache restoration revalidates the
profile. Cancellation does not appear as a server error.

Logout captures the old bearer token, clears local authentication immediately, then
best-effort posts `/auth/logout` with that captured token (five-second timeout, no
refresh). Offline, expired-token and server failures do not undo local logout.
This endpoint audits logout; it does not revoke JWTs already issued by Module 2.

## Student eligibility and attendance (Batch 2)

The student dashboard combines array responses from `/sessions/active` and
`/courses/me`. Missing enrollment or face-policy information disables check-in.
Face matching uses `session.require_face_match OR course.require_face_recognition`;
the resulting shared policy drives enrollment prerequisites and image capture.
Sessions requiring liveness use the active flow below; face-only sessions retain
single-image verification. Browser registration is handled by the Batch 4 preflight below.

The backend uses inclusive `checkin_opens_at <= now <= checkin_closes_at`. Naive
timestamps are UTC, explicit offsets are respected, and display uses Singapore time.
The UI re-evaluates the attendance window while displaying cards, when starting an
attempt and before submission. Lecture scheduled start/end times do not restrict
eligibility. Device-clock estimates can be wrong; server window/enrollment checks
remain authoritative. Only reliably returned open sessions and their current
unavailable/already-recorded states are displayed, not an invented upcoming timetable.

History loads 50 records at a time and retains total/limit/offset semantics. Load More
merges by record ID. A changing offset-based dataset can overlap or shift; detected
count divergence requests a refresh rather than silently claiming completeness.
The backend provides no snapshot/cursor or session filter on `/checkins/me`.

Only session IDs in the student's own returned records are used for historical name
lookups: at most 50 distinct IDs per loaded page, three requests concurrently, with
an in-memory per-view cache. Restricted/unavailable metadata has a neutral fallback.
No attendance, tokens or images are added to PWA caches or persistent history storage.

Before opening an otherwise eligible check-in, incomplete history is scanned for an
existing record, using up to 20 additional pages of 100 records per attempt. Scanned
IDs are reused while the view remains mounted. If the scan cannot finish, the UI
requires history loading/refresh rather than treating partial data as a negative.
All recorded statuses prevent duplicate attempts; approval is never inferred from
HTTP 201. Final duplicate enforcement remains with the backend.

## Active liveness (Batch 3)

Prepare location and the front-camera preview, then scan/enter any instructor QR.
Request a challenge only when ready. `POST /checkins/liveness-challenge` takes
`session_id` as a query parameter and no client-selected challenge type. Its
response supplies `challenge_id`, `challenge_type` (`blink` or `head_turn`),
`instruction`, `min_frames`, `recommended_frames`, `max_frames` and `expires_at`.
All paths use the existing runtime `/api/v1` configuration and JWT recovery.

The modal displays server instructions and an expiry countdown. Capture starts
with one second to face forward with eyes open, since Module 2 uses frame 1 for
identity matching. It then samples 24 frames over about 3.5 seconds (roughly 153 ms
apart), giving a short blink several chances to appear and a head turn time to
develop. The target respects the server's minimum, recommendation and maximum;
smaller caps use longer intervals. Duplicate encoded frames cause a capture error,
never padding. No face detection, biometric score or liveness verdict runs here.

`POST /checkins/` receives `challenge_id` and ordered `challenge_frames` (bare
Base64 JPEG, no data-URL prefix), alongside the existing location, accuracy,
browser UUID and optional QR secret. No single-image/passive fallback is used for
a session requiring liveness, including sessions where face matching is optional.
Face-only verification retains `image`; the backend remains the final policy and
outcome authority. HTTP 201 can record approved, pending, flagged or rejected.

Expiry, cancellation, a session switch, camera loss or hiding the browser tab
discards the challenge and frames. Cancellation/session switches/tab hiding stop
camera tracks. Submission stops the preview and consumes the frontend evidence
once even on an error; recovery requires a newly issued challenge. Frames remain
only in memory, canvases are cleared, and the unchanged service worker caches no
API responses or POSTs. No tokens, frames or QR secrets are put in URLs or logs.

The UI rejects location readings older than 60 seconds before requesting/capturing
a challenge or submitting. QR secrets collected 30 seconds ago are cleared and
must be rescanned/re-entered. These are frontend freshness checks, not new backend
security rules. A raw QR secret has no issue timestamp: it may already be near its
server expiry when scanned. Obtain a newly displayed QR and submit promptly.
Currently Module 2 records expired/mismatched QR evidence as
`qr_code_verified: false`; it does not reject it or include it in its earlier risk
decision. The UI reports that flag separately and preserves the returned status.

Timeouts, connection loss, unexpected success bodies and HTTP 5xx leave an
uncertain-submission marker. Only that marker (account/session IDs, no biometric
evidence) is retained in sessionStorage, with an in-memory fallback. Reopening the
modal cannot immediately repeat the POST. “Check attendance status” searches the
student's own paginated history (at most 20 pages of 100); an existing record shows
its actual outcome. Failed/incomplete reconciliation keeps retry blocked. A
completed search with no record permits an explicit fresh attempt with new
evidence, while warning that the server may still be processing. It never replays
the previous POST. History is not a transaction-status/idempotency API, so an
in-flight commit after a negative lookup cannot be excluded by Module 1; backend
duplicate enforcement remains essential.

Real-device validation still needed: iOS Safari/Android Chrome (HTTPS), permission
denial and camera interruption, blink timing/lighting and head-turn range against
Module 3, slow networks and large frame payloads, QR issuance timing and actual
approved/flagged/rejected results. Check challenge storage uses Redis with multiple
backend workers; the backend's process-local fallback cannot share issued IDs.
Countdowns depend on the device clock because the current contract has no server
clock field. Batch 3 added no backend, risk-scoring, QR enforcement or device
registration changes; Batch 4 adds the frontend registration flow below.
For a server-wide active-challenge guarantee, Module 2 must also stop accepting its
legacy single-image/client-score alternatives when active liveness is required.
This frontend always uses challenges for that policy, but cannot enforce other
clients' requests. Such backend hardening is outside this batch.

## Browser device registration (Batch 4)

The official briefing (pages 19 and 26) requires device identification and student
device registration. The PWA uses an installation UUID, not a physical hardware
fingerprint or attestation. The old `saiv_device_fingerprint_v1` localStorage key
is preserved. A valid existing UUID is reused exactly; new UUID v4 values use
`crypto.randomUUID()` or `crypto.getRandomValues()`. No IP, user-agent, font,
canvas, hardware enumeration, public key, signature or attestation is collected
for registration. Canvas remains limited to the existing camera/QR workflows.

Opening an eligible student check-in starts a registration gate before camera,
location, QR or liveness controls mount. This lazy preflight avoids registration
requests during ordinary dashboard renders. Module 2 uses unknown devices as a
risk signal even when course binding is optional, so the frontend requires a
confirmed active registration for all new check-in attempts. This is a frontend
workflow prerequisite, not a claim that the backend hard-rejects every unknown
device or that registration guarantees approval.

Actual authenticated contracts (under the existing runtime `/api/v1` base):

- `GET /devices/my-devices` returns a **bare array**, including inactive devices.
- `POST /devices/` accepts `device_fingerprint` (8–64 characters), optional
  `device_name`, `platform`, `browser`, `os_version`, `app_version`, `public_key`.
  This client sends only `{ device_fingerprint: <UUID>, platform: "web" }`.
- Both return device objects with `id`, `user_id`, `device_fingerprint`, optional
  metadata above except the public key, `is_trusted`, `trust_score`, `is_active`,
  `total_checkins`, `first_seen_at`, `last_seen_at`. There is no separate revoked
  status/reason or attestation result in this response. POST returns HTTP 201,
  including re-announcements of an existing owned registration.

The API specification's `/devices/register` path is outdated; source controllers
define `/devices/`. Fingerprints are globally unique. A different account using
the same installation UUID gets HTTP 409; the frontend shows that restriction and
never rotates the UUID to evade it. A new browser registration is untrusted and
does not establish a signing key or hardware attestation.

Every preflight first lists owned devices. Active matching registrations are
reused without POST. Missing or malformed status/ownership data fails closed.
`is_active: false` is treated as disabled/revoked and never re-registered. Refusal
is retained for the current login; a failed lookup cannot erase it. Module 2's
current POST **reactivates inactive owned devices**, so it must not be used as a
generic retry. A fresh read-only status check also runs immediately before each
attendance POST, using exactly the identifier confirmed by the gate. Failure
unmounts the capture workflow and discards its evidence.

Concurrent calls in one page share an in-flight registration operation. A compact
account/identifier-specific sessionStorage marker records only that registration
was observed or attempted. It confers no ready status, but prevents a timeout,
malformed response or modal reopen from causing a repeated POST. Once a request
has been attempted, “Check registration again” performs status lookup only; it
can recover if the original request committed. A complete negative lookup still
does not authorize a replay. An initial lookup failure can be retried, followed
by a first POST only after a successful fresh lookup finds no registration.
HTTP 401 retains the existing coordinated refresh/retry, since authentication
runs before the backend registration handler; transport errors do not replay POST.

Logout/account replacement aborts operations and clears in-memory confirmations,
refusals and the account-specific markers. Late responses cannot update another
login. The installation UUID alone persists across logout. Closing the registration
dialog does not undo a registration already sent; reopening checks the server
instead of repeating it. Browser storage denial or failed write/readback blocks
registration with actionable guidance; ephemeral UUIDs are not silently used.
Clearing site data, private browsing or separate browser/PWA storage partitions
can change the identifier. UUIDs can also be copied or forged by a modified client.

Uncertain **attendance** recovery is kept separate: history reconciliation remains
available without device registration. Starting a new attempt after an empty
reconciliation goes through the registration gate before collecting new evidence.
Neither registration nor a retry replays a timed-out attendance POST.

Backend dependencies remain: reject unauthorized reactivation of inactive devices;
decide whether device validity needs hard enforcement rather than risk scoring;
handle global uniqueness atomically across concurrent tabs/clients. A read-then-
POST race or revocation after the final lookup cannot be made atomic in Module 1.
The list API cannot distinguish revoked from disabled or prove historical state
once `is_active` is changed. Current-tab request coordination is not a cross-tab
lock. Real-browser tests must cover persistence across reload/PWA restart, blocked
storage, shared-browser account conflict, and real backend revoke/timeout/refresh
flows. No hardware signing, attestation, device dashboard or backend changes are
included in this batch.

## Authentication storage cleanup

Authentication tokens remain tab-scoped in sessionStorage. Logout invalidates the
running application's identity before accessing storage, so delayed login,
refresh, profile and consent responses cannot restore that identity. Reads fail
closed when storage is inaccessible; token/profile writes must pass readback checks.

Cleanup attempts removal of each authentication entry, verifies it is absent or
empty, and falls back to writing an empty value if removal fails or leaves data
behind. Empty, whitespace and malformed placeholder credential values are treated
as absent; the backend still validates token authenticity and expiry. The browser
installation UUID and its registration workflow are unchanged.

If cleanup cannot be verified, the login page warns that the user should clear
the site's browser data before sharing the device and that reloading may restore
the previous sign-in. This warning is kept in memory so it can survive client-side
navigation without depending on the storage that just failed. It cannot survive a
full reload when no durable warning could be written.

Frontend cleanup is **best-effort**. If removal and overwrite are both blocked or
silently ignored, valid tokens can remain physically stored and become readable
after a full reload. The current Module 2 logout endpoint records an audit event;
it does not revoke access or refresh JWTs. Neither its success response nor the
frontend cleanup confirms server-side revocation. Reliable revocation requires a
separate Module 2 change; offline logout cannot guarantee server acknowledgement.
Reload tests recreate module state with mocked storage, not a real browser/server.

## Frontend validation

```sh
npm ci
npm test
npm run typecheck
npm run lint
npm run build
npm run test:production
```

Tests use Node's built-in runner, the existing TypeScript compiler for in-memory
transpilation, and the matching React 18 test renderer. They mount real components
with mocked navigation, browser storage and HTTP adapters; they never call backend
services or a database. Full type checking is separate from test transpilation.
Real mobile permission, installed-PWA and cross-service tests remain necessary.
The production smoke command starts and stops its own local frontend on an ephemeral
port. It proves runtime URL configuration, entry routing and preserved public pages
against the built app, without contacting the backend or staff portal.

### Temporary local Attendance & Appeals navigation

For isolated local development, set these in `.env.phase3a` (or the frontend's
runtime environment when running it directly):

```dotenv
SAIV_STUDENT_PORTAL_ENABLED=true
SAIV_STUDENT_PORTAL_LOCAL_ONLY=true
NEXT_PUBLIC_STUDENT_PORTAL_URL=http://localhost:8501/portal/
```

Compose forwards these settings only to the frontend. Recreate just that service
with `.\tools\phase3a\saiv.ps1 rebuild -Service frontend` from the repository root.
Reload the dashboard at `http://localhost:8080/dashboard` or `http://127.0.0.1:8080/dashboard`.
The link opens the existing Streamlit login in a new tab; sign in separately.
The local-only flag restricts both browser and destination hostnames to localhost
or 127.0.0.1. HTTPS Dev Tunnel, LAN and other non-local hosts keep the button disabled.
No authentication credentials are transferred. Port 8501 stays loopback-bound and
gateway `/portal/` remains blocked. Module 4's existing URL-session concern remains
unresolved; this setup does not enable public/HTTPS portal integration.

Set `SAIV_STUDENT_PORTAL_ENABLED=false` and recreate the frontend to disable it.
