#!/usr/bin/env python3
"""
scripts/ai/generate_tests.py
-----------------------------
Calls OpenAI to generate all 7 QA test suites.
Key fixes:
  - Smoke/Sanity/Regression use ONLY httpx (no playwright)
  - UAT uses playwright as proper pytest test_ functions
  - Prompts include actual routes + expected responses from detection
  - Content-Type checked with .startswith() not ==
"""

import os
import json
import time
from pathlib import Path
from openai import OpenAI, RateLimitError, APIStatusError

# ── Config ─────────────────────────────────────────────────────────────────────
DEFAULT_MODEL = "gpt-4o-mini"
TEST_TYPES    = ["smoke", "sanity", "api", "regression", "uat", "load", "stress"]

SYSTEM_PROMPT = (
    "You are a senior QA automation engineer writing production-quality tests. "
    "Output ONLY raw, runnable Python or JavaScript code with zero markdown, "
    "zero fences (no ```), zero explanations, zero comments that aren't code comments. "
    "The output is written directly to a file and executed immediately."
)

# ── Prompt builder ─────────────────────────────────────────────────────────────

def build_prompt(test_type: str, language: str, framework: str,
                 project_name: str, code_sample: str,
                 changed_files: list, base_url: str) -> str:

    changed_str = "\n".join(changed_files[:10]) if changed_files else "General changes"

    # Common header injected into every prompt
    base = f"""Project: '{project_name}' | Language: {language} | Framework: {framework}
Base URL: {base_url}
Changed files: {changed_str}

Source code context:
{code_sample[:2500]}

"""

    prompts = {

        # ── SMOKE ─────────────────────────────────────────────────────────────
        # Rule: ONLY use httpx + os. NO playwright. NO browser. Fast.
        "smoke": base + f"""Write 6-8 SMOKE tests using ONLY pytest + httpx (no playwright, no browser).

RULES:
- Import only: pytest, httpx, os
- Use BASE_URL = os.getenv("BASE_URL", "{base_url}")
- Every test makes one HTTP request and checks status < 500
- Do NOT check for specific env variable names unless you see them in the code
- Do NOT import playwright or any browser library

Tests must verify:
1. GET / returns status < 500
2. GET /health returns 200 and body contains "ok" or "status"
3. At least 2 other routes from the code sample return < 500
4. POST to a create endpoint with valid JSON returns < 500
5. GET a non-existent resource returns 404

Output ONLY the complete Python test file, no markdown.""",

        # ── SANITY ────────────────────────────────────────────────────────────
        # Rule: ONLY use httpx. NO playwright.
        "sanity": base + f"""Write 8-10 SANITY tests using ONLY pytest + httpx (no playwright, no browser).

RULES:
- Import only: pytest, httpx
- BASE_URL = "{base_url}"
- Only test routes that actually exist in the source code above
- Check EXACT response bodies only if you can see the exact return value in the code
- Use response.status_code == 200 (not < 500) for known-good endpoints
- Do NOT import playwright

Tests must cover:
1. Health check returns 200 with correct JSON body (read it from code)
2. Root endpoint returns 200
3. List endpoint returns 200 and response is a dict or list
4. Get one item by ID returns 200 with correct fields (id, name, etc.)
5. Get non-existent item returns 404
6. Price/numeric fields are >= 0
7. Boolean fields have correct types

Output ONLY the complete Python test file, no markdown.""",

        # ── API ───────────────────────────────────────────────────────────────
        "api": base + f"""Write 10-14 API tests using pytest + httpx.

CRITICAL RULES:
- BASE_URL = "{base_url}"
- Only test routes that exist in the source code above
- For Content-Type: use `assert "application/json" in response.headers["content-type"]`
  (not ==, Flask appends charset)
- For error messages: read the EXACT string from the source code above
- For DELETE: Flask may return 200 with body OR 204 with no body — handle both:
  `assert response.status_code in (200, 204)`
- For response time: `assert response.elapsed.total_seconds() < 3`
- Do NOT test routes like /api/widgets or /contact that don't exist in the code

Tests must cover:
1. GET /health — 200, check JSON body matches code
2. GET main list endpoint — 200, returns dict with "items" key or list
3. GET single item — 200, check id/name/price fields exist
4. GET non-existent item — 404
5. POST create with valid body — 201, check "id" in response
6. POST create with missing required field — 400
7. PUT update existing — 200, check updated fields
8. PUT update non-existent — 404
9. DELETE existing — 200 or 204
10. DELETE non-existent — 404

Output ONLY the complete Python test file, no markdown.""",

        # ── REGRESSION ────────────────────────────────────────────────────────
        # Rule: httpx only, no playwright, only real routes
        "regression": base + f"""Write 12-15 REGRESSION tests using pytest + httpx (no playwright, no browser).

RULES:
- Import only: pytest, httpx
- BASE_URL = "{base_url}"
- Only test routes that exist in the source code
- Do NOT assume routes like /widgets, /contact, /nav that don't appear in the code
- Do NOT import playwright

Tests must cover:
1. All CRUD operations for the main resource
2. Filter/query parameters if they exist in the code
3. Boundary values (price = 0, very long name string)
4. Correct HTTP methods (PUT vs PATCH if both exist)
5. Response structure (all required fields present)
6. Error handler routes (404 response has "error" key)
7. Creating then reading back (state consistency)
8. Idempotency checks where applicable

Output ONLY the complete Python test file, no markdown.""",

        # ── UAT ───────────────────────────────────────────────────────────────
        # Rule: Playwright BUT as proper pytest test_ functions
        "uat": base + f"""Write 5-6 UAT tests using pytest + playwright (playwright.sync_api).

CRITICAL RULES:
- Each test MUST be a proper `def test_*` function that pytest can collect
- Do NOT write a standalone function called run_playwright_tests()
- Use `from playwright.sync_api import sync_playwright` inside each test
- Use headless=True (not headless=False)
- BASE_URL = "{base_url}"
- These are API/JSON endpoints, NOT a web UI — navigate to JSON endpoints, check response text
- Do NOT click on "About", "Projects", "Skills", "nav links" — there is no HTML UI
- Do NOT use page.title() == "Sample App" — it won't have that title
- Screenshots path must use os.makedirs first

Tests must cover:
1. Navigate to /health — response body contains "ok"
2. Navigate to main list endpoint — response body contains "items" or "count"
3. Navigate to /api/items/1 — body contains "Widget A"
4. Navigate to /api/items/999 — body contains "error" or status suggests 404
5. Mobile viewport (375x812) — navigate to / and check page loads

Output ONLY the complete Python test file, no markdown.""",

        # ── LOAD ──────────────────────────────────────────────────────────────
        "load": base + f"""Write a k6 LOAD test (JavaScript).

const BASE_URL = __ENV.BASE_URL || '{base_url}';

Scenario:
- Ramp up: 0 → 50 VUs over 30s
- Hold: 50 VUs for 60s
- Ramp down: 50 → 0 over 10s

Thresholds: p(95) < 500ms, http_req_failed rate < 0.01

Test these endpoints in sequence per VU iteration:
1. GET /health
2. GET /api/items
3. GET /api/items/1
4. POST /api/items with JSON body
Add sleep(Math.random() * 2 + 1) between requests.
Add check() on status codes.

Output ONLY the complete k6 JavaScript file, no markdown.""",

        # ── STRESS ────────────────────────────────────────────────────────────
        "stress": base + f"""Write a k6 STRESS test (JavaScript).

const BASE_URL = __ENV.BASE_URL || '{base_url}';

Stages:
- 0 → 50 VUs / 60s (warm-up)
- 50 → 200 VUs / 120s (stress ramp)
- 200 VUs / 180s (sustained stress)
- 200 → 500 VUs / 60s (spike)
- 500 → 0 VUs / 60s (recovery)

Allow up to 20% error rate (stress is expected to degrade).
Track p95 and p99. Add check() on status codes.
Use const BASE_URL = __ENV.BASE_URL || '{base_url}'.

Output ONLY the complete k6 JavaScript file, no markdown.""",
    }

    return prompts[test_type]


# ── File naming ────────────────────────────────────────────────────────────────

def get_filename(test_type: str, language: str, project_name: str) -> str:
    safe = project_name.lower().replace(" ", "_").replace("-", "_")
    if test_type in ("load", "stress"):
        return f"{test_type}-test.js"
    if language == "javascript":
        return f"{safe}_{test_type}.spec.ts" if test_type == "uat" else f"{safe}_{test_type}.test.ts"
    return f"test_{safe}_{test_type}.py"


# ── OpenAI caller ──────────────────────────────────────────────────────────────

def call_openai(client: OpenAI, model: str, prompt: str,
                max_tokens: int = 2500) -> str:
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
                temperature=0.15,
                max_tokens=max_tokens,
            )
            content = resp.choices[0].message.content.strip()
            # Strip accidental markdown fences
            if content.startswith("```"):
                lines   = content.split("\n")
                end     = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
                content = "\n".join(lines[1:end])
            return content

        except RateLimitError:
            wait = 5 * (2 ** attempt)
            print(f"    ⏳ Rate limited — waiting {wait}s")
            time.sleep(wait)
        except APIStatusError as e:
            if e.status_code == 429:
                time.sleep(10)
                continue
            if attempt == 2:
                raise
            time.sleep(3)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3)
    raise RuntimeError("OpenAI call failed after 3 attempts")


def estimate_cost(model: str, prompt: str, output: str) -> float:
    ti = len(prompt) // 4
    to = len(output) // 4
    rates = {
        "gpt-4o-mini": (0.00000015, 0.00000060),
        "gpt-4o":      (0.0000025,  0.0000100),
    }
    ri, ro = rates.get(model, (0.0000025, 0.0000100))
    return ti * ri + to * ro


# ── Fallback ───────────────────────────────────────────────────────────────────

def write_fallback(out_dir: Path, test_type: str,
                   language: str, project_name: str, base_url: str):
    filename = get_filename(test_type, language, project_name)
    safe     = project_name.replace("-", "_").replace(" ", "_")
    filepath = out_dir / filename

    if test_type in ("load", "stress"):
        content = f"""import http from 'k6/http';
import {{ sleep }} from 'k6';
export const options = {{ vus: 1, duration: '5s' }};
export default function () {{
  http.get(__ENV.BASE_URL || '{base_url}');
  sleep(1);
}}
"""
    elif language == "python":
        content = f"""import pytest
import httpx

BASE_URL = "{base_url}"

class Test{safe.title()}{test_type.title()}Fallback:
    def test_{test_type}_placeholder(self):
        response = httpx.get(f"{{BASE_URL}}/health")
        assert response.status_code < 500
"""
    else:
        content = f"""describe('{project_name} {test_type} (fallback)', () => {{
  test('placeholder', () => {{ expect(true).toBe(true); }});
}});
"""
    filepath.write_text(content)
    print(f"      fallback → {filepath.name}")


# ── Newman collection ──────────────────────────────────────────────────────────

def write_newman_collection(project_name: str, base_url: str):
    collection = {
        "info": {
            "name": f"{project_name} API Tests",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
        },
        "item": [
            {
                "name": "Health Check",
                "request": {
                    "method": "GET",
                    "header": [],
                    "url": {"raw": "{{BASE_URL}}/health",
                            "host": ["{{BASE_URL}}"], "path": ["health"]}
                },
                "event": [{"listen": "test", "script": {"exec": [
                    "pm.test('Status 200', () => pm.response.to.have.status(200));",
                    "pm.test('Response < 500ms', () => pm.expect(pm.response.responseTime).to.be.below(500));",
                ]}}]
            }
        ],
        "variable": [{"key": "BASE_URL", "value": base_url}]
    }
    api_dir = Path("generated-tests/api")
    api_dir.mkdir(parents=True, exist_ok=True)
    (api_dir / "collection.json").write_text(json.dumps(collection, indent=2))


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("❌  OPENAI_API_KEY is not set.")
        raise SystemExit(1)

    model         = os.environ.get("OPENAI_MODEL",   DEFAULT_MODEL)
    language      = os.environ.get("LANGUAGE",       "python")
    framework     = os.environ.get("FRAMEWORK",      "flask")
    project_name  = os.environ.get("PROJECT_NAME",   "MyProject")
    base_url      = os.environ.get("BASE_URL",        "http://localhost:3000")
    changed_files = [f for f in os.environ.get("CHANGED_FILES", "").split(",") if f]

    # Load richer data from detect_stack.py
    detection_path = Path("generated-tests/detection.json")
    detection: dict = {}
    if detection_path.exists():
        try:
            detection     = json.loads(detection_path.read_text())
            language      = detection.get("language",  language)
            framework     = detection.get("framework", framework)
            base_url      = detection.get("base_url",  base_url)
            changed_files = changed_files or detection.get("changed_files", [])
        except Exception:
            pass

    code_sample = detection.get("code_sample", "")
    if not code_sample:
        ws          = os.environ.get("GITHUB_WORKSPACE", os.getcwd())
        code_sample = _read_code_sample(ws, language)

    client = OpenAI(api_key=api_key)

    print(f"🤖 OpenAI test generation")
    print(f"   Model:    {model}")
    print(f"   Project:  {project_name}")
    print(f"   Language: {language} / {framework}")
    print(f"   Base URL: {base_url}")
    print(f"   Changed:  {len(changed_files)} files")
    print()

    generated: list   = []
    total_cost: float = 0.0

    for test_type in TEST_TYPES:
        print(f"  ⚙️  {test_type:<12}", end="  ", flush=True)

        out_dir = Path(f"generated-tests/{test_type}")
        out_dir.mkdir(parents=True, exist_ok=True)

        prompt = build_prompt(
            test_type, language, framework,
            project_name, code_sample, changed_files, base_url
        )

        try:
            content    = call_openai(client, model, prompt)
            filename   = get_filename(test_type, language, project_name)
            filepath   = out_dir / filename
            filepath.write_text(content)
            generated.append(str(filepath))
            cost        = estimate_cost(model, prompt, content)
            total_cost += cost
            print(f"✅  {filename}  (~{len(content)//4} tokens, ~${cost:.5f})")
        except Exception as e:
            print(f"⚠️   {e.__class__.__name__}: {str(e)[:60]}")
            write_fallback(out_dir, test_type, language, project_name, base_url)

        time.sleep(1.5)

    if detection.get("has_api", True):
        write_newman_collection(project_name, base_url)
        print(f"  📮  Newman collection written")

    summary = {
        "project":            project_name,
        "language":           language,
        "framework":          framework,
        "model":              model,
        "base_url":           base_url,
        "has_api":            detection.get("has_api", True),
        "has_auth":           detection.get("has_auth", False),
        "generated_files":    generated,
        "test_types":         TEST_TYPES,
        "estimated_cost_usd": round(total_cost, 5),
    }
    Path("generated-tests/summary.json").write_text(json.dumps(summary, indent=2))

    output_file = os.environ.get("GITHUB_OUTPUT", "")
    if output_file:
        with open(output_file, "a") as f:
            f.write(f"test-files={','.join(generated)}\n")

    print()
    print(f"✅  Generated {len(generated)} / {len(TEST_TYPES)} test files")
    print(f"💰  Estimated cost: ${total_cost:.5f} USD  ({model})")


def _read_code_sample(workspace: str, language: str) -> str:
    p    = Path(workspace)
    exts = {"javascript": [".ts", ".tsx", ".js"], "python": [".py"]}.get(language, [".py"])
    skip = {"node_modules", ".git", "venv", "__pycache__", "dist", "build", ".next"}
    samples = []
    for ext in exts:
        for f in sorted(p.rglob(f"*{ext}"))[:8]:
            if any(s in f.parts for s in skip):
                continue
            try:
                text = f.read_text(errors="ignore")
                if len(text) > 50:
                    samples.append(f"// ── {f.relative_to(p)} ──\n{text[:700]}")
            except Exception:
                pass
        if samples:
            break
    return "\n\n".join(samples[:4])


if __name__ == "__main__":
    main()