#!/usr/bin/env bash
# Run one application module on the host, against infra in Docker.
#
#   scripts/local/run.sh backend | face | exporter | dashboard | frontend
#
# Why this exists rather than a bare uvicorn command: modules 2, 3 and 4 read
# configuration with plain os.getenv and load no dotenv file, so their
# .env.local is NOT picked up automatically the way Next.js picks up module
# 1's. Running uvicorn directly silently falls back to in-code defaults -- for
# the backend that means DATABASE_URL pointing at localhost:5434 (right by
# luck) but SECRET_KEY, ALLOWED_ORIGINS and FACE_SERVICE_URL all unset. This
# script exports the file first, so what you run matches what you configured.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# Export every assignment in a module's .env.local, if it has one.
load_env() {
    local file="$1"
    [ -f "$file" ] || { printf 'note: %s not found, using in-code defaults\n' "$file" >&2; return; }
    set -a
    # shellcheck disable=SC1090
    . "$file"
    set +a
}

need_venv() {
    [ -x "$1/venv/bin/python" ] || die "no virtualenv at $1/venv -- create it with:
  python3 -m venv $1/venv && $1/venv/bin/pip install -r $1/requirements.txt"
}

case "${1:-}" in
  backend)
    cd "$ROOT/module2-backend"
    need_venv .
    load_env .env.local
    # --no-proxy-headers: uvicorn would otherwise rewrite the peer address
    # from X-Forwarded-For before the app sees it. See API-NOTES.md section 8.
    exec venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload \
        --no-proxy-headers
    ;;
  face)
    cd "$ROOT/module3-face-recognition"
    need_venv .
    load_env .env.local
    exec venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
    ;;
  exporter)
    cd "$ROOT/module4-observability"
    need_venv .
    load_env .env.local
    exec venv/bin/python exporter.py
    ;;
  dashboard)
    cd "$ROOT/module4-observability"
    need_venv .
    load_env .env.local
    exec venv/bin/streamlit run app/main.py --server.port=8501 --server.address=0.0.0.0
    ;;
  frontend)
    cd "$ROOT/module1-frontend"
    [ -d node_modules ] || die "no node_modules -- run: npm install"
    # Next.js loads .env.local itself; nothing to export here.
    exec npm run dev
    ;;
  ""|-h|--help)
    cat <<'USAGE'
usage: scripts/local/run.sh <target>

  backend     module 2, uvicorn :8000  (--reload)
  face        module 3, uvicorn :8001  (--reload)
  exporter    module 4, prometheus exporter :9101
  dashboard   module 4, streamlit :8501
  frontend    module 1, next dev :3000

Start the infra first:  docker compose -f docker-compose.infra.yml up -d
USAGE
    ;;
  *)
    die "unknown target '$1' -- try: backend | face | exporter | dashboard | frontend"
    ;;
esac
