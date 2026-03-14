#!/usr/bin/env python3
"""
scripts/detect_stack.py
-----------------------
Auto-detects the project language, framework, and changed files.
Writes GitHub Actions step outputs and saves detection.json for the AI step.
"""

import os
import json
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def set_output(name: str, value: str):
    output_file = os.environ.get("GITHUB_OUTPUT", "")
    line = f"{name}={value}\n"
    if output_file:
        with open(output_file, "a") as f:
            f.write(line)
    print(f"  -> {name}={value}")


SKIP_DIRS = {"node_modules", ".git", "venv", "__pycache__",
             "dist", "build", ".next", ".nuxt", "coverage",
             "scripts", "qa-platform"}


def count_source_files(workspace: str, extensions: list) -> int:
    p = Path(workspace)
    count = 0
    for ext in extensions:
        for f in p.rglob(f"*{ext}"):
            if not any(s in f.parts for s in SKIP_DIRS):
                count += 1
    return count


def detect_language(workspace: str) -> tuple:
    p = Path(workspace)
    has_package_json  = (p / "package.json").exists()
    has_requirements  = (p / "requirements.txt").exists()
    has_pyproject     = (p / "pyproject.toml").exists()
    has_python_marker = has_requirements or has_pyproject

    if has_package_json and has_python_marker:
        py_count = count_source_files(workspace, [".py"])
        js_count = count_source_files(workspace, [".js", ".ts", ".jsx", ".tsx"])
        print(f"   Both markers found -- py={py_count} js={js_count}")
        use_python = py_count > js_count
    else:
        use_python = has_python_marker and not has_package_json

    if has_package_json and not use_python:
        try:
            pkg = json.loads((p / "package.json").read_text())
        except Exception:
            pkg = {}
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        if "next"    in deps: return "javascript", "nextjs"
        if "express" in deps: return "javascript", "express"
        if "fastify" in deps: return "javascript", "fastify"
        if "react"   in deps: return "javascript", "react"
        if "koa"     in deps: return "javascript", "koa"
        if "hapi"    in deps: return "javascript", "hapi"
        return "javascript", "node"

    if has_python_marker or use_python:
        req_path = p / "requirements.txt"
        reqs = req_path.read_text().lower() if req_path.exists() else ""
        if "django"  in reqs: return "python", "django"
        if "fastapi" in reqs: return "python", "fastapi"
        if "flask"   in reqs: return "python", "flask"
        if "tornado" in reqs: return "python", "tornado"
        return "python", "python"

    if (p / "pom.xml").exists():      return "java", "maven"
    if (p / "build.gradle").exists(): return "java", "gradle"
    if (p / "go.mod").exists():       return "go", "go"
    if (p / "Gemfile").exists():
        if (p / "config" / "routes.rb").exists() or "rails" in (p / "Gemfile").read_text().lower():
            return "ruby", "rails"
        return "ruby", "ruby"

    py_count = count_source_files(workspace, [".py"])
    js_count = count_source_files(workspace, [".js", ".ts", ".jsx", ".tsx"])
    return ("python", "python") if py_count > js_count else ("javascript", "node")


# --- detect_has_api -----------------------------------------------------------
# Structural markers (directory/file layout) - these are definitive
_API_DIR_MARKERS = [
    "routes/", "controllers/", "endpoints/", "routers/",
    "openapi.yaml", "openapi.json", "swagger.yaml", "swagger.json",
    "app/api/", "pages/api/", "src/routes/", "src/api/",
]

# STRONG route-registration code patterns (not bare "/api/" string occurrences)
_ROUTE_PATTERNS = [
    "@app.route(", "@router.get(", "@router.post(", "@router.put(",
    "@router.delete(", "@router.patch(", "app.add_url_rule(",
    "router = APIRouter", "api_router = APIRouter",
    "router.get(", "router.post(", "router.put(", "router.delete(",
    "app.get(", "app.post(", "app.put(", "app.delete(",
    "fastify.get(", "fastify.post(",
]

_ROUTE_FILE_EXTS = {".py", ".js", ".ts", ".go", ".java", ".rb"}
_SCAN_SKIP = {"node_modules", ".git", "venv", "__pycache__", "dist", "build",
              ".next", "coverage", "scripts", "qa-platform", "generated-tests",
              "test", "tests", "__tests__"}


def detect_has_api(workspace: str) -> bool:
    p = Path(workspace)

    # Tier 1: structural check (fast)
    for marker in _API_DIR_MARKERS:
        if (p / marker).exists():
            print(f"   API dir/file found: {marker}")
            return True

    # Tier 2: scan for actual route-registration code
    # Deliberately skips: test files, QA scripts, string-literal mentions
    for f in p.rglob("*"):
        if f.suffix.lower() not in _ROUTE_FILE_EXTS:
            continue
        if any(s in f.parts for s in _SCAN_SKIP):
            continue
        stem = f.stem.lower()
        if stem.startswith("test_") or stem.endswith("_test") or \
           stem.endswith(".test") or stem.endswith(".spec"):
            continue
        try:
            text = f.read_text(errors="ignore")
        except Exception:
            continue
        for pat in _ROUTE_PATTERNS:
            if pat in text:
                print(f"   API pattern '{pat}' in {f.relative_to(p)}")
                return True

    return False


def detect_has_auth(workspace: str) -> bool:
    p = Path(workspace)
    auth_indicators = ["login", "register", "logout", "auth", "signin", "signup",
                       "session", "jwt", "oauth", "passport"]
    skip = {"node_modules", ".git", "venv", "__pycache__", "dist", "build",
            "scripts", "qa-platform", "generated-tests"}

    for item in p.rglob("*"):
        if any(s in item.parts for s in skip):
            continue
        name = item.name.lower()
        if any(a in name for a in auth_indicators):
            return True

    pkg_path = p / "package.json"
    if pkg_path.exists():
        try:
            pkg  = json.loads(pkg_path.read_text())
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            auth_pkgs = {"passport", "jsonwebtoken", "next-auth", "auth0", "firebase",
                         "supabase", "clerk", "@auth0/auth0-react", "jwt-decode"}
            if auth_pkgs & set(deps.keys()):
                return True
        except Exception:
            pass
    return False


def get_changed_files(workspace: str) -> list:
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
            capture_output=True, text=True, check=True, cwd=workspace
        )
        files = [f for f in result.stdout.strip().split("\n") if f]
        if files:
            return files
    except Exception:
        pass
    try:
        result = subprocess.run(["git", "ls-files"], capture_output=True, text=True, cwd=workspace)
        return result.stdout.strip().split("\n")[:30]
    except Exception:
        return []


def read_code_sample(workspace: str, language: str) -> str:
    p = Path(workspace)
    ext_map = {
        "javascript": [".ts", ".tsx", ".js", ".jsx"],
        "python":     [".py"],
        "java":       [".java"],
        "go":         [".go"],
        "ruby":       [".rb"],
    }
    extensions = ext_map.get(language, [".py", ".js"])
    samples = []
    for ext in extensions:
        for f in sorted(p.rglob(f"*{ext}"))[:10]:
            if any(s in f.parts for s in SKIP_DIRS):
                continue
            try:
                text = f.read_text(errors="ignore")
                if len(text) > 50:
                    rel = f.relative_to(p)
                    samples.append(f"// -- {rel} --\n{text[:700]}")
            except Exception:
                pass
        if len(samples) >= 4:
            break
    return "\n\n".join(samples[:4])


def main():
    workspace = os.environ.get("WORKSPACE", os.getcwd())
    print(f"Detecting stack in: {workspace}\n")

    base_url             = os.environ.get("BASE_URL", "http://localhost:3000").rstrip("/")
    language, framework  = detect_language(workspace)
    has_api              = detect_has_api(workspace)
    has_auth             = detect_has_auth(workspace)
    changed_files        = get_changed_files(workspace)
    code_sample          = read_code_sample(workspace, language)

    print("Detected:")
    print(f"   Language:      {language}")
    print(f"   Framework:     {framework}")
    print(f"   Has API:       {has_api}")
    print(f"   Has Auth:      {has_auth}")
    print(f"   Base URL:      {base_url}")
    print(f"   Changed files: {len(changed_files)}")
    print()

    os.makedirs("generated-tests", exist_ok=True)
    detection = {
        "language":      language,
        "framework":     framework,
        "has_api":       has_api,
        "has_auth":      has_auth,
        "base_url":      base_url,
        "changed_files": changed_files,
        "code_sample":   code_sample[:4000],
        "workspace":     workspace,
    }
    with open("generated-tests/detection.json", "w") as f:
        json.dump(detection, f, indent=2)

    env_file = os.environ.get("GITHUB_ENV", "")
    if env_file:
        with open(env_file, "a") as f:
            f.write(f"DETECTED_LANGUAGE={language}\n")
            f.write(f"DETECTED_FRAMEWORK={framework}\n")
            f.write(f"CODE_SAMPLE<<EOF\n{code_sample[:2000]}\nEOF\n")

    set_output("language",      language)
    set_output("framework",     framework)
    set_output("has-api",       str(has_api).lower())
    set_output("changed-files", ",".join(changed_files[:20]))

    print("Stack detection complete")


if __name__ == "__main__":
    main()