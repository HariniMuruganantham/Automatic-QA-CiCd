#!/usr/bin/env python3
"""
scripts/detect_stack.py
-----------------------
Detects language, framework, API presence, auth, base URL, and changed files.
Writes detection.json for the AI generation step.
"""

import os
import json
import subprocess
from pathlib import Path


def set_output(name: str, value: str):
    output_file = os.environ.get("GITHUB_OUTPUT", "")
    line = f"{name}={value}\n"
    if output_file:
        with open(output_file, "a") as f:
            f.write(line)
    print(f"  → {name}={value}")


def detect_language(workspace: str) -> tuple:
    p = Path(workspace)

    if (p / "package.json").exists():
        try:
            pkg  = json.loads((p / "package.json").read_text())
        except Exception:
            pkg = {}
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        if "next"    in deps: return "javascript", "nextjs"
        if "express" in deps: return "javascript", "express"
        if "fastify" in deps: return "javascript", "fastify"
        if "react"   in deps: return "javascript", "react"
        if "koa"     in deps: return "javascript", "koa"
        return "javascript", "node"

    if (p / "requirements.txt").exists() or (p / "pyproject.toml").exists():
        req  = p / "requirements.txt"
        reqs = req.read_text().lower() if req.exists() else ""
        if "django"  in reqs: return "python", "django"
        if "fastapi" in reqs: return "python", "fastapi"
        if "flask"   in reqs: return "python", "flask"
        if "tornado" in reqs: return "python", "tornado"
        return "python", "python"

    if (p / "pom.xml").exists():      return "java",   "maven"
    if (p / "build.gradle").exists(): return "java",   "gradle"
    if (p / "go.mod").exists():       return "go",     "go"
    if (p / "Gemfile").exists():      return "ruby",   "rails"

    return "javascript", "node"


def detect_has_api(workspace: str) -> bool:
    p = Path(workspace)
    indicators = [
        "routes/", "api/", "controllers/", "endpoints/",
        "openapi.yaml", "openapi.json", "swagger.yaml", "swagger.json",
        "app/api/", "src/routes/", "src/api/", "routers/",
    ]
    return any((p / ind).exists() for ind in indicators)


def detect_has_auth(code_sample: str) -> bool:
    keywords = ["jwt", "token", "bearer", "authorization", "login", "authenticate",
                "passport", "session", "cookie", "oauth"]
    lower = code_sample.lower()
    return any(kw in lower for kw in keywords)


def detect_base_url(workspace: str) -> str:
    """Try to find the port the app runs on."""
    p = Path(workspace)
    skip = {"node_modules", ".git", "venv", "__pycache__", "dist"}

    for f in list(p.rglob("*.py"))[:20] + list(p.rglob("*.js"))[:20]:
        if any(s in f.parts for s in skip):
            continue
        try:
            text = f.read_text(errors="ignore")
            for port in ["8000", "8080", "5000", "4000"]:
                if port in text:
                    return f"http://localhost:{port}"
        except Exception:
            pass
    return "http://localhost:3000"


def get_changed_files() -> list:
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
            capture_output=True, text=True, check=True
        )
        files = [f for f in result.stdout.strip().split("\n") if f]
        if files:
            return files
    except Exception:
        pass
    try:
        result = subprocess.run(["git", "ls-files"], capture_output=True, text=True)
        return result.stdout.strip().split("\n")[:30]
    except Exception:
        return []


def read_code_sample(workspace: str, language: str) -> str:
    p    = Path(workspace)
    exts = {
        "javascript": [".ts", ".tsx", ".js", ".jsx"],
        "python":     [".py"],
        "java":       [".java"],
        "go":         [".go"],
    }.get(language, [".py"])
    skip = {"node_modules", ".git", "venv", "__pycache__",
            "dist", "build", ".next", "coverage"}

    samples = []
    for ext in exts:
        for f in sorted(p.rglob(f"*{ext}"))[:10]:
            if any(s in f.parts for s in skip):
                continue
            try:
                text = f.read_text(errors="ignore")
                if len(text) > 50:
                    samples.append(f"// ── {f.relative_to(p)} ──\n{text[:800]}")
            except Exception:
                pass
        if len(samples) >= 4:
            break
    return "\n\n".join(samples[:4])


def main():
    workspace = os.environ.get("WORKSPACE", os.getcwd())
    print(f"🔍 Detecting stack in: {workspace}\n")

    language, framework = detect_language(workspace)
    has_api             = detect_has_api(workspace)
    changed_files       = get_changed_files()
    code_sample         = read_code_sample(workspace, language)
    has_auth            = detect_has_auth(code_sample)
    base_url            = os.environ.get("BASE_URL") or detect_base_url(workspace)

    print("📦 Detected:")
    print(f"   Language:  {language}")
    print(f"   Framework: {framework}")
    print(f"   Has API:   {has_api}")
    print(f"   Has Auth:  {has_auth}")
    print(f"   Base URL:  {base_url}")
    print(f"   Changed:   {len(changed_files)} files")
    print()

    os.makedirs("generated-tests", exist_ok=True)
    detection = {
        "language":      language,
        "framework":     framework,
        "has_api":       has_api,
        "has_auth":      has_auth,
        "base_url":      base_url,
        "changed_files": changed_files,
        "code_sample":   code_sample[:5000],
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
    set_output("has-auth",      str(has_auth).lower())
    set_output("base-url",      base_url)
    set_output("changed-files", ",".join(changed_files[:20]))

    print("✅ Stack detection complete")


if __name__ == "__main__":
    main()