# Phase 3B roadmap — requirements retained, not implemented

Phase 3A changes deployment/verification only. No frontend redesign, new route,
device information panel or student portal button is included.

1. Finish the real laptop/phone baseline with the approved recognition model and
   unchanged security policy. Resolve independently verified integration blockers
   with their module owners, in reviewable changes.
2. Establish a distinctive accessible visual system; do not reproduce another
   team's screenshots, layout, colors, typography, wording or navigation.
3. Keep `/dashboard` attendance-focused: welcome/identity summary, active/upcoming
   eligible sessions, readiness, enrolled courses and recent attendance. Include
   useful loading/empty/error/result states; no unnecessary Online header indicator.
4. Create authenticated `/profile` for personal information and privacy/settings.
   The dashboard profile action opens this Next.js route. Only offer edits supported
   by real APIs. Move detailed settings out of the attendance workflow.
5. On `/profile`, distinguish saved backend consent from live browser permissions.
   Preserve revocation and backend enforcement. Opening a page must not request
   camera/GPS permissions automatically.
6. Add read-only Browser Registration/My Devices information to `/profile`: current
   registration/active state, shortened UUID, available metadata/timestamps and
   clearly labelled backend trust assessment; optional account-owned browser list.
   Viewing it must not create an identifier or register a browser. Use a non-mutating
   local lookup and existing self APIs. Handle absent/inactive/revoked/unknown/storage
   failure states without implying hardware verification or cryptographic attestation.
   Coordinate revocation/reactivation policy with Module 2 before adding controls.
7. Add a distinct dashboard Attendance & Appeals action to the existing Streamlit
   student view. Use runtime-configured local/HTTPS URLs, separate-sign-in wording
   and preserved backend/RBAC restrictions. No JWT, refresh token or bearer-equivalent
   session credential in URLs. Resolve Module 4's URL-session restoration first.
8. Preserve capture component lifetimes, original evidence dimensions, session/auth
   invalidation, geolocation freshness, server-issued liveness instructions and
   uncertain-submission/retry safeguards through presentation refactors.
9. Verify touch layouts, keyboard/focus/modal behavior, contrast, understandable
   errors, and actual iOS/Android camera/GPS behavior. Keep dependencies and animation
   modest. Exclude `/portal/` from the PWA navigation fallback when enabling it.
10. Run existing frontend checks plus focused new tests and isolated integration
    tests; record physical-device outcomes. Review the diff and coordinated module
    patches before requesting approval for a PR into `dev`. Do not commit/push
    without that approval.
