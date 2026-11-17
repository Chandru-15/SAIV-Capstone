# Running the four modules locally, infra in Docker

The application modules run on the host in their own virtualenvs; Postgres,
Redis, Prometheus and Grafana run in Docker. Verified working on Colima 0.8.1,
macOS, 2026-11-05.

## Why not just `docker compose up postgres redis prometheus grafana`

Because `grafana` drags the backend in. Compose resolves `depends_on`
transitively:

```
grafana -> metrics-exporter -> backend -> migrate, postgres, redis
```

`docker-compose.infra.yml` exists to break that: it defines only the five
shared services and nothing that depends on an application module, so no
amount of naming `grafana` can start a backend.

It sets `name: capstone`, the same project name `docker-compose.yml` derives
from this directory, so it **reuses** the existing `capstone_postgres_data`,
`capstone_grafana_data` and `capstone_prometheus_data` volumes. Your database
is not reset by switching between the two files.

## Start

```bash
docker compose -f docker-compose.infra.yml up -d
```

That gives you postgres `:5434`, redis `:6380`, prometheus `:9090`, grafana
`:3001`, mailpit `:1025`/`:8025`, and runs `db/schema.sql` once via the
`migrate` service.

Then one process per terminal tab:

```bash
./scripts/local/run.sh backend      # module 2, uvicorn :8000, --reload
./scripts/local/run.sh face         # module 3, uvicorn :8001, --reload
./scripts/local/run.sh exporter     # module 4, exporter :9101
./scripts/local/run.sh dashboard    # module 4, streamlit :8501
./scripts/local/run.sh frontend     # module 1, next dev :3000
```

## The one thing that will bite you

**Modules 2, 3 and 4 do not load their own `.env.local`.** They read
configuration with plain `os.getenv` and there is no `python-dotenv` anywhere
in the three codebases. Only module 1 (Next.js) loads `.env.local` by itself.

So running `venv/bin/uvicorn app.main:app` directly does *not* pick up
`module2-backend/.env.local`. It starts anyway, on in-code defaults, with
`SECRET_KEY`, `ALLOWED_ORIGINS` and `FACE_SERVICE_URL` unset — which looks
like it works until a browser request fails CORS or tokens turn out to be
signed with a default key. `scripts/local/run.sh` exports the file first,
which is the only reason it exists. If you'd rather not use it:

```bash
cd module2-backend && set -a && . ./.env.local && set +a && venv/bin/uvicorn app.main:app --reload
```

## Prometheus scraping host processes

Containers reach the host at `host.docker.internal` (Colima maps it to
`192.168.5.2`; verified reachable over TCP, not just resolvable). So the infra
stack mounts `module4-observability/prometheus.local.yml`, which is
`prometheus.yml` with `backend:8000` / `face-recognition:8001` /
`metrics-exporter:9101` rewritten to `host.docker.internal`. Job names are
unchanged, so the provisioned Grafana dashboards match series without edits.

Grafana still reaches Prometheus at `http://prometheus:9090` — both are in the
compose network, so that one needs no change.

Check it with:

```bash
curl -s 'http://localhost:9090/api/v1/targets?state=active' | python3 -m json.tool
```

All four jobs (`saiv-backend`, `saiv-face-recognition`, `saiv-exporter`,
`prometheus`) should read `"health": "up"`.

## Ports

| Where | Service | Port |
|---|---|---|
| host venv | module 1 frontend | 3000 |
| host venv | module 2 backend | 8000 |
| host venv | module 3 face | 8001 |
| host venv | module 4 dashboard | 8501 |
| host venv | module 4 exporter | 9101 |
| docker | postgres | 5434 |
| docker | redis | 6380 |
| docker | prometheus | 9090 |
| docker | grafana | 3001 |
| docker | mailpit SMTP | 1025 |
| docker | mailpit web UI | 8025 |

Under Colima a published container port shows up on the host as an `ssh`
listener (Colima forwards VM ports over its SSH mux), not `docker-proxy`.
`lsof -nP -iTCP:5434` naming `ssh` is normal, not a stray tunnel.

## Reading a password-reset code

`Forgot password?` emails a six-digit code. Nothing is sent to a real inbox
locally: the `mailpit` container catches it, so open
<http://localhost:8025> and read it there.

The host-run backend needs to know where to send, and it loads
`module2-backend/.env.local` rather than `.env`, so that file carries:

```
SMTP_HOST=localhost
SMTP_PORT=1025
SMTP_STARTTLS=0
```

`STARTTLS=0` because mailpit speaks plain SMTP. In the all-Docker stack the
same defaults are already in `docker-compose.yml`, pointing at the service
name `mailpit` instead of `localhost`.

With `SMTP_HOST` unset nothing is sent and no request fails: codes are still
issued and still listed for an admin under Users. If the dashboard offers a
temporary password instead of emailing one, that is this fallback.

## Switching back to the all-Docker stack

```bash
docker compose -f docker-compose.infra.yml down
```

```bash
docker compose up -d
```

Stop the host processes first, or the containers will fail to publish 8000,
8001, 8501, 9101 and 3000.

## Env files

`.env.local` is gitignored (`.env.*` with an exception for `.env.example`), so
these stay on your machine. Modules 1, 2 and 3 already had one; module 4's was
added for this workflow. Note module 4 needs `GRAFANA_INTERNAL_URL` overridden
— it defaults to `http://grafana:3000`, which only resolves inside compose.
