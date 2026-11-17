# Module 2 → Module 3 integration

How the backend uses the face recognition service.

## Layers

```
checkin_controller / face_controller      HTTP in
        │
        ▼
FaceService            consent, persistence, audit, degraded-mode fallbacks
        │
        ▼
FaceClient             the only code that knows module 3's wire format
        │
        ▼  HTTP  (FACE_SERVICE_URL, default http://localhost:8001)
Module 3
```

## Which module 3 endpoints are used

| Module 3 endpoint | Called from | When |
|---|---|---|
| `POST /face/enroll` | `FaceService.enroll` | `POST /users/me/face/enroll` |
| `POST /face/verify` | `FaceService.verify` | `POST /users/me/face/verify`, and at check-in when the session requires a face match |
| `POST /liveness/check` | `FaceService.check_liveness` | `POST /users/me/face/liveness`, and at every check-in that includes an image |
| `GET /health` | `FaceClient.health` | `GET /users/me/face/status` |
| `POST /risk/assess` | `FaceClient.assess_risk` | Not used in the check-in decision — see below |
| `POST /face/match` | — | Module 3 marks it legacy; `/face/verify` is used instead |

## Why module 3's `/risk/assess` is not the check-in decision

The backend keeps its own `RiskService` and uses module 3 only for the two
signals it alone can produce (liveness and face match). Three reasons:

1. **It must work when module 3 is down.** Attendance cannot stop because one
   container died.
2. **Module 3 cannot see the data the decision needs** — whether the student is
   enrolled, whether the device is bound to them, where the venue is, what
   threshold this session overrides.
3. **The spec's status table is module 2's rule**, including the "GPS beyond 2×
   the geofence is an outright rejection" clause, which module 3 knows nothing
   about.

`FaceClient.assess_risk` is implemented and tested so module 4 can compare the
two scores, but no check-in depends on it.

## Trust model

A client-supplied `liveness_score` is worthless in an anti-spoofing system: the
person cheating is the one filling in the form. So when `POST /checkins/`
includes an `image`, the backend calls module 3 itself and **discards** any
`liveness_*` / `face_match_*` / `face_embedding_hash` the client sent.

The old client-supplied fields still work when no `image` is given, for test
harnesses and pre-integration callers. **They should be removed before this is
graded as a security system** — leaving them is a documented accept-the-client's-word
hole.

## Failure behaviour

| Situation | Result |
|---|---|
| No camera consent | Image never leaves the process; module 3 is not called |
| Module 3 says "No face detected" (400) | `422` with module 3's own message |
| Module 3 unreachable, during **enrolment** | `503` (the spec requires this) |
| Module 3 unreachable, during **check-in** | Check-in is **recorded as `flagged`** |

That last row is the important choice. Failing closed would lock a whole
lecture theatre out of attendance over an outage; failing open would wave
through exactly the submissions the service exists to catch. The risk engine
already treats a missing liveness signal as full risk, so the submission lands
in the instructor's review queue and a human decides.

## Privacy

* The base64 image lives only for the duration of the call. It is never
  persisted, cached, or written to a log — `test_the_image_is_never_logged`
  pins that.
* Only the 64-character SHA-256 template hash module 3 returns is stored, on
  `users.face_embedding_hash` and `checkins.face_embedding_hash`.
* The stored hash is never returned by any endpoint.
* Withdrawing camera consent (`PATCH /auth/me`) clears the template, as does
  `DELETE /users/me/face/`.

## Testing without module 3

`tests/unit/test_face_client.py` drives the client through
`httpx.MockTransport`; `tests/flow/test_face_flow.py` uses the `face_stub`
fixture from `conftest.py`. Neither needs a running module 3, so neither needs
MediaPipe. Set `behaviour["down"] = True` on the fixture to simulate an outage.

---

# Active liveness challenges (blink / head-turn)

Module 3 already verifies multi-frame challenges: `LivenessRequest` takes
`challenge_frames: List[str]` (ordered first→last, max 30), checks blink as
`open → closed → open` and head-turn as a yaw sweep ≥ 0.12, and returns
`details.challenge_passed`. Its `passed` is
`score_passed and challenge_passed is not False`, so an active challenge
cannot pass on image quality alone.

What module 2 adds is the part a stateless service cannot: **binding the
challenge to a moment**. Module 3 can confirm a blink happened in some frames;
only the backend can confirm it is the blink it just asked for. Without that,
a client picks `challenge_type` itself, records one blink, and replays it for
the rest of term.

## Flow

```
1. POST /checkins/liveness-challenge?session_id=...
   -> { challenge_id, challenge_type, instruction,
        min_frames, recommended_frames, max_frames, expires_at }

2. client performs the action, captures ordered frames

3. POST /checkins/
   { session_id, challenge_id, challenge_frames: [...], latitude, ... }
```

The challenge is:

* **server-chosen** — `blink` or `head_turn`, unpredictably, so the answer
  cannot be pre-recorded;
* **bound** to one student and one session — another student's id gives 403;
* **short-lived** — 60 seconds;
* **single-use** — consumed on submission, so captured frames cannot be
  replayed even inside the window.

`liveness_challenge_type` in the check-in body is ignored when
`challenge_frames` is present: the type comes from the issued challenge.

## Frame counts

| Challenge | Minimum | Recommended | Why |
|---|---|---|---|
| `blink` | 3 | 8 | needs open → closed → open |
| `head_turn` | 2 | 8 | needs two distinct yaw angles |

Maximum is 30 — module 3's limit, enforced in `FaceClient` too so an
over-eager client is refused before megabytes of images are shipped.

## Errors

| Situation | Response |
|---|---|
| `challenge_frames` without `challenge_id` | 400 |
| unknown / expired / already-used challenge | 400, one message for all three |
| challenge issued to another student or session | 403 |
| fewer than `min_frames` | 422 |
| more than 30 frames | 422 |
| no camera consent | 400, and no image leaves the process |
| challenge not completed | 201, `status: rejected`, `liveness_score: 0.0` |
| module 3 unreachable | 201, `status: flagged` for human review |

The last two matter. A challenge the student didn't complete is a **hard**
liveness failure regardless of image quality — otherwise "didn't blink" would
pass on a well-lit photo. An outage still flags rather than blocking, so a
container restart doesn't lock a lecture theatre out of attendance.

## Pre-flight

`POST /users/me/face/liveness-frames` runs a multi-frame check with no bound
challenge, so the frontend can confirm its capture loop, framing and frame
count produce a pass before a real check-in depends on it. Its result carries
no weight at check-in.

## Storage

Challenges live in Redis (`REDIS_URL`, already in docker-compose) with TTL
expiry, consumed via `GETDEL` so two concurrent submissions cannot both
succeed. Without Redis it falls back to an in-process dict and logs a warning
— correct for one worker, **not** safe across several.
