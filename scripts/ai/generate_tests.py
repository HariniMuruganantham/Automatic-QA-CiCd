#!/usr/bin/env python3
"""
generate_tests.py
Uses OpenAI API to generate tests for all 7 QA types.
Supports JS/TS and Python projects.
Models: gpt-4o-mini (default, cheapest), gpt-4o, gpt-4-turbo
"""

import os
import json
import time
from pathlib import Path
from openai import OpenAI, RateLimitError, APIStatusError

# ── Config ────────────────────────────────────────────────────────────────────

# Model priority: try primary, fall back to mini if rate-limited
PRIMARY_MODEL   = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
FALLBACK_MODEL  = "gpt-4o-mini"

TEST_TYPES = ["smoke", "sanity", "api", "regression", "uat", "load", "stress"]

SYSTEM_PROMPT = """You are a senior QA engineer. Output ONLY raw test code — no explanations,
no markdown code fences, no preamble, no commentary. The output is written directly to a file."""

# ── Prompt builder ────────────────────────────────────────────────────────────

def build_prompt(test_type: str, language: str, framework: str,
                 project_name: str, code_sample: str, changed_files: list) -> str:

    changed_str = "\n".join(changed_files[:10]) if changed_files else "General changes"

    lang_hint = {
        "javascript": f"Use Jest for unit/integration, Playwright for E2E. Framework: {framework}.",
        "python":     f"Use pytest for unit/integration, playwright-python for E2E. Framework: {framework}.",
    }.get(language, f"Use appropriate framework for {language}.")

    base = f"""Project: '{project_name}' | Language: {language} | Framework: {framework}
{lang_hint}
Changed files:
{changed_str}

Source code context:
{code_sample[:2000]}

"""

    prompts = {

        "smoke": base + """Generate 6-8 SMOKE tests that verify:
1. Application starts and the health/root endpoint returns 2xx
2. Critical routes return non-500 status codes
3. Database / external service connections succeed
4. Required environment variables are present
5. No fatal import / module errors

Output ONLY the complete test file.""",

        "sanity": base + """Generate 8-12 SANITY tests focused on the CHANGED files listed above:
1. Core business logic of the changed modules
2. Function input/output contracts
3. Critical user-facing flows that touch the changes
4. No obvious regressions in changed areas
5. Boundary value checks for changed logic

Output ONLY the complete test file.""",

        "api": base + """Generate 12-16 API tests using:
- Python: pytest + httpx  |  JavaScript: Jest + supertest

Cover:
1. All detectable endpoints (GET, POST, PUT, PATCH, DELETE)
2. Happy path for each endpoint
3. 400 Bad Request — missing/invalid body
4. 401/403 — missing or invalid auth token
5. 404 — non-existent resource
6. Response body schema validation
7. Pagination / filtering parameters if applicable
8. Content-Type header validation

Output ONLY the complete test file.""",

        "regression": base + """Generate 15-20 REGRESSION tests that protect existing functionality:
1. Every major feature of the application
2. Edge cases and boundary conditions
3. Data transformation / calculation correctness
4. Integration between modules
5. Error handling paths
6. Previously common failure points

Output ONLY the complete test file.""",

        "uat": base + """Generate 6-8 UAT (User Acceptance) end-to-end tests using Playwright.
{'JavaScript: use @playwright/test  |  Python: use playwright.sync_api'}

Cover realistic user journeys:
1. User registration and login flow
2. Primary feature workflow (infer from code)
3. Create → Read → Update → Delete flow for main entity
4. Form validation feedback to user
5. Error recovery / retry scenario
6. Responsive layout check (mobile viewport 375×812)

Include page.screenshot() call inside each test's catch block.
Output ONLY the complete test file.""",

        "load": base + """Generate a k6 LOAD test script (JavaScript):

Scenario:
- Ramp up: 0 → 50 VUs over 30 seconds
- Sustained load: 50 VUs for 60 seconds
- Ramp down: 50 → 0 VUs over 10 seconds

Requirements:
1. Test all main API endpoints in sequence
2. Use realistic think time: sleep(Math.random() * 2 + 1)
3. Thresholds: http_req_duration p(95) < 500, http_req_failed rate < 0.01
4. Use __ENV.BASE_URL with fallback to http://localhost:3000
5. Add checks for status codes and response time

Output ONLY the complete k6 script.""",

        "stress": base + """Generate a k6 STRESS test script (JavaScript) to find the breaking point:

Scenario:
- Stage 1: Ramp  0 →  50 VUs over 1 min  (warm up)
- Stage 2: Ramp 50 → 200 VUs over 2 min  (ramp stress)
- Stage 3: Hold 200 VUs for 3 min         (sustained stress)
- Stage 4: Spike to 500 VUs for 1 min     (peak spike)
- Stage 5: Drop back to 0 over 1 min      (recovery check)

Requirements:
1. Monitor error rate at each stage
2. Track http_req_duration degradation
3. Thresholds: failure if error rate > 20% (stress allows some failure)
4. Log VU count alongside response times
5. Use __ENV.BASE_URL with fallback

Output ONLY the complete k6 script.""",
    }

    return prompts[test_type]


# ── File naming ────────────────────────────────────────────────────────────────

def get_filename(test_type: str, language: str, project_name: str) -> str:
    safe = project_name.lower().replace(" ", "_").replace("-", "_")
    if test_type in ("load", "stress"):
        return f"{test_type}-test.js"          # k6 is always JS
    if test_type == "uat":
        return f"{safe}_uat.spec.ts" if language == "javascript" else f"{safe}_uat_test.py"
    if language == "javascript":
        return f"{safe}_{test_type}.test.ts"
    return f"test_{safe}_{test_type}.py"


# ── OpenAI caller with retry + fallback ───────────────────────────────────────

def call_openai(client: OpenAI, model: str, system: str, user: str,
                max_tokens: int = 2500, retries: int = 3) -> str:
    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                temperature=0.15,
                max_tokens=max_tokens,
            )
            content = response.choices[0].message.content.strip()

            # Strip accidental markdown fences
            if content.startswith("```"):
                lines = content.split("\n")
                start = 1
                end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
                content = "\n".join(lines[start:end])

            return content

        except RateLimitError:
            wait = 2 ** attempt * 5   # 5s, 10s, 20s
            print(f"    ⚠️  Rate limited — waiting {wait}s (attempt {attempt+1}/{retries})")
            time.sleep(wait)

        except APIStatusError as e:
            if e.status_code == 429:
                time.sleep(10)
                continue
            raise

        except Exception as e:
            if attempt < retries - 1:
                time.sleep(3)
                continue
            raise

    raise RuntimeError(f"OpenAI call failed after {retries} attempts")


# ── Fallback test writer ───────────────────────────────────────────────────────

def write_fallback(out_dir: Path, test_type: str, language: str, project_name: str):
    filename = get_filename(test_type, language, project_name)
    filepath = out_dir / filename
    safe = project_name.replace("-", "_").replace(" ", "_")

    if language == "python":
        content = f"""import pytest

class Test{safe.title()}{test_type.title()}Fallback:
    \"\"\"Fallback {test_type} tests — AI generation failed. Add real tests here.\"\"\"

    def test_{test_type}_placeholder(self):
        \"\"\"TODO: Replace with real {test_type} test for {project_name}.\"\"\"
        assert True
"""
    else:
        content = f"""// Fallback {test_type} tests — AI generation failed. Add real tests here.
describe('{project_name} — {test_type} (fallback)', () => {{
  test('placeholder: replace with real {test_type} tests', () => {{
    // TODO: Add real {test_type} tests for {project_name}
    expect(true).toBe(true);
  }});
}});
"""
    filepath.write_text(content)
    print(f"    → fallback written: {filepath}")


def generate_newman_collection(project_name: str):
    """Minimal Postman/Newman collection for API stage."""
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
                    "url": {
                        "raw": "{{BASE_URL}}/health",
                        "host": ["{{BASE_URL}}"],
                        "path": ["health"]
                    }
                },
                "event": [{
                    "listen": "test",
                    "script": {"exec": [
                        "pm.test('Status 200', () => pm.response.to.have.status(200));",
                        "pm.test('Response < 500ms', () => pm.expect(pm.response.responseTime).to.be.below(500));"
                    ]}
                }]
            }
        ],
        "variable": [{"key": "BASE_URL", "value": "http://localhost:3000"}]
    }
    api_dir = Path("generated-tests/api")
    api_dir.mkdir(parents=True, exist_ok=True)
    (api_dir / "collection.json").write_text(json.dumps(collection, indent=2))


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("❌  OPENAI_API_KEY not set")
        raise SystemExit(1)

    model        = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    language     = os.environ.get("LANGUAGE", "javascript")
    framework    = os.environ.get("FRAMEWORK", "node")
    project_name = os.environ.get("PROJECT_NAME", "MyProject")
    changed_files = [f for f in os.environ.get("CHANGED_FILES", "").split(",") if f]

    # Load richer detection data written by detect_stack.py
    detection_file = Path("generated-tests/detection.json")
    detection: dict = {}
    if detection_file.exists():
        detection = json.loads(detection_file.read_text())
        language  = detection.get("language", language)
        framework = detection.get("framework", framework)
        if not changed_files:
            changed_files = detection.get("changed_files", [])

    code_sample = detection.get("code_sample", "")
    if not code_sample:
        ws = os.environ.get("GITHUB_WORKSPACE", os.getcwd())
        code_sample = _read_code_sample(ws, language)

    client = OpenAI(api_key=api_key)

    print(f"🤖 Generating tests with OpenAI ({model})")
    print(f"   Project:   {project_name}")
    print(f"   Language:  {language} / {framework}")
    print(f"   Changed:   {len(changed_files)} files")
    print()

    generated_files: list[str] = []
    cost_estimate = 0.0

    for test_type in TEST_TYPES:
        print(f"  ⚙️  {test_type:<12}", end=" ", flush=True)

        out_dir = Path(f"generated-tests/{test_type}")
        out_dir.mkdir(parents=True, exist_ok=True)

        prompt = build_prompt(test_type, language, framework,
                              project_name, code_sample, changed_files)

        try:
            content = call_openai(client, model, SYSTEM_PROMPT, prompt)

            filename = get_filename(test_type, language, project_name)
            filepath = out_dir / filename
            filepath.write_text(content)
            generated_files.append(str(filepath))

            # Rough token-based cost estimate (gpt-4o-mini: $0.15/1M in, $0.60/1M out)
            tokens_in  = len(prompt) // 4
            tokens_out = len(content) // 4
            if "mini" in model:
                cost_estimate += (tokens_in * 0.00000015) + (tokens_out * 0.00000060)
            elif "4o" in model:
                cost_estimate += (tokens_in * 0.0000025)  + (tokens_out * 0.0000100)

            print(f"✅  → {filepath.name}  (~{tokens_out} tokens)")

        except Exception as e:
            print(f"⚠️   Failed ({e.__class__.__name__}: {str(e)[:60]})")
            write_fallback(out_dir, test_type, language, project_name)

        # Avoid hitting rate limits — 1s gap between calls
        time.sleep(1.2)

    # Newman collection for the API stage
    if detection.get("has_api", True):
        generate_newman_collection(project_name)
        print(f"  📮  Newman collection written")

    # Summary JSON
    summary = {
        "project":         project_name,
        "language":        language,
        "framework":       framework,
        "model":           model,
        "generated_files": generated_files,
        "test_types":      TEST_TYPES,
        "estimated_cost_usd": round(cost_estimate, 5),
    }
    Path("generated-tests/summary.json").write_text(json.dumps(summary, indent=2))

    # GitHub Actions output
    output_file = os.environ.get("GITHUB_OUTPUT", "/dev/null")
    with open(output_file, "a") as f:
        f.write(f"test-files={','.join(generated_files)}\n")

    print(f"\n✅  Generated {len(generated_files)}/{len(TEST_TYPES)} test files")
    print(f"💰  Estimated cost: ${cost_estimate:.5f} USD ({model})")


def _read_code_sample(workspace: str, language: str) -> str:
    p = Path(workspace)
    exts = {"javascript": [".ts", ".js", ".tsx"], "python": [".py"]}.get(language, [".py"])
    skip = ["node_modules", ".git", "venv", "__pycache__", "dist", "build", ".next"]
    samples = []
    for ext in exts:
        for f in list(p.rglob(f"*{ext}"))[:6]:
            if any(s in str(f) for s in skip):
                continue
            try:
                samples.append(f.read_text(errors="ignore")[:600])
            except Exception:
                pass
        if samples:
            break
    return "\n\n".join(samples[:4])


if __name__ == "__main__":
    main()
