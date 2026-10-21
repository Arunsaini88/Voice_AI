#!/bin/sh
# Apply pending migrations, then serve. PORT is honoured for platforms that
# inject one (Render, Railway, Fly). Proxy headers are only trusted when the
# app is told it sits behind a proxy, so X-Forwarded-For can't be spoofed on a
# direct deployment.
set -eu

echo "migrating database to head..."
alembic upgrade head

EXTRA=""
if [ "${TRUST_PROXY_HEADERS:-false}" = "true" ]; then
  EXTRA="--proxy-headers --forwarded-allow-ips=*"
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers "${WEB_CONCURRENCY:-1}" $EXTRA
