#!/usr/bin/env python3
"""
scripts/ai/generate_tests.py
-----------------------------
Calls OpenAI to generate all 7 QA test suites automatically.
Supports JS/TS (Jest, Playwright) and Python (pytest, playwright-python).
"""

import os
import json
import time
from pathlib import Path
from openai import OpenAI, RateLimitError, APIStatusError

# -- Configuration -------------------------------------------------------------

DEFAULT_MODEL = "gpt-4o-mini"
TEST_TYPES    = ["smoke", "sanity", "api", "regression", "uat", "load", "stress"]

SYSTEM_PROMPT = (
    "You are a senior QA automation engineer. "
    "Output ONLY raw, runnable test code - no explanations, no markdown fences "
    "(no ```), no preamble, no commentary of any kind. "
    "The output is written directly to a file and must be valid syntax."
)

# -- Prompt factory -------------------------------------------------------------

def build_prompt(test_type: str, language: str, framework: str,
                 project_name: str, code_sample: str,
                 changed_files: list, base_url: str,
                 has_api: bool, has_auth: bool) -> str:

    changed_str = "\n".join(changed_files[:10]) if changed_files else "General changes"

    lang_hint = {
        "javascript": (
            f"Use Jest for unit/integration tests. "
            f"Use @playwright/test for E2E. Framework: {framework}."
        ),
        "python": (
            f"Use pytest with pytest-json-report. "
            f"Use httpx or requests for HTTP calls. "
            f"Use playwright.sync_api for E2E. Framework: {framework}."
        ),
    }.get(language, f"Use an appropriate testing framework for {language}.")

    # Build a clear project context block so AI never guesses wrong
    auth_note = (
        "This project HAS authentication (login/register/logout flows exist)."
        if has_auth else
        "This project has NO authentication - do NOT generate login, register, "
        "logout, or session tests. There are no auth routes."
    )
    api_note = (
        f"The application DOES have a backend API. Base URL: {base_url}"
        if has_api else
        f"This is a STATIC/SPA frontend application with NO backend API server. "
        f"It is deployed at: {base_url}. "
        f"Do NOT generate supertest/server-side API tests. "
        f"Use Playwright or fetch() against the live URL for any HTTP checks."
    )

    base = f"""Project: '{project_name}'
Language: {language} | Framework: {framework}
Deployed at: {base_url}
{lang_hint}

Project context (READ CAREFULLY - do not contradict this):
- {auth_note}
- {api_note}

Changed files:
{changed_str}

Source code (for context):
{code_sample[:2000]}

"""

    prompts = {

        "smoke": base + f"""Write 6-8 SMOKE tests that verify the LIVE deployment at {base_url}:
1. Root URL {base_url}/ returns 2xx
2. At least 2-3 key page routes return non-500 responses
3. Required environment variables exist (use process.env - only ones that realistically exist)
4. No fatal import errors on main entry files

IMPORTANT: Use fetch() or @playwright/test request fixture to hit {base_url}.
Do NOT check for database connections or AWS credentials in frontend tests.
Keep each test fast (under 5 seconds). Output ONLY the complete test file.""",

        "sanity": base + f"""Write 8-12 SANITY tests focused on the CHANGED files above.
Test against: {base_url}

1. Core business logic of changed modules
2. Function input/output contracts (happy path)
3. Critical user-facing content is present on the live site
4. Boundary value checks for any changed logic
5. At least one negative/error path per changed module

Use imports that match the actual project structure visible in the source code above.
Output ONLY the complete test file.""",

        "api": base + f"""Write API tests for: {base_url}

{"IMPORTANT: This project is a static SPA with NO traditional REST API. Write HTTP-level tests using node-fetch or axios that hit the actual deployed URL. Test that pages return 200, response times are acceptable, and content-type headers are correct. Do NOT use supertest or import from '../src/app'." if not has_api else f"""Write 12-16 API tests covering all detectable endpoints.
Use: Python - pytest + httpx  |  JS - Jest + axios/node-fetch

Cover:
1. Happy path for every detectable endpoint (GET, POST, PUT, PATCH, DELETE)
2. 400 Bad Request - missing / malformed request body
3. 401 Unauthorized - missing auth token (only if auth exists)
4. 404 Not Found - non-existent resource ID
5. Response schema validation (check required fields exist)
6. Content-Type: application/json header on responses
7. Response time assertion (< 3000 ms)"""}

Output ONLY the complete test file.""",

        "regression": base + f"""Write 15-20 REGRESSION tests covering existing functionality.
Test the live site at: {base_url}

1. Every major page/section of the application (infer from code and routes)
2. Key UI components render correctly (Navbar, Footer, main sections)
3. Navigation between routes works
4. Content from source files (portfolio data, text) appears on the correct pages
5. Error handling paths (network errors, invalid routes)
6. {"Form validation and submission flows" if has_auth else "Contact form or any interactive forms"}
7. At least 2 edge case / boundary tests

{"Do NOT test login/register/logout - this project has no auth." if not has_auth else ""}
Use @testing-library/react for component tests and @playwright/test for E2E.
Output ONLY the complete test file.""",

        "uat": base + f"""Write 6-8 UAT (end-to-end) tests using Playwright.
{'JS: import { test, expect } from "@playwright/test"' if language == "javascript" else 'Python: from playwright.sync_api import sync_playwright, expect'}

BASE_URL = "{base_url}"

Simulate REALISTIC user journeys for THIS specific project (read the source code):
{"1. User registration -> login flow\n2. Authenticated user workflow" if has_auth else
 "1. Visitor lands on homepage and sees hero content\n2. Visitor browses to key sections (About, Projects, Skills)"}
3. Primary feature workflow (infer from the source code above)
4. Form interaction (submit with missing fields -> error message appears)
5. Responsive check - mobile viewport (375 x 812)
6. No console errors on main pages
{"7. Logout / session end" if has_auth else "7. All nav links navigate to correct routes"}

{"IMPORTANT: Do NOT generate register/login/logout tests - this app has no auth." if not has_auth else ""}

Add a screenshot inside each test:
{'await page.screenshot({ path: "screenshots/test-name.png" });' if language == "javascript" else 'page.screenshot(path="screenshots/test_name.png")'}

Output ONLY the complete test file.""",

        "load": base + f"""Write a k6 LOAD test script (always JavaScript for k6).

import http from 'k6/http';
import {{ sleep, check }} from 'k6';

const BASE_URL = __ENV.BASE_URL || '{base_url}';

Scenario:
- Ramp up:   0 -> 50 VUs over 30 seconds
- Hold:      50 VUs for 60 seconds
- Ramp down: 50 -> 0 VUs over 10 seconds

Requirements:
1. Test the REAL routes of this application - infer from the source code above.
   For a SPA/portfolio: test /, /about, /projects, /skills, /contact
   For an API: test actual endpoints visible in the code
2. Add think time: sleep(Math.random() * 2 + 1)
3. Thresholds: p(95) < 500ms, http_req_failed rate < 0.01
4. Add check() assertions for status 200

Output ONLY the complete k6 JavaScript file.""",

        "stress": base + f"""Write a k6 STRESS test script (always JavaScript for k6)
to find the breaking point of: {base_url}

const BASE_URL = __ENV.BASE_URL || '{base_url}';

Stages:
- Stage 1:  0 ->  50 VUs /  60s  (warm-up)
- Stage 2: 50 -> 200 VUs / 120s  (ramp stress)
- Stage 3: 200 VUs      / 180s  (sustained stress)
- Stage 4: 200 -> 500 VUs/  60s  (spike)
- Stage 5: 500 ->   0 VUs/  60s  (recovery)

Requirements:
1. Hit the most critical route of this application (infer from code)
2. Thresholds: allow up to 20% error rate (stress is expected to break things)
3. Track p95 and p99 response times across stages
4. Add check() for status codes
5. Log error rate increase with console.warn

Output ONLY the complete k6 JavaScript file.""",
    }

    return prompts[test_type]


# -- File naming ----------------------------------------------------------------

def get_filename(test_type: str, language: str, project_name: str) -> str:
    safe = project_name.lower().replace(" ", "_").replace("-", "_")
    if test_type in ("load", "stress"):
        return f"{test_type}-test.js"
    if language == "javascript":
        if test_type == "uat":
            return f"{safe}_uat.spec.ts"
        return f"{safe}_{test_type}.test.ts"
    else:
        if test_type == "uat":
            return f"test_{safe}_uat.py"
        return f"test_{safe}_{test_type}.py"


# -- OpenAI caller with retry + model fallback ----------------------------------

def call_openai(client: OpenAI, model: str,
                prompt: str, max_tokens: int = 2500) -> str:
    retries = 3
    for attempt in range(retries):
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
            if content.startswith("```"):
                lines = content.split("\n")
                end   = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
                content = "\n".join(lines[1:end])
            return content

        except RateLimitError:
            wait = 5 * (2 ** attempt)
            print(f"    ⏳ Rate limited - waiting {wait}s (attempt {attempt+1}/{retries})")
            time.sleep(wait)

        except APIStatusError as e:
            if e.status_code == 429:
                time.sleep(10)
                continue
            if attempt == retries - 1:
                raise
            time.sleep(3)

        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(3)

    raise RuntimeError(f"OpenAI call failed after {retries} attempts")


# -- Cost estimator -------------------------------------------------------------

def estimate_cost(model: str, prompt: str, output: str) -> float:
    tokens_in  = len(prompt)  // 4
    tokens_out = len(output)  // 4
    rates = {
        "gpt-4o-mini":  (0.00000015, 0.00000060),
        "gpt-4o":       (0.0000025,  0.0000100),
        "gpt-4-turbo":  (0.0000100,  0.0000300),
    }
    r_in, r_out = rates.get(model, (0.0000025, 0.0000100))
    return (tokens_in * r_in) + (tokens_out * r_out)


# -- Fallback writer ------------------------------------------------------------

def write_fallback(out_dir: Path, test_type: str,
                   language: str, project_name: str):
    filename = get_filename(test_type, language, project_name)
    filepath = out_dir / filename
    safe     = project_name.replace("-", "_").replace(" ", "_")

    if language == "python":
        content = f"""import pytest

class Test{safe.title()}{test_type.title()}Fallback:
    def test_{test_type}_placeholder(self):
        assert True
"""
    elif test_type in ("load", "stress"):
        content = f"""import http from 'k6/http';
import {{ sleep }} from 'k6';
export const options = {{ vus: 1, duration: '5s' }};
export default function () {{
  http.get(__ENV.BASE_URL || 'http://localhost:3000');
  sleep(1);
}}
"""
    else:
        content = f"""describe('{project_name} - {test_type} (fallback)', () => {{
  test('placeholder - replace with real {test_type} tests', () => {{
    expect(true).toBe(true);
  }});
}});
"""
    filepath.write_text(content)
    print(f"      fallback -> {filepath.name}")


# -- Newman collection ----------------------------------------------------------

def write_newman_collection(project_name: str, base_url: str):
    collection = {
        "info": {
            "name": f"{project_name} API Tests",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
        },
        "item": [{
            "name": "Health Check",
            "request": {
                "method": "GET",
                "header": [],
                "url": {"raw": "{{BASE_URL}}/", "host": ["{{BASE_URL}}"], "path": [""]}
            },
            "event": [{"listen": "test", "script": {"exec": [
                "pm.test('Status 2xx', () => {",
                "  pm.expect(pm.response.code).to.be.oneOf([200, 201, 204]);",
                "});",
            ]}}]
        }],
        "variable": [{"key": "BASE_URL", "value": base_url}]
    }
    api_dir = Path("generated-tests/api")
    api_dir.mkdir(parents=True, exist_ok=True)
    (api_dir / "collection.json").write_text(json.dumps(collection, indent=2))


# -- Main -----------------------------------------------------------------------

def main():
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("❌  OPENAI_API_KEY is not set.")
        raise SystemExit(1)

    model        = os.environ.get("OPENAI_MODEL",   DEFAULT_MODEL)
    language     = os.environ.get("LANGUAGE",       "javascript")
    framework    = os.environ.get("FRAMEWORK",      "node")
    project_name = os.environ.get("PROJECT_NAME",   "MyProject")
    base_url     = os.environ.get("BASE_URL",       "http://localhost:3000").rstrip("/")
    changed_files = [
        f for f in os.environ.get("CHANGED_FILES", "").split(",") if f
    ]

    # Load richer data from detect_stack.py output
    detection_path = Path("generated-tests/detection.json")
    detection: dict = {}
    if detection_path.exists():
        try:
            detection     = json.loads(detection_path.read_text())
            language      = detection.get("language",  language)
            framework     = detection.get("framework", framework)
            changed_files = changed_files or detection.get("changed_files", [])
        except Exception:
            pass

    has_api  = detection.get("has_api",  False)
    has_auth = detection.get("has_auth", False)

    # Override base_url from detection if set, but env var takes priority
    if not base_url or base_url == "http://localhost:3000":
        base_url = detection.get("base_url", base_url)

    code_sample = detection.get("code_sample", "")
    if not code_sample:
        ws          = os.environ.get("WORKSPACE", os.environ.get("GITHUB_WORKSPACE", os.getcwd()))
        code_sample = _read_code_sample(ws, language)

    client = OpenAI(api_key=api_key)

    print(f"🤖 OpenAI test generation")
    print(f"   Model:    {model}")
    print(f"   Project:  {project_name}")
    print(f"   Language: {language} / {framework}")
    print(f"   Base URL: {base_url}")
    print(f"   Has API:  {has_api}")
    print(f"   Has Auth: {has_auth}")
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
            project_name, code_sample, changed_files,
            base_url, has_api, has_auth
        )

        try:
            content = call_openai(client, model, prompt)

            filename = get_filename(test_type, language, project_name)
            filepath = out_dir / filename
            filepath.write_text(content)
            generated.append(str(filepath))

            cost = estimate_cost(model, prompt, content)
            total_cost += cost
            tokens_out  = len(content) // 4
            print(f"✅  {filepath.name}  (~{tokens_out} tokens, ~${cost:.5f})")

        except Exception as e:
            print(f"⚠️   {e.__class__.__name__}: {str(e)[:60]}")
            write_fallback(out_dir, test_type, language, project_name)

        time.sleep(1.5)

    if has_api:
        write_newman_collection(project_name, base_url)
        print(f"  📮  Newman collection -> generated-tests/api/collection.json")

    summary = {
        "project":            project_name,
        "language":           language,
        "framework":          framework,
        "model":              model,
        "base_url":           base_url,
        "has_api":            has_api,
        "has_auth":           has_auth,
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
    exts = {
        "javascript": [".ts", ".tsx", ".js", ".jsx"],
        "python":     [".py"],
    }.get(language, [".py"])
    skip = {"node_modules", ".git", "venv", "__pycache__", "dist", "build", ".next"}

    samples = []
    for ext in exts:
        for f in sorted(p.rglob(f"*{ext}"))[:8]:
            if any(s in f.parts for s in skip):
                continue
            try:
                text = f.read_text(errors="ignore")
                if len(text) > 50:
                    samples.append(text[:600])
            except Exception:
                pass
        if samples:
            break
    return "\n\n".join(samples[:4])


if __name__ == "__main__":
    main()