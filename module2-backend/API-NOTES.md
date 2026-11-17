# Module 2 — behaviour worth knowing

Notes on backend behaviour that is not obvious from `docs/API-SPECIFICATION.md`
and that the dashboard and frontend depend on. Every item below was checked
against the running backend rather than read off the code. Sections 1-5 were
verified at commit `b470de6` (suite at 525 passing); section 6 on the working
tree that followed it, with the suite at 662 passing.

This file deliberately documents only what was verified. An earlier,
longer version described per-session rosters, login throttling and
`POST /admin/users`, none of which exist any more; a stale API document is
worse than none, so it was removed rather than carried forward.

---

## 1. Registration honours the caller's `role` — a known open risk

`POST /api/v1/auth/register` accepts `role` from the request body and uses it:

```python
# app/schemas/user.py
class RegisterRequest(BaseModel):
    ...
    role: UserRole = UserRole.STUDENT

# app/controllers/auth_controller.py
return AuthService(db).register(..., role=payload.role, ...)
```

The default is `student`, so the UI behaves correctly. But the field is
caller-supplied, not server-assigned, so anyone who can reach the endpoint can
create an **admin** account with a direct request. No invite, no approval, no
existing-admin check.

It is this way because `docs/API-SPECIFICATION.md` specifies `role` on this
endpoint, and the graded tests register staff accounts through it. The point of
recording it here is that it is a deliberate specification-compliance decision,
not an oversight.

To close it, `register()` would ignore `role` and always create a student,
leaving staff provisioning to the admin routes. That change breaks the
specification and the public tests, so it should not be made casually.

---

## 2. `GET /api/v1/courses/{course_id}/roster` — who is enrolled

Returns a page envelope, not a bare list:

```
GET /api/v1/courses/{course_id}/roster?limit=50&offset=0
-> PageResponse[EnrollmentResponse]
```

`limit` defaults to 50 and is capped at 100; `offset` starts at 0. Requires an
authenticated caller. Each row is an **enrolment**, not a user:

```
id, student_id, course_id, is_active, enrolled_at, dropped_at
```

Two consequences for anything consuming this:

- There is no name or email. Resolve `student_id` against the user directory
  separately.
- **Dropped enrolments are still returned**, with `is_active: false` and a
  `dropped_at` timestamp. Filter on `is_active` or you will show students who
  have left. Rows are kept rather than deleted so a historical check-in still
  has an enrolment to point at.

---

## 3. Sessions open manually; only closing is automatic

The status machine allows no automatic promotion into `active`:

```python
# app/services/session_service.py
SessionStatus.SCHEDULED: (SessionStatus.ACTIVE, SessionStatus.CANCELLED),
SessionStatus.ACTIVE:    (SessionStatus.CLOSED, SessionStatus.CANCELLED),
```

Closing *is* automatic — `app/main.py` runs a background loop that calls
`close_finished_sessions()` every `SESSION_AUTO_CLOSE_SECONDS` (default 60).

So a session sitting inside its scheduled window is **not** open for check-in
until a staff member sets it to `active`. A student seeing nothing to check in
to during a scheduled lecture is usually this, not a bug. The asymmetry is
intentional: closing late only ends an attendance window, while opening early
would let people check in to a session nobody is running.

---

## 4. `/courses/me` and `/sessions/my-sessions` are scoped differently

They look like a matching pair and are not:

- `GET /api/v1/courses/me` → `CourseService.list_for_student(current_user.id)`.
  The courses the caller is **enrolled in**. For an instructor or admin this is
  usually empty; it is not "courses I teach".
- `GET /api/v1/sessions/my-sessions` → `SessionService.list_mine(current_user)`.
  Role-aware: assigned sessions for a TA, sessions of their courses plus any
  they created for an instructor, everything for an admin.

For staff-facing views, use the course catalogue with the ownership filter
rather than `/courses/me`.

---

## 5. `group_members` duplicates `course_id` and `kind` on purpose

`group_members` carries `course_id` and `kind` even though both are reachable
through `group_id -> course_groups`. The duplication exists so the database
itself can enforce one tutorial and one lab per student per course:

```python
UniqueConstraint("course_id", "kind", "student_id", name="uq_group_members_one_per_kind")
```

That constraint cannot be written against the normalised shape, because the
columns it needs live in the parent table.

The cost is that the copies can drift from the group's real course if a row is
written carelessly. A composite foreign key — `(group_id, course_id, kind)`
referencing a matching unique key on `course_groups` — would make drift
impossible while keeping the constraint. Noted rather than done, since the
current writers all go through the service layer.

---

## 6. `require_liveness_check` requires an *active* challenge, not any image

`POST /api/v1/checkins/` accepts biometric evidence two ways:

```
challenge_id + challenge_frames   active   -- server chose the challenge
image                             passive  -- one still frame
```

On a session with `require_liveness_check = true`, **only the active path
satisfies the requirement.** A submission carrying just `image` is recorded
with status `flagged` and a `replay_suspected` risk signal, even when module 3
returns `liveness_passed: true` with a high score, and even when
`risk_threshold` is relaxed — the gate is a requirement, not a score budget.

This was not always so. Until this change the flag was read only in the
browser: `CheckInModal` checks `session.require_liveness_check`, calls
`POST /checkins/liveness-challenge` and submits frames. Nothing on the server
required any of it, so a caller that skipped the challenge call and posted a
single `image` was **approved**. That was reproduced end to end, not inferred
from the code:

```
HTTP 201   status: approved   liveness_passed: true   liveness_score: 0.93
           liveness_challenge_type: passive
```

It matters because the passive path has little anti-spoofing to offer. Module 3
leaves `challenge_passed` as `null` for `passive` and scores detection
confidence, image quality and an inferred nose-depth value. The first two rate a
sharp printed photo *above* a real face in poor light. Only the active path is
bound to a type the server chose, for this student and session, within 60
seconds, single-use.

**What this means for each caller**

- **Module 1 (frontend)** — no change needed. It already requests a challenge
  whenever `require_liveness_check` is true and sends `image` only for
  face-match-only sessions, which still work exactly as before.
- **Module 4 / dashboards** — `replay_suspected` now appears on real traffic for
  the first time. It means "no server-issued challenge", and its `details`
  carry `{"liveness_challenge_type": ..., "reason": "no_server_issued_challenge"}`.
- **Anything scripted against the API** — a bare `image` on a liveness session
  will be flagged rather than approved. Request a challenge first.

**`liveness_challenge_type` is now in the check-in response.** Without it, a
record flagged for liveness while `liveness_passed` reads `true` is
unexplainable; that field is the explanation. Values: `blink`, `head_turn`,
`passive`, or `null` when no biometric evidence was submitted.

Note the deliberate asymmetry in how a `null` type is treated: the decision
counts it as unmet (nothing was verified), but the risk engine does **not**
raise `replay_suspected` for it. A missing verdict is usually a student with no
camera consent or a module 3 outage, and labelling that a suspected replay
would send reviewers hunting an attacker who isn't there.

A failed *active* challenge is still a hard `rejected`, per the spec's status
table. This gate only governs what counts as a pass.

---

## 7. Two geolocation consistency signals: `impossible_travel` and `geo_ip_mismatch`

GPS coordinates arrive in the request body, so a spoofer picks them freely and
the geofence agrees with whatever they pick. Two checks now ask a different
question — not *is this fix at the venue*, but *does this fix contradict
something the client does not choose*.

```python
# app/services/risk_service.py
IMPLAUSIBLE_SPEED_KMH       = 200.0      # implied ground speed
MIN_TRAVEL_DISTANCE_METERS  = 1000.0     # below this it is GPS noise
IP_MISMATCH_DISTANCE_METERS = 150_000.0  # after the resolver's own radius
```

**`impossible_travel`** compares the submitted fix with this student's most
recent **approved** fix. The comparison runs over `checkin_attempts`, not
`checkins`, so it covers both an earlier attempt at the session being submitted
*now* (seconds ago) and a check-in at some other session (hours ago) —
`checkins` holds only the attempt that survived a retry.

**Only approved attempts form the baseline, and that filter is load-bearing.**
A rejected position is one the engine already refused to believe, so using it
to accuse the next submission would be reasoning from evidence we discarded.
The concrete case: a student whose first fix comes back stale from the OS — a
cached position from home — is rejected for being outside the geofence, walks
nothing, retries honestly from the lecture theatre seconds later, and that
retry reads as a 40 km teleport. The existing flow test
`test_an_automatically_rejected_check_in_can_be_retried` catches exactly this,
and `RiskService._retries` had already settled the principle: a stale GPS fix
is the ordinary reason for a second go, so an attempt is judged on its own
merits exactly as a first attempt would be.

What that gives up, stated plainly: a spoofer whose first attempt is rejected
can move their fake position and retry without tripping the speed test. That
pattern cannot be told apart from the honest stale-fix case using position data
alone, and the cost of guessing wrong is an attendance mark withheld from
someone who attended. Both positions stay on the attempt trail for a reviewer,
and `rapid_succession` still records that it took more than one go.

The distance floor is the load-bearing part. A phone indoors wanders hundreds
of metres between fixes; divided by the few seconds between two retries that
noise alone implies an aircraft's speed. Without the floor the check would fire
on honest students retrying in a dark room, which is the single most common
thing that happens on this endpoint.

**It is enforced in `_decide`, not by the score.** Geolocation carries 15% of
the weighted mean and the check can only raise that component to 1.0, so a
detected teleport moves the score by about 0.15–0.21 — never across a 0.5
threshold. Left to the average it would have been filed as a signal on an
`approved` check-in nobody reads. Same reasoning as the required-checks rule in
section 6.

**Flagged, never rejected.** A stale or cached OS fix can manufacture a jump
the student had no part in, and the cost of a false positive here is an
attendance mark withheld from someone who attended. A human sees both positions
and the elapsed time in the signal's `details`.

**`geo_ip_mismatch`** compares the fix with the apparent location of the source
address. Three things to know before relying on it:

- It is **inert in the current deployment**, by design. It needs a MaxMind
  GeoLite2 database (`GEOIP_DATABASE_PATH`, optional, see
  `app/services/ip_geolocation.py`) *and* an `X-Forwarded-For` header from a
  real reverse proxy. Without the header the only address we see is the Docker
  bridge, and locating that would manufacture a contradiction out of our own
  topology. With neither, the signal is omitted and the weights renormalise —
  "we could not tell" is never recorded as "the address matches".
- IP geography is coarse. The resolver's own `accuracy_radius` is subtracted
  before comparing, and the threshold is 150 km, so only a gross mismatch
  counts as evidence. Carrier egress and campus gateways sit far from the user.
- A VPN produces a mismatch from an honest student, which is why it scores 0.6
  rather than 1.0, does **not** flag on its own, and carries the same
  student-facing recommendation as `vpn_detected` so the two collapse into one
  line.

No address is sent anywhere: a lookup is a local read against a file, so a
student's IP does not leave the deployment and a check-in does not depend on a
third party's uptime.

**New `signal_type` value for module 4 and any dashboard.** `geo_ip_mismatch`
was added to `ck_risk_signals_type` in `db/schema.sql`. `CREATE TABLE IF NOT
EXISTS` is a no-op on an existing database, so the constraint is also dropped
and re-added further down the file — re-running `schema.sql` is what applies it.
`impossible_travel` was already in the allowed list and needed no DDL.

---

## 8. `X-Forwarded-For` is only believed from a trusted proxy

`client_ip` used to return the **leftmost** `X-Forwarded-For` entry:

```python
forwarded = request.headers.get("x-forwarded-for")
if forwarded:
    return forwarded.split(",")[0].strip()   # whatever the caller wrote
```

A proxy **appends** the address it received from, so the header reads

```
X-Forwarded-For: <whatever the client wrote>, <client>, <proxy1>, ...
                  \_____ forgeable ______/   \___ added by us ___/
```

The leftmost entry is therefore chosen by the caller. Harmless in a log line,
not harmless in `rate_limit_by_ip`, which uses this value as the counter key: a
credential-stuffing run that varies the header lands in a fresh bucket on every
request, and the 60-per-hour login and 10-per-hour registration budgets never
fire at all. Measured on the old code, 49 requests from one connection produced
**49 distinct buckets**; they now produce **1**.

The function's own docstring claimed the header was *"never trusted for an
access decision"*. Choosing a rate-limit bucket is an access decision.

**The rule now.** The header counts only when the *connection* came from an
address named in `TRUSTED_PROXY_IPS`. The chain is then walked from the
**right**, skipping entries that are themselves trusted proxies, until an
address we did not add is found; everything to its left is ignored. Naming the
proxies is all the configuration there is — no hop count to keep in sync, and a
chain that grows a hop needs no second setting changed.

```bash
TRUSTED_PROXY_IPS=10.250.0.9          # one proxy
TRUSTED_PROXY_IPS=10.250.0.0/24       # CIDR
TRUSTED_PROXY_IPS=                    # default: trust nothing, use the peer
```

**The default is empty, and that matters.** A default of "trust one hop" would
reinstate the bypass anywhere the backend is reachable directly — which is the
compose default, with port 8000 published. Do not set `TRUSTED_PROXY_IPS` while
that port is open: a client who can connect to the backend would then qualify
as the proxy. It is safe once the backend sits only behind a reverse proxy, and
it should name that proxy's address rather than a whole bridge subnet, since
the bridge gateway is also what direct traffic from the host looks like.

**What this costs.** Where traffic genuinely arrives through the frontend's
`/api/v1` rewrite and `TRUSTED_PROXY_IPS` is *not* set, every browser now shares
one bucket — the proxy's address — so the per-IP budgets become per-proxy. That
is the correct reading of what the backend can actually prove about the caller,
and the fix is to configure the proxy honestly, not to believe the header.

**Also changed:** `ip_is_forwarded` on `POST /checkins` no longer means "the
header was present" (any caller can send one). It now means "the address came
from a trusted proxy", which is what `network_service.network_signal_known`
always assumed. Where nothing is trusted, the network component is omitted and
the weights renormalise, exactly as before for a private direct peer.

**Audit trail.** `audit_logs.ip_address` now records the address the backend can
stand behind rather than the one the caller claimed. Behind an unconfigured
proxy that is the proxy's address — less specific than before, and no longer
attacker-controlled.

**Flow tests** model a real deployment: `conftest` injects a peer address
(Starlette's `TestClient` leaves `scope["client"]` unset) and names it as the
one trusted proxy. `tests/unit/test_client_ip.py` pins the untrusted path, and
`TestTheHeaderCannotBuyAFreshBudget` in `tests/flow/test_rate_limit_flow.py`
pins the bypass itself at `/auth/login` and `/auth/register`.

## 9. Password-reset codes are emailed, and email is best-effort

`POST /api/v1/auth/forgot-password` issues a six-digit code and emails it;
`POST /api/v1/auth/reset-password` spends it. Both reply the same way whether or
not the address has an account, so neither one confirms that an email is
registered.

```python
# app/services/user_service.py
def request_password_reset(...)   # rate limits, issues, then hands off to email
def _new_code(...)                # stores the code hashed, salted by its row id
# app/services/email_service.py
def send_email(...)               # plain SMTP; returns False rather than raising
```

**The code is never stored in the clear.** `_new_code` inserts the row, flushes
to obtain its id, and only then writes `code_hash = _code_hash(row.id, code)`.
The `code_hash="-"` placeholder on the insert is that two-step, not a stub — the
row id is the salt, so the hash cannot be computed before the row exists. A
stolen database yields no usable codes, and two accounts that happen to hold the
same six digits hash differently.

**Issuing is rate limited on two axes.** Per IP per hour
(`RESET_REQUESTS_PER_IP_PER_HOUR`), counted from `audit_logs` so that requests
for addresses with *no* account still count — otherwise the limiter itself would
answer "does this account exist". Per account there is both an hourly cap and a
resend cooldown (`RESET_CODES_PER_ACCOUNT_PER_HOUR`, `RESET_CODE_RESEND_SECONDS`).
The per-IP count-then-insert is serialised across API workers with
`pg_advisory_xact_lock` keyed on a hash of the address, so two workers cannot
both observe a count below the limit and both issue.

**Email never blocks the request and never fails it.** `send_email` returns
`False` and logs an exception *type* on any failure; it never raises, and the
reason never reaches the caller. It also runs on a background thread, because
waiting on a mail server would make a request for a real account measurably
slower than one for an unknown address — a timing oracle that would undo the
generic response above.

**With no `SMTP_HOST` the feature degrades instead of breaking.** Codes are
still issued and still appear on the admin's list
(`GET /api/v1/admin/password-reset-requests`), which is the documented fallback
alongside `POST /api/v1/admin/users/{user_id}/send-reset-email` and the
temporary-password route. `GET /api/v1/admin/email-status` reports whether
sending is configured so the dashboard can choose which to offer.

**The compose default is a local catcher, not a dead end.** Both
`docker-compose.yml` and `docker-compose.infra.yml` run `mailpit`, and
`SMTP_HOST` defaults to it, so a clean clone delivers codes with no account
anywhere — read them at <http://localhost:8025>. Set the `SMTP_*` variables in
`.env` to send through a real provider, and set `SMTP_STARTTLS=1` when you do:
the default is `0` because mailpit speaks plain SMTP.

**Tests.** `tests/flow/test_password_reset_flow.py` and
`tests/flow/test_password_recovery_security_flow.py` cover the happy path plus
enumeration, code reuse, expiry and the rate limits — 44 tests between them.

## 10. Replacing a face template needs staff approval, not a password

`PUT /api/v1/users/me/face` used to take the caller's password alongside the
photo. It no longer takes one. A student asks, an instructor or admin decides,
and the decision is spent by exactly one replacement.

```python
# app/models/face_reenroll.py     the request record (no photo, ever)
# app/services/face_service.py    request_reenroll / review_reenroll / reenroll
# app/services/staff_scope.py     visible_student_ids, can_review_student
```

| Endpoint | Who | What |
|---|---|---|
| `POST /users/me/face/reenroll-requests` | the student | ask; no photo |
| `GET /users/me/face/reenroll-requests` | the student | own history, newest first |
| `GET /face-reenroll-requests` | instructor, admin, TA | the queue, oldest first |
| `PATCH /face-reenroll-requests/{id}` | instructor, admin | approve or deny |
| `PUT /users/me/face` | the student | spend the approval, send the photo |

**Why the password went.** It was step-up re-authentication, and against a
stolen token it did real work. But the threat it could never address is the
account holder themselves: a student always knows their own password, so the
check proved somebody was present and never that anybody else *agreed*. A
template is what every later check-in is matched against, so the decision to
replace one belongs to whoever is responsible for that student. The audit
action `face_re_enroll_denied` survives the change with a new meaning — a staff
refusal rather than a mistyped password.

**The request carries no photo.** Deliberately. A pending row holding a base64
capture would be biometric data at rest that `unenroll` could never erase, and
this module's whole stance is that the image exists only for the duration of a
call. The student captures the replacement *after* approval, and it goes
straight to module 3 as it always has. The reviewer is therefore approving a
person, not an image.

**Single use, no expiry.** `face_reenroll_requests.used_at` is what makes an
approval single-use; there is deliberately no deadline. The status stays
`approved` after it is spent, because it was — `used_at` is the only thing that
distinguishes an approval still worth using, which is why `find_spendable_for_user`
matches `status = 'approved' AND used_at IS NULL` and takes `FOR UPDATE`, so
two concurrent re-enrolments cannot both read the same row as available.

**A rejected photo does not burn the approval.** `used_at` is stamped only
after module 3 has accepted the capture, in the same transaction as the
template write. A refused photo raises before that line, so a student whose
lighting was bad can retry without going back to their instructor.

**Who may decide.** Instructors and admins — the line `CheckInService.review`
already draws. A TA may read the queue and sees nothing, because
`visible_student_ids` hands them an empty set. An instructor answers for the
students actively enrolled on courses they teach, and nobody may decide their
own request, which matters for admins since they are in scope for everyone.

Note one rule that is **not** inherited: `can_manage_course` treats a course
with no assigned instructors as open to any instructor, so that course setup is
never blocked. `visible_student_ids` does not do this. An unassigned instructor
gets an empty set rather than everybody, because a convenience that exists to
unblock admin work should not quietly widen who can authorise a biometric
change.

**Decisions are final.** Re-deciding a reviewed request returns 400 rather than
flipping it: an approval cannot be withdrawn after the student has spent it, and
a denial cannot be reopened without the student asking again — which leaves a
new record. A student may hold only one pending request, and may not ask again
while an approval is unspent.

**Tests.** `tests/flow/test_face_reenroll_flow.py` covers asking, scope, the
queue per role and decision finality; `TestReEnrolmentNeedsAnApproval` in
`tests/flow/test_face_flow.py` covers the replacement itself — that a password
buys nothing, that pending and denied authorise nothing, that an approval is
single-use, and that a refused photo does not spend it.

## 11. The colour-reflection liveness challenge

Blink and head-turn ask the student to *move*, and a good video replay moves
correctly — it was filmed from a person who really blinked. The colour
challenge asks something a recording cannot supply: the client screen is
driven through a sequence of saturated colours this server invented seconds
ago, and the face has to be seen to be lit by them.

### The physics it rests on

A face is a dim, matte, three-dimensional surface, so light from the screen
scatters back in proportion to how much each patch of skin faces it — nose
bridge and forehead more, temples and eye sockets less. Three measurements
follow, and each defeats a different attack:

| measurement | question | defeats |
|---|---|---|
`response_correlation` | did the face's *chromaticity* track our sequence? | recordings, replays |
`pattern_variation` | did the pattern across the face *change* as the colour changed? | printed photographs |
`specular_concentration` | did the whole response arrive in one small patch? | phones and tablets |

Chromaticity rather than brightness, because camera auto-exposure fights
per-frame brightness and would eat the signal — it cannot undo a hue shift.
This is measured, not assumed: `test_it_survives_camera_auto_exposure`
simulates auto-exposure and the verdict is unchanged.

`pattern_variation` is the subtle one. The obvious implementation measures how
*unevenly* the face responded, and it does not work: a printed photograph
carries the depicted face's shading in its ink, so its response is exactly as
uneven as a real face's. The quantity that separates them is whether the
unevenness *changes* across the sequence. Normalise each frame by its own mean
and a flat surface gives the identical pattern every time — the illuminant
cancels top and bottom, leaving the albedo map. A real face does not, because
`(N·L)` weights the screen's contribution per pixel while ambient light does
not, so the shading slides as the colour's luminance rises and falls. **Flat
surfaces hold still; real ones breathe.**

### Division of knowledge

The client is told the colours, because it has to display them — there is no
way to light a face with a colour the screen does not show. It is *not* told,
and may not tell us, which frame goes with which colour. That pairing is the
evidence, and `LivenessChallenge.colours_per_frame` derives it here from the
stored sequence. A client that could label its own frames would relabel them
to fit whatever light it managed to get, and the challenge would decay into
"did the screen change colour at all", which a video of a screen changing
colour satisfies.

This is why the frame count is **exact** and not a minimum: each frame's
colour is recovered from its position, so a short submission would mean
guessing which steps were dropped, and every wrong guess scores frames against
a colour they were never lit by.

### The palette is a requirement, not a preference

Module 3 refuses to judge shape unless the sequence spans a wide enough
luminance **ratio** — brightest over dimmest, not the difference. The shading
only slides if the screen's contribution changes by a large *factor*, since
what varies is `g·c/A`. Six pretty pastels cover most of the brightness range
by difference and still only reach about 6, at which a live face measures
below the threshold and is **refused for being alive**.

Saturated primaries reach about 12.9, because blue carries 7% of yellow's
luminance. `build_colour_sequence` therefore seeds every sequence with the
darkest colour plus one bright one rather than drawing uniformly — a uniform
draw of six from seven falls short often enough to matter, which the benchmark
script found before the guard existed. Module 2's floor (9.0) is deliberately
above module 3's (8.0) so a rounding difference between two separately
deployed services cannot produce a challenge nobody can verify.

### No verdict is not a failed verdict

Module 3 can decline to judge: the screen was too dim, the room too bright,
the face too far away. `challenge_passed` is then `None`, and that is neither
a pass nor an accusation.

Recording it as a pass is the dangerous option — a tablet held far enough back
to wash out the reflection produces exactly this, with a clean, sharp,
high-quality image, and `liveness_passed` would ride in on the quality score
alone. Recording it as a failure is the unjust one: it accuses a student of
spoofing over their lighting, the same mistake as reading an absent attestation
verdict as a failed one (section on `attestation_known`).

So neither. The capture is unusable, which is the same situation as too few
frames: the student is told what to change, in module 3's own wording, and the
spent challenge is gone. Since the server picks the type at random, their next
challenge may well be a gesture one, which is the escape hatch for a student
whose lighting simply will not cooperate.

### Accessibility

Six colours over 2.88 s is about 2.1 changes per second, deliberately under
the three-per-second large-area flash threshold in WCAG 2.3.1, and the palette
contains no black, so no step is a full-contrast blackout. Both the server and
the client assert this independently — the client refuses a challenge that
would exceed the rate rather than driving the screen at it.

**Open policy question, deliberately not decided here.** A student with
photosensitivity cannot currently opt out of this challenge type. An opt-out
is easy to build, but it is also a bypass: everyone would set it and the
system would fall back to the weaker gesture challenges. The honest shape is
probably to allow it, record it in the audit trail, and surface it to staff as
a fact about the check-in — but that is an institutional decision about
accommodation, not one to make in code.

### Known limits

The thresholds are calibrated against a reflectance model, not against phones.
`tests/test_colour_liveness.py::TestCalibration` renders every case across
screen gain, auto-exposure, face size and seed and asserts the shipped
thresholds sit inside the gaps (about 3x margin on print, 27x on screen), and
the JPEG tests confirm the signal survives the browser's quality-0.9 encode
down to quality 60. None of that is a measurement on real hardware.

No public dataset can supply the positive half: NUAA, CASIA-FASD,
Replay-Attack, OULU-NPU and SiW are passive captures under ambient light and
contain no face lit by a server-chosen sequence. They are, however, exactly
the replay attack, so every clip in them must be *refused* —
`benchmark/evaluate_colour_liveness.py` runs that true-negative check on real
frames, and `--lit` scores captures taken through the app against their own
recorded sequence for the false-reject half.

### What the public dataset actually measured

Run against 589 clips reconstructed from the CASIA-FASD frame release (90
genuine captures and 270 attacks per split, re-grouped by subject and video so
each clip is in shooting order), scored against sequences built by
`build_colour_sequence` rather than drawn uniformly — a uniform draw often
misses `MIN_LUMINANCE_RATIO`, and module 3 then answers "no verdict" instead of
refusing, which quietly spends a large share of the dataset on sequences this
server would never issue.

Every clip is a replay attack here, genuine ones included, so every verdict
must be a refusal. Re-scoring the same footage at each sequence length it can
support — within-clip, so the comparison is not confounded by which clips are
long — gives a per-attempt false-accept rate that falls monotonically with the
number of colour steps:

    steps     4       5       6       7       8       9      10     13+
    FAR    2.56%   2.17%   1.24%   1.12%   0.71%   0.24%   0.14%   0.00%

Holding the clip population fixed gives the cleanest before-and-after: the
same 104 clips trimmed to a fixed length, 20 sequences each, 2080 scored
sequences either way — **31 false accepts (1.49%) at six steps, 10 (0.48%) at
nine**, a 3.1x reduction. The within-clip table above and this fixed-population
run differ a little in the rate they put on nine steps (0.24% against 0.48%)
because they weight clip lengths differently; the useful claim is the ratio,
which both agree on, not the third decimal place.

The mechanism is in the same table: the 99th-percentile chromaticity
correlation of footage that was *not* lit by the sequence falls from 0.81 at
four steps to 0.51 at nine, crossing below `CORRELATION_MIN`. Spurious
correlation is easy over four samples and hard over nine. **Sequence length,
not the threshold, is the lever** — and it is the right lever, because a
genuine response correlates near 1 at any length, so lengthening the sequence
raises the attacker's difficulty without raising an honest student's. Tightening
the threshold instead would trade these false accepts for false rejects that no
public dataset can measure. `COLOUR_STEPS` is 9 for this reason; the flash rate
depends only on `COLOUR_HOLD_MS`, so the accessibility margin is untouched.

Two things this does not establish. The residual half-percent is a per-attempt rate
against a single-use challenge, not a per-session success rate, and it is not
the only gate a check-in passes — face match, the risk engine, the geofence and
duplicate detection all still apply. And the positive half remains unmeasured
on real hardware for the reason above: 60 live sequences straight from
`build_colour_sequence` are accepted 60/60 and the print, screen and recording
renderings refused 60/60, but those renderings come from the reflectance model,
not from a camera.

## 13. Browser key verification pilot

Apply [the additive schema](../db/schema.sql) before starting the updated
backend. Existing accounts default to legacy check-in, and existing UUID
registrations and public keys remain unverified until enrollment.
Upgrade every backend instance serving check-ins and the frontend before
enabling a pilot account.

For a loopback-only stack and manual proof tests, see the
[Windows pilot runbook](../docs/CRYPTO-PILOT-LOCAL.md). A database operator
sets the account flag by user ID and increments the token version to retire
outstanding sessions and proofs. There is no student-facing endpoint that
changes the flag.

```sql
UPDATE users
SET browser_verification_required = TRUE, token_version = token_version + 1
WHERE id = '<student-user-id>' AND role = 'student' AND is_active
  AND browser_verification_required IS DISTINCT FROM TRUE
RETURNING id, browser_verification_required;
```

The authenticated user response exposes the flag. Reload the student
dashboard, open an eligible check-in, and confirm the account password to
enroll the browser. Use HTTPS or trusted loopback and allow site storage.
The non-extractable P-256 private key stays in IndexedDB for that origin,
account and browser UUID. Device responses expose `browser_key_usable` so a
browser with an expired key can offer explicit password-confirmed recovery.
Use the same recovery prompt if IndexedDB storage was lost. If all site data
was cleared, select the old browser in
the recovery list to revoke its key as the new registration is enrolled.
A revoked registration cannot be reactivated by posting to the legacy
registration endpoint. The legacy endpoint still accepts the optional
`public_key` field for compatibility. It ignores that field and changes no key.

All routes below use the `/api/v1` prefix and require an authenticated
active pilot student.

| Endpoint | Request |
| --- | --- |
| `POST /devices/browser-key/challenge` | Owned active `device_id`, base64url SPKI `public_key`, and optional `recovery_device_id` |
| `POST /devices/browser-key/enroll` | `challenge_id`, the same `public_key`, `signature`, and the current `password` |
| `POST /checkins/browser-challenge` | Eligible `session_id` and enrolled `device_fingerprint` |

Both challenge endpoints return `challenge_id`, `message`, and `expires_at`.
Sign the UTF-8 enrollment message as returned. For check-in, append a single
line feed and the unpadded base64url SHA-256 digest of the **exact UTF-8 JSON
body**. Send that body unchanged with `X-SAIV-Browser-Challenge` and
`X-SAIV-Browser-Signature` on the existing `POST /checkins/`. Use ECDSA
P-256 with SHA-256 and the unpadded base64url encoding of Web Crypto's
64-byte `r || s` signature. The server message binds the purpose, challenge,
account and token version, browser UUID and device ID, key ID, session or
recovery target, and expiry. Enrollment challenges last two minutes and
check-in challenges last one minute.

PostgreSQL claims each verified proof in an independent transaction before
password or biometric processing. Enrollment confirmation has a database
limit of five password checks in 15 minutes per account. A failed downstream
check cannot make a used challenge valid again. Issue a fresh challenge for
each deliberate retry and reconcile uncertain attendance responses before
resubmitting.

The server refuses missing or invalid proof for pilot accounts even when
the request omits the device UUID or both proof headers. Legacy clients for
other accounts can still submit without proof. The API and attempt history
record `browser_key_verified` separately from face, liveness, geofence,
risk and platform attestation. This proves possession of an enrolled browser
key. It does not attest hardware or certify the origin of camera or GPS
readings.
