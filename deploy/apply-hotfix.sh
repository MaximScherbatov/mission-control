#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

echo "Checking that the hotfix sources are present..."
grep -q 'location \^~ /api/' nginx.conf
grep -q 'const ratio=1' src/App.tsx
grep -q 'Math.min(126' src/App.tsx
grep -q 'ordersLoadError' src/App.tsx
grep -q 'def _json_object' backend/app/main.py
grep -q '2026.09.19-mission-builder.1' backend/app/main.py

echo "Building fresh images..."
docker compose build --pull --no-cache api mission-control
docker compose up -d --force-recreate api mission-control

echo "Waiting for health checks..."
attempt=0
while [ "$attempt" -lt 30 ]; do
  api_state=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' citymetrics-mission-control-api-1 2>/dev/null || true)
  web_state=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' citymetrics-mission-control-mission-control-1 2>/dev/null || true)
  if [ "$api_state" = "healthy" ] && [ "$web_state" = "healthy" ]; then
    break
  fi
  attempt=$((attempt + 1))
  sleep 2
done

echo "Checking the running containers..."
docker compose exec -T api grep -q 'def _json_object' /app/app/main.py
docker compose exec -T mission-control sh -c "nginx -T 2>&1 | grep -q 'location \^~ /api/'"
docker compose exec -T mission-control sh -c "grep -R -q 'Math.min(126' /usr/share/nginx/html/assets"

echo "Release and database state:"
docker compose exec -T mission-control wget -q -O - http://mission-api:8000/api/health
echo
docker compose exec -T db psql -U mission -d mission_control -c 'SELECT number, status FROM customer_orders ORDER BY number DESC;'
docker compose ps

echo "Release 2026.09.19-mission-builder.1 is active. Refresh the browser with Ctrl+Shift+R."
