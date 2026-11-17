# SAIV — Secure Attendance & Identity Verification

**NTU SC3099 Capstone Project · Group 33**

SAIV is a full-stack attendance verification system designed to make classroom check-ins more trustworthy while reducing manual attendance administration. It combines student authentication, browser registration, face recognition, active liveness checks, geolocation, risk-based attendance decisions, and a staff dashboard in one Docker-based application.

> **Project status:** Final coursework submission. This repository is a snapshot of the submitted system, not a production-hardened identity-verification service.

## Overview

A student signs in to the web application, gives the required camera and location consents, enrols their face, and registers a browser. During an open attendance session, SAIV captures the required evidence and submits it to the backend. The backend checks the session rules, face match, liveness, location and device signals before returning an attendance outcome. Instructors and administrators can manage sessions and review attendance through the staff portal.

The normal student attendance challenge in this submission uses **randomized screen-light/colour reflection together with a blink**. Head-pose detection exists in the face service but **standalone head-turn or head-tilt evidence does not authorize an attendance check-in**.

## Key features

### Student experience — Module 1

- Student registration, login, password recovery and protected pages.
- Dashboard with enrolled courses, active sessions, check-in status and attendance history.
- Camera and geolocation permission handling with explicit, backend-recorded consent.
- Face enrolment, browser registration and a guided check-in flow.
- Active liveness capture combining a screen-light challenge with an **open → closed → open** blink sequence.
- Geolocation/geofencing, QR scanning support and clear attendance results.
- Responsive **Next.js** Progressive Web App with offline handling for supported static resources.

### Attendance and security — Module 2

- **FastAPI** REST API for authentication, users, courses, enrolments, sessions and check-ins.
- Role-based access for **student, TA, instructor and admin** accounts.
- JWT-based sessions, password hashing, input validation and Redis-backed rate limiting.
- Risk-based attendance outcomes (**approved, flagged or rejected**) using face, liveness, location, device and session evidence.
- Consent enforcement, unknown-browser flagging, replay protections and audit records.
- Optional **cryptographic browser verification** for enabled student accounts: a browser stores a non-extractable **ECDSA P-256** signing key and proves possession by signing a server-issued, single-use challenge. This is **browser-key possession**, not hardware-backed device attestation.

### Face recognition — Module 3

- Face enrolment and one-to-one matching using an ONNX face embedding model.
- MediaPipe-based face landmarks and liveness-related signals.
- Active colour-reflection and blink verification, with face identity checks across submitted evidence.
- Anti-spoofing checks intended to reduce straightforward still-photo and replay abuse.
- Raw face frames are processed for verification rather than stored as attendance images.

### Staff dashboard and monitoring — Module 4

- **Streamlit** portal for role-scoped course, session, attendance and administrative workflows.
- Attendance review, face re-enrolment requests, reporting and export capabilities.
- **Prometheus** metrics, **Grafana** dashboards and a metrics exporter for service monitoring.

## Architecture

```text
Student browser / PWA (Next.js)
        │
        │ REST /api/v1
        ▼
Backend API (FastAPI) ────────── PostgreSQL
        │                             │
        ├────────────── Redis         └── Attendance, users, sessions,
        │                                  device records and audit events
        ▼
Face recognition service (FastAPI + MediaPipe + ONNX Runtime)

Staff browser ───── Streamlit staff dashboard ───── Backend API

Prometheus ← Backend / Face Service / Metrics Exporter → Grafana
Mailpit ← Local password-reset email
```

| Module | Directory | Main technologies |
|---|---|---|
| Student PWA | [`module1-frontend/`](module1-frontend/) | Next.js 14, React, TypeScript, Tailwind CSS, browser camera/geolocation/Web Crypto APIs |
| Backend API | [`module2-backend/`](module2-backend/) | Python, FastAPI, SQLAlchemy, PostgreSQL, Redis |
| Face recognition | [`module3-face-recognition/`](module3-face-recognition/) | Python, MediaPipe, OpenCV, ONNX Runtime |
| Staff dashboard and observability | [`module4-observability/`](module4-observability/) | Streamlit, Prometheus, Grafana |

## Getting started

### Requirements

- **Docker Desktop** with Docker Compose (recommended for the complete application).
- Git.
- For running the frontend outside Docker: **Node.js 22.x** (as specified in [`module1-frontend/package.json`](module1-frontend/package.json)).
- For local Python tests: a compatible Python environment and dependencies from [`requirements-test.txt`](requirements-test.txt).

### Run the application with Docker

> **Use a fresh, disposable database for demonstrations and tests.** The root Compose file uses fixed container names and published ports, so do not start it alongside another SAIV stack that already occupies those names or ports. The supplied configuration is intended for local coursework use, **not public internet exposure**.

From PowerShell:

```powershell
git clone https://github.com/Chandru-15/SAIV-Capstone.git
cd SAIV-Capstone

Copy-Item .env.example .env
# Edit .env: set unique POSTGRES_PASSWORD, SECRET_KEY and GF_ADMIN_PASSWORD
# Generate a signing secret locally with:
python -c "import secrets; print(secrets.token_urlsafe(48))"

docker compose config --quiet
docker compose up -d --build
docker compose ps
```

The `migrate` service applies [`db/schema.sql`](db/schema.sql) after PostgreSQL becomes healthy. Confirm it completed successfully:

```powershell
docker compose logs --tail=100 migrate
docker compose logs --tail=100 backend face-recognition frontend dashboard
```

### Local service URLs

| Service | URL |
|---|---|
| Student frontend | http://localhost:3000 |
| Backend API / Swagger | http://localhost:8000/docs |
| Backend health | http://localhost:8000/health |
| Face-recognition API / Swagger | http://localhost:8001/docs |
| Face-service health | http://localhost:8001/health |
| Staff dashboard | http://localhost:8501 |
| Mailpit (local password-reset messages) | http://localhost:8025 |
| Grafana | http://localhost:3001 |
| Prometheus | http://localhost:9090 |

The frontend and staff portal use separate sign-in flows. You need suitable accounts, course enrolments, and an open attendance session to run a complete check-in. Do not run destructive demo-seeding utilities against a database containing data you want to keep.

To stop the stack **without deleting its volumes**:

```powershell
docker compose down
```

For additional workflows, see [`LOCAL-DEV.md`](LOCAL-DEV.md), [`docs/API-SPECIFICATION.md`](docs/API-SPECIFICATION.md), and [`docs/CRYPTO-PILOT-LOCAL.md`](docs/CRYPTO-PILOT-LOCAL.md).

## End-to-end walkthrough

1. **Staff setup:** Sign in with an appropriate staff account, create a course, assign staff, enrol a student, and open an attendance session with the desired venue and policies.
2. **Student setup:** Register or sign in, enable camera and geolocation consents, enrol a face, and register the current browser.
3. **Browser-key setup (when required):** Confirm the account password and enrol the browser signing key.
4. **Check in:** Open the session, allow camera/location access, follow the screen-light and blink instructions, and submit the evidence.
5. **Review:** Read the approved/flagged/rejected result and inspect the corresponding attendance record through the staff portal.

## Testing

Public tests, module-specific tests, and frontend checks are included in the repository. **Public HTTP tests create data and should run only against a disposable test stack.** Results from a previous development revision should not be assumed to apply to an untested checkout.

```powershell
# From the repository root, with isolated test services running:
python -m pip install -r requirements-test.txt
python -m pytest tests/public -v

# Frontend-only checks:
cd module1-frontend
npm ci
npm test
npm run typecheck
npm run lint
npm run build
```

See the test suite and module directories for additional backend and face-service checks. Automated checks supplement, rather than replace, a real-camera test of face matching and liveness.

## Project files and submission material

- [API specification](docs/API-SPECIFICATION.md)
- [Security requirements](docs/SECURITY-REQUIREMENTS.md)
- [Recommended database design](docs/recommended_design/DATABASE-SCHEMA.md)
- [Group 33 presentation slides](docs/SC3099%20Capstone%20Presentation%20Slides%20Group%2033.pdf)
- [Local development notes](LOCAL-DEV.md)

**Demo video:** The original coursework submission included a recording, but it is **not in this Git repository** because browser session information appeared in the recording. A redacted version may be shared separately after review. Do not follow a link to the excluded MP4.

## Security and deployment limitations

- This is a coursework implementation. The default Docker Compose configuration publishes internal service ports and contains **development fallback credentials**; set unique secrets, restrict network access, and review the deployment before exposing it beyond localhost.
- Browser-key verification confirms that the browser can use its registered signing key; it **does not attest to a physical device's hardware identity**.
- Active-liveness protections reduce common photo attacks but do not guarantee resistance to prepared video, injected camera frames, or sophisticated replay attacks.
- The staff portal's session-link handling requires further hardening for public deployment. The included Next.js version should also be upgraded and security-reviewed before internet-facing use.
- The supplied face recognition model is documented as **non-commercial research use**; see [`module3-face-recognition/models/README.md`](module3-face-recognition/models/README.md) before reuse.
- Keep passwords, API keys, biometric media and live session tokens out of Git history.

## Project context

Developed by **Group 33** for **NTU SC3099 Capstone Project**. The repository preserves the team's final submitted application across its four integrated modules.

