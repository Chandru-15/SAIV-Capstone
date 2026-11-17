# Forgot Password — independent implementation

## Scope and base

Branch: `feature/forgot-password-email-otp`, originally based directly on
`origin/dev` at `bb286424367bfbaaf9b5a3e55315926cd6ea883b`.
At the final review on 3 November 2026, fetched `origin/dev` was one commit ahead,
at `6e1815dbb2e5f8046138d004d3bedf00b14b7b2d`. Its changed files do not overlap
the proposed core feature commit. No merge/rebase has been performed; validation
results below apply to this feature's original base, not the future combined tree.

Worktree: `C:\Users\user\.codex\worktrees\saiv-forgot-password\Capstone-dev`.
The requested `C:\Users\user\Capstone-forgot-password` destination could not be
used because moving the newly created worktree there was denied. The managed
worktree is independent of the original dev, Android and biometric experiments.
No experiment changes, dependency upgrades or existing service/data changes are
part of this feature. Nothing has been staged, committed or pushed.

## User journey and existing contracts

1. `/login` → **Forgot Password?** below the password field.
2. `/forgot-password`: enter email → **Send Verification Code**.
3. Receive a generic conditional confirmation. A successful HTTP response is not
   a guarantee that an account exists or that email delivery succeeded.
4. Enter the latest six-digit code, new password and confirmation together. The
   API verifies the code and changes the password in one operation, not a separate
   client-side verification step. Both password inputs have a show/hide toggle.
5. Success → **Return to Login**. There is no automatic sign-in.

| Existing Module 2 endpoint | Request | Response |
| --- | --- | --- |
| `POST /api/v1/auth/forgot-password` | `{ "email": "synthetic@example.edu" }` | Generic `200 { "message": "..." }`, including unknown, ineligible and throttled accounts |
| `POST /api/v1/auth/reset-password` | `{ "email": "synthetic@example.edu", "code": "123456", "new_password": "..." }` | `200 { "message": "..." }`; invalid/expired/used/exhausted code is a domain validation error (`422`) |

These paths are anonymous in the frontend API client: no existing access token,
automatic JWT refresh or session mutation. OTPs and passwords stay only in
component/request memory, never browser storage or logs. Arbitrary API error
bodies are not displayed. The UI blocks simultaneous submissions and does not
automatically replay uncertain reset requests. On timeout/connection loss, try
logging in with the new password before repeating the reset.

Existing backend policy, mirrored and contract-tested in the frontend:

- Cryptographically random six-digit code; 10-minute expiry; single use.
- Five failed guesses, then consumption; 60-second resend cooldown.
- Five codes per account per hour; ten requests per source IP per hour.
- A newly issued code invalidates the previous unused code.
- At least eight Unicode code points; reset passwords are limited to 72 UTF-8
  bytes because the existing bcrypt implementation otherwise truncates them.
- Resend/expiry clocks are UI guidance. The backend remains authoritative and
  silently suppresses requests beyond its limits to avoid account enumeration.

## Reused backend and focused corrections

Reused `UserService`, `PasswordResetCode`, HMAC-SHA256 keyed by the existing
`SECRET_KEY`, bcrypt hashing, audit events, SMTP service, request schemas and both
routes. No duplicate recovery service, endpoints or models were added.

Corrections required for this feature:

- Lock the existing user row during OTP issuance/redemption and admin password
  changes. Concurrent redemption has exactly one winner; wrong guesses and resend
  limits cannot race. A PostgreSQL transaction advisory lock serializes the
  existing per-IP count/insert limiter without adding a rate-limit table.
- Read redemption time after acquiring the account lock, so a code that expires
  during the wait is rejected, including at the exact expiry boundary. Issuance
  also reads time after locking so newly issued codes retain the full 10-minute
  validity period. The IP-limit window is evaluated after its advisory lock.
- Inactive/deleted accounts cannot self-reset or receive a new reset code.
- An admin temporary-password reset invalidates outstanding email codes.
- Reject missing HMAC secret and reset passwords exceeding bcrypt's byte limit.
- Fail safely if a mail worker cannot start or SMTP configuration is invalid.
- Verify SMTP certificates/hostnames for TLS and refuse plaintext SMTP credentials.
  An unauthenticated local catcher can still use plaintext on its isolated network.

Remaining limitations:

- Existing access/refresh JWTs are **not revoked** by a reset. A regression test
  explicitly documents that an already-issued access token still works. Proper
  global session invalidation needs a separately reviewed backend design.
- Existing registration/login password-length behavior outside recovery is not
  changed. A broader password-policy migration is a separate backend task.
- Public response bodies are generic, not constant-time. IP limiting depends on
  the deployment supplying a trustworthy source IP; untrusted forwarded headers
  need proxy-boundary hardening. The new lock prevents concurrency bypass, not
  distributed abuse or forged source identity.
- Email runs on the existing best-effort daemon thread, not a durable delivery
  queue. Failure leaves the current password unchanged; request a new code after
  the cooldown once delivery is repaired. A resend may invalidate an older code
  even if the new email fails. No delivery-success claim is shown to the user.
- The existing Next.js 14/dependency security advisories remain outside this
  feature. Installation reported 22 findings (11 moderate, 10 high, 1 critical).

## Staff compatibility

Module 4 `app/client.py::SaivClient.login` uses Module 2 `/auth/login`, the same
account database/password hashes and token/user response. Its login view already
has Forgot Password and combined code/password reset controls. Module 1 recovery
works for eligible student, TA, instructor and admin accounts. A reset affects
both interfaces when they target the same backend/database. Existing student/staff
redirects are preserved. No Module 4 changes were necessary. Backend tests cover
all four roles; an interactive Streamlit login has not been performed here.

## Email configuration

Reuse `module2-backend/.env.example` as documentation; it contains placeholders
only. The backend does not automatically load that file, and root Compose does
not forward SMTP variables. Operators must inject them into a separately approved
backend process/container. Do not edit/restart an existing stack for this test.

Required settings are `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, optional `SMTP_USER`
and `SMTP_PASSWORD`, plus `SMTP_STARTTLS=1` (typically port 587) or `SMTP_SSL=1`
(typically port 465). Use the existing backend `SECRET_KEY`; do not rotate it just
for recovery. With no `SMTP_HOST`, mail is disabled. Real Gmail SMTP delivery to
an NTU Outlook recipient was manually confirmed in the separate disposable test
environment (see acceptance evidence below). The user configured the app password
privately; no credential or OTP value belongs in this document or repository.
This test does not configure SMTP for the shared or production backend.

For local tests, use a catcher with no SMTP credentials. The optional demo below
uses [Mailpit](https://mailpit.axllent.org/docs/install/docker/) on an isolated
network. Its web inbox is localhost-only. Never use real accounts in this demo.

## Database and verification results

No new table/column/migration. `PasswordResetCode` already exists in the base ORM;
the existing backend startup bootstrap creates its table if absent. The base
`db/schema.sql` alone does not include it. Deployments must verify the table exists
and that the existing bootstrap has the required DDL permissions; its existing
retry/error handling is unchanged. No persistent database was accessed or changed.

Executed with Node 22 and this branch's unchanged dependency lockfile:

| Check | Result |
| --- | --- |
| `npm test` | **295 passed**, including all original 271 and 24 recovery tests |
| `npx --no-install tsc --noEmit --incremental false` | Passed |
| `npm run lint` | Passed, no warnings |
| `npm run build` | Passed |
| `npm run test:production` | **7 checks passed** |
| Disposable full backend suite | **535 passed**, one existing Starlette/AnyIO deprecation warning |
| `git diff --check` | Passed |

Backend tests use a new internal Docker network and RAM-only PostgreSQL instance,
synthetic users, mocked SMTP and the actual services/controllers. No published
ports, existing volumes or live integrations. The runner verifies the source
mount and database identity before pytest and removes only its labelled disposable
resources afterwards. The local test image is retained for repeat runs.

Coverage includes valid/invalid/expired/reused OTPs, five guesses, resend/code
invalidation, hourly limits, concurrent issuance/redemption/guesses, inactive
accounts, provider failure, actual email composition through mocked SMTP, old
password rejection/new-password login, all roles and existing auth regressions.
Five timing regressions advance a controlled clock across real PostgreSQL account
lock queries: valid just before expiry, rejection at/after expiry, and full code
validity for both self-service and admin issuance. Existing concurrency tests
retain the single-use, failed-attempt and resend protections.
Frontend coverage includes navigation, phone input/autofill attributes, password
validation, loading/duplicates, cooldown, unmount cancellation, safe errors,
uncertain network outcomes, no auto-login and no storage mutation. Responsive
classes and production HTML are checked; physical Android/iOS and browser layout
acceptance still require manual testing. Mock/local delivery is not genuine
external SMTP-provider delivery.

Manual acceptance on the isolated demo was completed and reported by the user:

- A registered disposable account received its OTP email through local Mailpit.
- An unregistered valid email received the generic confirmation without an email.
- Manual password reset and subsequent login were reported successful.

Real-email manual acceptance was subsequently completed and reported by the user
on 3 November 2026 in the separate disposable environment at `localhost:3004`
(backend `localhost:8401`):

- A disposable student account was registered using an NTU Outlook inbox.
- A real OTP was sent through the dedicated Gmail SMTP sender using STARTTLS.
- The message arrived in the recipient's **Junk Email** folder.
- An incorrect OTP was rejected.
- The correct OTP reset the password successfully.
- The original registration password was rejected afterward.
- The new password allowed login to the student dashboard.

These are user-reported browser results, separate from automated/mocked tests.
Real delivery is verified for this route; normal inbox placement and production
deliverability are not guaranteed. No recipient address, password, code, mail
headers or private screenshots are included. Physical Android/iOS recovery and
interactive Streamlit acceptance are still separate, unverified items.

Re-run only against disposable infrastructure, from this worktree root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\password-reset\test-disposable.ps1
# Optional faster recovery-only repeat, after the test image is built:
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\password-reset\test-disposable.ps1 -SkipBuild -RecoveryOnly
Set-Location .\module1-frontend
npm test
npx --no-install tsc --noEmit --incremental false
npm run lint
npm run build
npm run test:production
```

## Optional manual demo, separate from every existing service

This Compose definition was validated and launched locally on 2 November 2026.
Health checks, frontend API configuration, CORS preflight, synthetic registration/
initial login and local Mailpit delivery passed. The user subsequently completed
the manual recovery flow described above. The assistant did not redeem the code;
Windows browser automation had stopped because it could not reliably identify the
current URL for its safety checks.
The demo launcher needs `PYTHONPATH=/app` so Uvicorn's `/demo` entrypoint can import
the backend package; this is set only in the demo Compose environment. It adds a new
localhost-only backend (8400), local mail inbox (8025) and RAM-only database. It
does not copy existing accounts or biometric data. A demo-only entrypoint refuses
any database other than `postgres/saiv_forgot_password_demo` and any mail host
other than `mailpit`, and allows CORS only from `http://localhost:3003`. Production
backend CORS is unchanged. No API proxy or tunnel is involved.

1. Check ports **8400, 8025, 3003** are free and no containers already belong to
   `saiv-forgot-password-local`. Stop if they are in use; do not stop other services.
2. In a new PowerShell window at this worktree root:

```powershell
Get-NetTCPConnection -State Listen | Where-Object LocalPort -In 8400,8025,3003
docker ps -a --filter label=com.docker.compose.project=saiv-forgot-password-local --format '{{.Names}}'
# Continue only if both checks show no conflicting resources.
$env:FP_DB_PASSWORD = python -c "import secrets; print(secrets.token_urlsafe(24))"
$env:FP_SECRET_KEY = python -c "import secrets; print(secrets.token_urlsafe(48))"
docker compose -f .\tools\password-reset\compose.local.yml -p saiv-forgot-password-local config --quiet
docker compose -f .\tools\password-reset\compose.local.yml -p saiv-forgot-password-local up -d --build
Set-Location .\module1-frontend
$env:NEXT_PUBLIC_API_URL = 'http://localhost:8400/api/v1'
npm run dev -- --hostname 127.0.0.1 --port 3003
```

Do not print the generated environment values. Docker inspection/config output
can contain secrets; use `config --quiet`, not unrestricted config output. The
database is intentionally disposable: removing its container loses demo records.

3. Open `http://localhost:3003/register` and manually create only a synthetic
   account, e.g. a non-real `example.edu` address. Do not use existing credentials.
4. At `/login`, select **Forgot Password?**, request a code, then read only this
   synthetic message at `http://localhost:8025`. Enter its code/new password.
5. Confirm success, old-password login rejection, new-password login acceptance,
   resend cooldown and invalid-code feedback. Repeat with disposable staff roles
   only if necessary; do not change Module 4's running configuration.
6. Test narrow/landscape layouts and keyboard navigation. A physical phone needs
   an approved HTTPS frontend/API deployment and appropriate CORS; localhost here
   refers to the phone itself. No mobile tunnel is prepared by this feature.

## Independent review / later commit

The recommended core commit contains 16 files: frontend feature/tests/docs,
focused backend fixes/tests, SMTP placeholders, and the three generic disposable
test/demo tools directly under `tools/password-reset/`. The optional nine files
under `tools/password-reset/real-email/` are local operator tooling, not required
by the feature. Keep them out of the core commit for now: they hard-code the test
sender and feature branch, and their private configuration is Windows-specific.
They can be generalized and reviewed as a separate tooling change later. Preserve
them locally; do not remove live test environments or private configuration.

Review `git status --short` and `git diff --check`. The change set is limited to
login navigation, the new page/helper/tests, anonymous recovery API paths, focused
Module 2 security fixes/tests, SMTP placeholders, this document and the disposable
testing tools. No lockfile, shared Compose, database schema, Module 3 or Module 4
changes. The smoke test simply adds the new public route.

After approval, stage only the listed feature files individually (never an entire
experimental worktree), review `git diff --cached`, then make an independent
feature commit. Push this feature branch and target `dev` only after explicit
approval. No staging, commit, push, merge or PR has been performed by this task.
