# Phase 3B — student frontend implementation and review

Repository: `C:\Users\user\Capstone-dev` · branch: `feature/frontend-improvements` · committed baseline: `df8f460`.

Existing uncommitted Phase 3A work is retained. No commit, push, branch switch, migration, database reset, volume deletion, biometric-model change or Module 2/3/4 application change is part of this phase.

## Implemented experience

The visual system uses deep navy, muted teal, neutral surfaces and distinct textual status badges. CSS tokens and shared panel, button, field, notice, empty-state and dialog styles live in `module1-frontend/app/globals.css`. Typography uses the system's Arial/Helvetica fonts without downloading fonts or adding dependencies. Headers wrap on phones; panels use shrinkable grid columns; buttons have 44-pixel minimum touch targets. Dialogs scroll within the viewport, contain keyboard focus, close through their existing cleanup handlers, and restore focus when closed. Busy submissions cannot be dismissed with Escape.

Authentication pages share an `AuthFrame` with a desktop introduction and a compact mobile form. Their existing validation, registration roles, authentication handling and password-recovery behavior remain in place. Login/registration password reveal controls support holding Space/Enter and hide on release or blur.

The dashboard places active sessions first. Cards show course, session type, venue, scheduled times, check-in window, requirements and the action determined by the existing policy function. A settled submission is called **Submission recorded**, rather than inferred to be approved. The dashboard also provides a compact setup summary, enrolled courses and the existing paginated attendance history. Pending, approved, flagged, rejected and appealed remain distinct.

`/profile` is protected by the existing `AuthGuard`, including its student/staff boundaries and session-change handling. It shows authenticated account metadata without unsupported editing controls, saved backend consent, face enrollment and browser registration. Detailed consent revocation is available here; quick setup remains available from the dashboard. The camera/location/consent workflow was extracted from the existing dashboard into `StudentSetup`, rather than replaced. Its camera constraints, permission requests, consent payloads and stream cleanup are preserved. Enrollment continues to use `FaceEnrollmentModal` and refreshes the authenticated profile after success. The dashboard validates the profile again when revisited.

### Browser registration semantics

`readBrowserDeviceIdentifier()` only reads and validates the existing localStorage UUID. It never creates, repairs, rotates or persists one. `BrowserRegistration` uses `GET /api/v1/devices/my-devices`; it never calls the registration POST or the attendance preflight coordinator. Responses are scoped to the authenticated account, previous lookups are aborted on unmount/account changes, and foreign-owner responses fail closed.

The page shows a shortened identifier, current-browser association, active state, supplied browser/platform metadata, first-seen/last-seen dates and the backend trust assessment. Missing metadata is identified as unavailable. The API's `is_active` boolean cannot distinguish revoked from other inactive states, so the UI says **Inactive / revoked** and explains that limitation. No revocation or reactivation controls were added. Registration is explicitly described as a browser installation UUID, not hardware ownership or cryptographic attestation.

### Check-in presentation

The existing device preflight, submission-time confirmation, challenge issuance, capture order/timing, frame data, GPS freshness, QR handling, submission payload, retry cooldown and uncertain-submission reconciliation are retained. The capture components retain their session keys and state ownership. `ModalFrame` provides a stable presentation shell; instruction updates do not remount capture children.

`CheckinResultSummary` separates the recorded attendance status, face recognition, effective liveness score, overall risk, QR outcome and venue distance. Known risk codes receive readable explanations, while technical codes remain available in a disclosure. Unknown/malformed signals do not produce invented reasons. Network heuristics are described as signals, not proof of VPN/proxy use. A failed active challenge with an effective score of zero explains the backend's `0.00` convention; it is not presented as image quality or a probability of being a real person. Lower overall risk is explicitly distinguished from hard policy requirements. No score or decision is recalculated in the frontend.

## Attendance & Appeals security dependency

The student action is disabled by default and explains that secure portal access is being prepared. The Profile action always points to Next.js `/profile`.

Two server environment settings are required before student portal navigation can be enabled:

```text
SAIV_STUDENT_PORTAL_ENABLED=true
NEXT_PUBLIC_STUDENT_PORTAL_URL=/portal/
```

**Do not enable these now.** Module 4 must first remove bearer-equivalent URL session restoration, replace it with an approved session mechanism, verify logout/session expiry and enforce student-only access to the caller's own attendance/appeals. Gateway assets, WebSockets, CSRF/origin behavior and private HTTPS operation must then be reviewed with the dashboard teammate. The gateway's current `/portal/` 503 restriction is unchanged.

Configuration is read by `/api/config` at runtime. Future Compose deployment must explicitly forward both settings into the frontend container; merely adding them to `.env.phase3a` does not inject them into a container. URLs containing credentials, query parameters, fragments or unsafe schemes are refused. HTTPS phone origins cannot navigate to loopback or downgrade to HTTP. No JWT, refresh token, account ID or session identifier is added to a destination. The action uses a plain full-navigation link with `noopener noreferrer` and no referrer. The service worker handles only known Next.js student routes, leaving portal navigation, assets and WebSockets to their server. Separate portal sign-in may be required.

## Localhost routing correction

A real Chrome check found that the existing absolute tunnel API URL made localhost review depend on passing the private tunnel's separate access gate. The disposable credentials succeeded through the local gateway, but the cross-origin tunnel request failed.

When the deployment explicitly supplies `SAIV_BACKEND_INTERNAL_URL`, `/api/config` now advertises `apiViaFrontend: true` without disclosing the internal URL. The browser then uses its current origin plus `/api/v1`. On port 8080, Caddy routes this directly to FastAPI; on port 3000, Next.js uses its existing rewrites. A Docker build argument supplies `http://backend:8000` because Next.js compiles those rewrites during its build. Host builds retain the existing `127.0.0.1:8000` default. Deployments without this explicit proxy configuration retain the configured external API URL. CORS, JWT/RBAC and backend verification policies were not weakened. Favicon metadata now points to the existing PWA icon.

## Changed files

All paths below are relative to the repository root. Existing Phase 3A changes to other modules are not new Phase 3B work.

### Created

| File | Purpose |
|---|---|
| `module1-frontend/app/profile/page.tsx` | Protected account/settings page |
| `module1-frontend/components/student/StudentSetup.tsx` | Shared original consent/camera/location/enrollment workflow |
| `module1-frontend/components/student/BrowserRegistration.tsx` | Read-only device information |
| `module1-frontend/components/student/EnrolledCourses.tsx` | Course list and honest empty/error states |
| `module1-frontend/components/student/AttendancePortalAction.tsx` | Safely gated student portal action |
| `module1-frontend/components/checkin/CheckinResultSummary.tsx` | Status and verification explanations |
| `module1-frontend/components/ui/StudentHeader.tsx` | Branding, student navigation, skip link and logout |
| `module1-frontend/components/ui/AuthFrame.tsx` | Shared authentication presentation |
| `module1-frontend/components/ui/Feedback.tsx` | Status badges, notices and empty states |
| `module1-frontend/components/ui/ModalFrame.tsx` | Stable, scrollable dialog and focus management |
| `module1-frontend/lib/portal-navigation.ts` | Credential-free portal destination validation |
| `module1-frontend/lib/verification-messages.ts` | Safe risk-signal parsing and explanations |
| `module1-frontend/tests/profile.test.cjs` | Profile guard, consent, devices and account-switch regressions |
| `module1-frontend/tests/presentation.test.cjs` | Session/status/result explanations and empty states |
| `module1-frontend/tests/portal.test.cjs` | Portal gate, URL safety and service-worker exclusions |
| `module1-frontend/tests/proxy-config.test.cjs` | Explicit same-origin proxy configuration |
| `module1-frontend/tests/browser-smoke.cjs` | Real Chrome/API smoke and responsive checks |
| `docs/PHASE3B-FRONTEND.md` | This implementation/review record |

### Modified

| File | Purpose |
|---|---|
| `module1-frontend/app/globals.css` | Shared design tokens, controls and responsive layouts |
| `module1-frontend/app/dashboard/page.tsx` | Attendance-focused composition; extracted setup state |
| `module1-frontend/app/login/page.tsx` | Shared authentication layout and accessible errors |
| `module1-frontend/app/register/page.tsx` | Shared authentication layout and accessible errors |
| `module1-frontend/app/forgot-password/page.tsx` | Shared authentication layout |
| `module1-frontend/app/layout.tsx` | Existing PWA icon used for browser favicon |
| `module1-frontend/components/student/AvailableSessions.tsx` | Responsive session presentation; existing eligibility retained |
| `module1-frontend/components/student/AttendanceHistory.tsx` | History/status presentation; pagination retained |
| `module1-frontend/components/checkin/CheckInModal.tsx` | Shared dialog styling and result explanations |
| `module1-frontend/components/checkin/DeviceRegistrationGate.tsx` | Shared dialog presentation; registration behavior retained |
| `module1-frontend/components/face/FaceEnrollmentModal.tsx` | Shared dialog presentation; capture/enrollment retained |
| `module1-frontend/lib/browser-device.ts` | Added non-generating identifier read |
| `module1-frontend/lib/public-config.ts` | Optional student portal gate and explicit API proxy capability |
| `module1-frontend/lib/client-config.ts` | Runtime portal settings and same-origin proxy resolution |
| `module1-frontend/public/sw.js` | Restrict navigation interception to known student routes |
| `module1-frontend/package.json` | Add focused tests and Chrome smoke command; no dependency changes |
| `module1-frontend/tests/student-flow.test.cjs` | Next.js Link test adapter for new navigation |
| `module1-frontend/tests/access.test.cjs` | Keyboard password-reveal regressions |
| `module1-frontend/tests/password-reset.test.cjs` | Replace old spacing-class assertions with shared-layout assertions |
| `module1-frontend/tests/production-smoke.cjs` | Include protected profile initial state and useful startup diagnostics |
| `module1-frontend/.env.example` | Document disabled student portal settings |
| `module1-frontend/.dockerignore` | Retain existing exclusions; exclude `out` and general logs |
| `module1-frontend/Dockerfile` | Preserve Phase 3A changes; add Docker-internal rewrite build argument |
| `module1-frontend/README.md` | Link to this review and commands |
| `tools/phase3a/saiv.ps1` | Preserve existing helper behavior; support service-specific build/rebuild with `--no-deps` |

## Review commands — Windows PowerShell 5.1

The current stack is already running. To rebuild only the changed frontend:

```powershell
Set-Location -LiteralPath C:\Users\user\Capstone-dev
.\tools\phase3a\saiv.ps1 rebuild -Service frontend
.\tools\phase3a\saiv.ps1 status
.\tools\phase3a\saiv.ps1 logs -Service frontend
Start-Process 'http://localhost:8080/dashboard'
```

Frontend: `http://localhost:8080`; settings: `http://localhost:8080/profile`. Direct frontend: `http://localhost:3000`. For Android, reuse the existing private HTTPS Dev Tunnel forwarding **8080**, sign in to its access gate as necessary, and open `/dashboard`. No gateway changes or new port forwarding are needed.

Frontend checks:

```powershell
Set-Location -LiteralPath C:\Users\user\Capstone-dev\module1-frontend
npm test
npm run typecheck
npm run lint
npm run build
npm run test:production
npm run test:browser
```

The Chrome test requires the existing disposable `.local/phase3a/browser-account.json`, Windows Chrome and the running development stack. It uses actual browser UI login and real backend reads, without replacing API responses. It does not create accounts, enroll courses, change consent, register devices, capture evidence or submit attendance. It performs ordinary login/logout, which can produce authentication/audit records. `CHROME_PATH` can specify another installed Chrome executable; `SAIV_BROWSER_ORIGIN` can select the direct frontend instead of the default gateway. Screenshots and sanitized diagnostics are stored under ignored `.local/phase3b/browser/<port>/`. Populated session/result fixtures are explicitly labeled **presentation only**, and create no backend records.

## Verification and remaining manual checks

- Original baseline: **310/310** tests and TypeScript passed before implementation.
- Updated suite: **359/359** tests passed, with none skipped. Existing capture, retry, uncertainty, refresh and registration tests remain active.
- TypeScript, existing ESLint configuration, production build and production HTTP smoke passed. No new frontend dependencies or lockfile changes were required.
- Real Chrome/API checks passed login, protected dashboard/profile redirects, dashboard/profile navigation, persisted authentication after refresh, logout, consent display and read-only device lookup. No UUID or camera/GPS request occurred merely from rendering or opening the camera setup dialog. Dialog focus, Tab containment, Escape and focus restoration were checked.
- Responsive checks cover 320, 375, 393 and 430 CSS pixels, landscape, larger text and setup dialogs. Separate populated layout fixtures cover session cards and failed-challenge explanations without claiming real attendance outcomes. Final browser output gives exact check counts and diagnostics.
- Final Chrome runs passed **30 layout checks on port 8080 and 30 on port 3000**, with no runtime exceptions, console errors, failed requests or HTTP errors in those successful runs. Five checks per origin use explicitly synthetic presentation fixtures; the authentication/profile checks use the real API. Login/registration keyboard password reveal is also exercised.
- Only the frontend container identity changed. Backend, database, Redis, recognition, dashboard, gateway and monitoring container identities remained unchanged. The development ONNX SHA-256 remains `9cc6e4a75f0e2bf0b1aed94578f144d15175f357bdc05e815e5c4a02b319eb4f`; Compose and the pre-existing Module 2/3/4 deployment changes were preserved.
- Initial checks exposed and then resolved the localhost/private-tunnel API problem and missing favicon. Sandbox restrictions initially prevented build output creation and temporary production server startup; those checks passed when run with the required local execution permissions.
- Docker's install reports existing dependency advisories, including Next.js 14.1.0. No framework upgrade or automatic audit fix was mixed into this redesign; coordinate a separate dependency/security upgrade.

Physical Android regression remains necessary after review: login/refresh/logout; profile consent grant/revocation and enrollment refresh; real camera cleanup in portrait/landscape; approved attendance with the requested blink/head turn and correct GPS; rejected missed challenges/out-of-bounds GPS; retry cooldown and uncertain-submission recovery. Preserve backend consent revocation effects. iPhone Safari camera, GPS, focus, viewport/safe-area behavior and installed-PWA updates require a physical iPhone. Chrome emulation is not that evidence.

## Coordinated follow-up

- **Module 2:** revocation/reactivation policy, optional device metadata, attestation semantics and network/proxy signal interpretation. No device-management mutation controls were added.
- **Module 3:** final team approval of the development recognition model; model, preprocessing, SimHash and all thresholds remain unchanged.
- **Module 4:** replace URL-session restoration; verify student RBAC and secure gateway/WebSocket support; correct rejected-submission attendance percentages and expose geofence/rejection evidence. These business/security changes were not implemented here.

The application is ready for frontend review and physical-device regression, not a claim that the redesigned UI has already passed real-camera attendance testing or that the Streamlit portal is secure to enable.
