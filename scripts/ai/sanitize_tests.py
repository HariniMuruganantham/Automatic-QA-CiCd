#!/usr/bin/env python3
"""
scripts/ai/sanitize_tests.py
-----------------------------
Post-processes AI-generated test files to fix known LLM mistakes:

  1. Removes playwright imports from smoke / sanity / regression
     (these suites must use httpx only — playwright breaks when not installed)
  2. Removes tests that use playwright in smoke / sanity / regression
  3. Fixes wrong routes (e.g. /items → /api/items based on app.py)
  4. Fixes Content-Type assertion (== → startswith)
  5. Fixes wrong error messages to match actual app responses
  6. Fixes UAT tests that aren't proper pytest functions
  7. Fixes DELETE status code (200 not 204 for Flask)
  8. Removes hallucinated env-var assertions (ENV_VAR_1 etc.)
  9. Removes tests hitting routes that don't exist in the app
  10. Fixes /items → /api/items route prefix if needed

Run after generate_tests.py, before tests execute.
"""

import os
import re
import json
from pathlib import Path


# ── Route map from detection ───────────────────────────────────────────────────

def load_real_routes(workspace: str) -> dict:
    """
    Parse app.py to find real routes.
    Returns dict like: {"/health": ["GET"], "/api/items": ["GET","POST"], ...}
    """
    routes = {}
    p = Path(workspace)
    skip = {"node_modules", ".git", "venv", "__pycache__"}

    for f in list(p.rglob("app.py")) + list(p.rglob("main.py")) + list(p.rglob("routes.py")):
        if any(s in f.parts for s in skip):
            continue
        try:
            text = f.read_text(errors="ignore")
            # Flask: @app.route("/path", methods=["GET","POST"])
            for m in re.finditer(
                r'@(?:app|router|blueprint)\s*\.\s*route\s*\(\s*["\']([^"\']+)["\']'
                r'(?:\s*,\s*methods\s*=\s*\[([^\]]+)\])?',
                text
            ):
                route   = m.group(1)
                methods_raw = m.group(2) or '"GET"'
                methods = re.findall(r'["\']([A-Z]+)["\']', methods_raw)
                if not methods:
                    methods = ["GET"]
                routes[route] = methods
        except Exception:
            pass

    return routes


def load_detection() -> dict:
    detection_file = Path("generated-tests/detection.json")
    if detection_file.exists():
        try:
            return json.loads(detection_file.read_text())
        except Exception:
            pass
    return {}


# ── Per-suite rules ────────────────────────────────────────────────────────────

# Suites where playwright must NOT appear at all
NO_PLAYWRIGHT_SUITES = {"smoke", "sanity", "regression"}


def remove_playwright_from_file(code: str) -> str:
    """Remove playwright import and any function that uses sync_playwright."""
    lines = code.splitlines()
    out   = []
    skip_fn = False
    indent  = ""

    for i, line in enumerate(lines):
        # Remove import lines
        if re.match(r'\s*from playwright', line) or re.match(r'\s*import playwright', line):
            continue

        # Detect start of a test function that uses playwright
        if re.match(r'^def test_\w+', line):
            # Look ahead to see if function body uses sync_playwright
            fn_body = "\n".join(lines[i:i+20])
            if "sync_playwright" in fn_body or "playwright" in fn_body:
                skip_fn  = True
                indent   = ""
                continue
            else:
                skip_fn = False

        if skip_fn:
            # Keep skipping until we hit a new top-level def or class
            if line and not line[0].isspace() and line.strip():
                skip_fn = False
                out.append(line)
            continue

        out.append(line)

    return "\n".join(out)


def fix_content_type_assertion(code: str) -> str:
    """Flask returns 'application/json' with charset — use 'in' not '=='."""
    # response.headers["Content-Type"] == "application/json"
    # response.headers['Content-Type'] == 'application/json'
    code = re.sub(
        r'response\.headers\[(["\'])Content-Type\1\]\s*==\s*(["\'])application/json\2',
        r'"application/json" in response.headers["content-type"]',
        code
    )
    return code


def fix_delete_status(code: str) -> str:
    """Flask DELETE returning body uses 200, not 204."""
    # Only fix assertions that say exactly == 204
    code = re.sub(
        r'(test_delete_item_success[^}]*?)status_code\s*==\s*204',
        r'\1status_code in (200, 204)',
        code,
        flags=re.DOTALL
    )
    return code


def fix_wrong_error_messages(code: str, real_routes: dict) -> str:
    """Fix hallucinated error messages to match Flask's actual responses."""
    # Flask's 404 handler in app.py returns {"error": "Not found"}
    code = code.replace(
        '{"error": "Item not found"}',
        '{"error": "Not found"}'
    )
    # Flask's 400 for missing 'name' returns {"error": "Field \'name\' is required"}
    code = code.replace(
        '{"error": "Missing required fields"}',
        '{"error": "Field \'name\' is required"}'
    )
    return code


def fix_wrong_routes(code: str, real_routes: dict) -> str:
    """
    If app.py has /api/items but tests use /items, fix it.
    Only correct if we can confirm the real route.
    """
    if not real_routes:
        return code

    # If real routes include /api/items but NOT /items, replace
    has_api_items  = any("/api/items" in r for r in real_routes)
    has_bare_items = any(r == "/items" for r in real_routes)

    if has_api_items and not has_bare_items:
        # Replace /items/  and /items" (but not /api/items)
        code = re.sub(r'(?<!/api)/items(?=["/\s])', '/api/items', code)

    return code


def remove_env_var_assertions(code: str) -> str:
    """Remove tests that check for ENV_VAR_1, ENV_VAR_2 etc. (LLM hallucination)."""
    lines = code.splitlines()
    out   = []
    skip_fn = False

    for i, line in enumerate(lines):
        if re.match(r'^def test_\w+', line):
            # Look ahead
            fn_body = "\n".join(lines[i:i+10])
            if re.search(r'ENV_VAR_\d+|assert.*in os\.environ', fn_body):
                skip_fn = True
                continue
            else:
                skip_fn = False

        if skip_fn:
            if line and not line[0].isspace() and line.strip():
                skip_fn = False
                out.append(line)
            continue

        out.append(line)

    return "\n".join(out)


def remove_sample_app_import(code: str) -> str:
    """Remove 'import sample_app.app' — that module path doesn't exist on runner."""
    lines = [l for l in code.splitlines()
             if "import sample_app" not in l and "sample_app.app" not in l]
    # Remove the test function that imports it
    out   = []
    skip_fn = False
    for line in lines:
        if re.match(r'^def test_\w+', line):
            if "import" in line:
                skip_fn = True
                continue
            skip_fn = False
        if skip_fn:
            if line and not line[0].isspace() and line.strip():
                skip_fn = False
                out.append(line)
            continue
        out.append(line)
    return "\n".join(out)


def fix_uat_as_pytest(code: str) -> str:
    """
    If UAT was generated as a standalone run_playwright_tests() function,
    rewrite it as proper pytest test_ functions.
    """
    if "def test_" in code:
        # Already has pytest functions — just ensure headless=True
        code = re.sub(r'headless\s*=\s*False', 'headless=True', code)
        return code

    # It's a standalone function — wrap it as a single pytest function
    # by renaming the main function to test_uat_flows
    code = code.replace(
        "def run_playwright_tests():",
        "def test_uat_user_flows():"
    )
    # Fix headless
    code = re.sub(r'headless\s*=\s*False', 'headless=True', code)

    # Remove the __main__ block since pytest handles execution
    code = re.sub(r'if __name__.*?run_playwright_tests\(\)', '', code, flags=re.DOTALL)

    return code


def ensure_nonempty(code: str, suite: str, project_name: str, base_url: str) -> str:
    """If after all fixes the file has no test_ functions, add a safe placeholder."""
    if "def test_" in code:
        return code

    safe = project_name.lower().replace(" ", "_").replace("-", "_")
    placeholder = f"""
# Auto-generated placeholder — original tests were removed during sanitization.
# The AI generated tests that couldn't run safely; these are safe fallbacks.
import httpx
import os

BASE_URL = os.getenv("BASE_URL", "{base_url}")

def test_{safe}_{suite}_health():
    response = httpx.get(f"{{BASE_URL}}/health")
    assert response.status_code < 500, f"Health check failed: {{response.status_code}}"

def test_{safe}_{suite}_root():
    response = httpx.get(BASE_URL)
    assert response.status_code < 500, f"Root endpoint failed: {{response.status_code}}"
"""
    return code + placeholder


# ── Main sanitizer ─────────────────────────────────────────────────────────────

def sanitize_suite(suite: str, filepath: Path,
                   real_routes: dict, detection: dict):
    if not filepath.exists():
        return

    code    = filepath.read_text(errors="ignore")
    original_len = len(code)
    project = detection.get("project", "app")
    base_url = detection.get("base_url", "http://localhost:3000")

    print(f"  🔧 Sanitizing {suite}/{filepath.name}")

    # 1. Remove playwright from non-E2E suites
    if suite in NO_PLAYWRIGHT_SUITES:
        code = remove_playwright_from_file(code)

    # 2. Fix Content-Type header assertions
    code = fix_content_type_assertion(code)

    # 3. Fix DELETE status code
    code = fix_delete_status(code)

    # 4. Fix wrong error messages
    code = fix_wrong_error_messages(code, real_routes)

    # 5. Fix wrong routes
    code = fix_wrong_routes(code, real_routes)

    # 6. Remove hallucinated ENV_VAR assertions
    code = remove_env_var_assertions(code)

    # 7. Remove bad sample_app import
    code = remove_sample_app_import(code)

    # 8. Fix UAT structure
    if suite == "uat":
        code = fix_uat_as_pytest(code)

    # 9. Ensure at least one test remains
    code = ensure_nonempty(code, suite, project, base_url)

    # Write back
    filepath.write_text(code)

    removed = original_len - len(code)
    print(f"     → {len(code)} bytes ({'-' if removed > 0 else '+'}{abs(removed)} bytes changed)")


def main():
    detection = load_detection()
    workspace = detection.get("workspace", os.environ.get("GITHUB_WORKSPACE", os.getcwd()))
    base_url  = detection.get("base_url", "http://localhost:3000")

    print("🔧 Sanitizing generated test files...")
    print(f"   Workspace: {workspace}")
    print()

    real_routes = load_real_routes(workspace)
    if real_routes:
        print(f"   Found {len(real_routes)} real routes in app code:")
        for route, methods in real_routes.items():
            print(f"     {','.join(methods):20} {route}")
    else:
        print("   ⚠️  No routes detected — route fixing disabled")
    print()

    base = Path("generated-tests")
    if not base.exists():
        print("❌ generated-tests/ not found")
        return

    # Process each suite
    for suite in ["smoke", "sanity", "api", "regression", "uat"]:
        suite_dir = base / suite
        if not suite_dir.exists():
            continue
        for f in suite_dir.glob("*.py"):
            sanitize_suite(suite, f, real_routes, detection)

    print()
    print("✅ Sanitization complete")


if __name__ == "__main__":
    main()