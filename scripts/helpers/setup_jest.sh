#!/usr/bin/env bash
# Installs Jest globally and writes a default config if none exists.
# Usage: setup_jest.sh [project-dir]

BASE="${1:-}"

if [ -n "$BASE" ] && [ -f "$BASE/package.json" ]; then
  echo "Installing project dependencies from $BASE/package.json"
  cd "$BASE"
  { npm ci 2>/dev/null || npm install; } || true
  cd - > /dev/null
fi

npm install -g jest ts-jest typescript @types/jest

if [ ! -f jest.config.js ] && [ ! -f jest.config.ts ] && [ ! -f jest.config.json ]; then
  cat > jest.config.js << 'EOF'
module.exports = {
  testEnvironment: "node",
  transform: { "^.+\\.tsx?$": ["ts-jest", { tsconfig: { strict: false } }] },
  testMatch: ["**/*.test.ts","**/*.test.js","**/*.spec.ts","**/*.spec.js"],
  moduleFileExtensions: ["ts","tsx","js","jsx","json"],
};
EOF
  echo "Wrote default jest.config.js"
fi
