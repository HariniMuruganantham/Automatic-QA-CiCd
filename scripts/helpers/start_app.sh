#!/usr/bin/env bash
# Starts the project application and polls until it responds.
# Uses if/elif to ensure only one server starts.
# Usage: start_app.sh <project-dir> <base-url>

BASE="${1:?Usage: start_app.sh <project-dir> <base-url>}"
BASE_URL="${2:-http://localhost:3000}"

echo "▶ Starting application from: $BASE"
echo "  Expected URL: $BASE_URL"

if [ -f "$BASE/package.json" ]; then
  echo "  Detected Node.js project"
  cd "$BASE"
  { npm ci 2>/dev/null || npm install; } || true
  npm start &

elif [ -f "$BASE/manage.py" ]; then
  echo "  Detected Django project"
  pip install --break-system-packages \
    $([ -f "$BASE/requirements.txt" ] && echo "-r $BASE/requirements.txt" || echo "django") 2>/dev/null || true
  cd "$BASE" && python manage.py runserver &

elif [ -f "$BASE/app.py" ]; then
  echo "  Detected Python app (app.py)"
  pip install --break-system-packages \
    $([ -f "$BASE/requirements.txt" ] && echo "-r $BASE/requirements.txt" || echo "flask fastapi uvicorn") 2>/dev/null || true
  cd "$BASE" && python app.py &

elif [ -f "$BASE/main.py" ]; then
  echo "  Detected Python app (main.py)"
  pip install --break-system-packages \
    $([ -f "$BASE/requirements.txt" ] && echo "-r $BASE/requirements.txt" || echo "") 2>/dev/null || true
  cd "$BASE" && python main.py &

else
  echo "  ⚠ No recognized entry point found"
  exit 0
fi

echo "  Polling for readiness..."
for i in $(seq 1 30); do
  if curl -sf "$BASE_URL" > /dev/null 2>&1; then
    echo "  ✓ App ready after ${i}s"
    exit 0
  fi
  sleep 1
done
echo "  ⚠ App may not be ready after 30s (continuing anyway)"
