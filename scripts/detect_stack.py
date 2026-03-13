#!/usr/bin/env python3
"""
detect_stack.py
Auto-detects the project's language, framework, and changed files.
Outputs GitHub Actions step outputs.
"""

import os
import json
import subprocess
from pathlib import Path


def set_output(name: str, value: str):
    output_file = os.environ.get("GITHUB_OUTPUT", "/dev/stdout")
    with open(output_file, "a") as f:
        f.write(f"{name}={value}\n")
    print(f"  → {name}={value}")


def detect_language(workspace: str) -> tuple[str, str]:
    p = Path(workspace)

    # JavaScript / TypeScript
    if (p / "package.json").exists():
        pkg = json.loads((p / "package.json").read_text())
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        if "next" in deps:
            return "javascript", "nextjs"
        if "express" in deps:
            return "javascript", "express"
        if "fastify" in deps:
            return "javascript", "fastify"
        if "react" in deps:
            return "javascript", "react"
        return "javascript", "node"

    # Python
    if (p / "requirements.txt").exists() or (p / "pyproject.toml").exists():
        req_file = p / "requirements.txt"
        reqs = req_file.read_text().lower() if req_file.exists() else ""
        if "django" in reqs:
            return "python", "django"
        if "fastapi" in reqs:
            return "python", "fastapi"
        if "flask" in reqs:
            return "python", "flask"
        return "python", "python"

    # Java
    if (p / "pom.xml").exists():
        return "java", "maven"
    if (p / "build.gradle").exists():
        return "java", "gradle"

    # Go
    if (p / "go.mod").exists():
        return "go", "go"

    return "javascript", "node"  # safe default


def detect_has_api(workspace: str) -> bool:
    p = Path(workspace)
    api_indicators = [
        "routes/", "api/", "controllers/", "endpoints/",
        "openapi.yaml", "openapi.json", "swagger.yaml", "swagger.json",
        "app/api/", "src/routes/", "src/api/"
    ]
    for indicator in api_indicators:
        if (p / indicator).exists():
            return True
    return False


def get_changed_files() -> list[str]:
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
            capture_output=True, text=True, check=True
        )
        files = [f for f in result.stdout.strip().split("\n") if f]
        return files
    except Exception:
        result = subprocess.run(["git", "ls-files"], capture_output=True, text=True)
        return result.stdout.strip().split("\n")[:20]


def read_code_sample(workspace: str, language: str) -> str:
    p = Path(workspace)
    extensions = {
        "javascript": [".ts", ".js", ".tsx", ".jsx"],
        "python": [".py"],
        "java": [".java"],
        "go": [".go"],
    }.get(language, [".py", ".js"])

    samples = []
    skip = ["node_modules", ".git", "venv", "__pycache__", "dist", "build", ".next"]

    for ext in extensions:
        for f in list(p.rglob(f"*{ext}"))[:8]:
            if any(s in str(f) for s in skip):
                continue
            try:
                content = f.read_text(errors="ignore")[:600]
                samples.append(f"// File: {f.relative_to(p)}\n{content}")
            except Exception:
                pass
        if samples:
            break

    return "\n\n".join(samples[:4])


def main():
    workspace = os.environ.get("WORKSPACE", os.getcwd())
    print(f"🔍 Detecting stack in: {workspace}")

    language, framework = detect_language(workspace)
    has_api = detect_has_api(workspace)
    changed_files = get_changed_files()
    code_sample = read_code_sample(workspace, language)

    print(f"\n📦 Stack detected:")
    print(f"   Language:  {language}")
    print(f"   Framework: {framework}")
    print(f"   Has API:   {has_api}")
    print(f"   Changed:   {len(changed_files)} files")

    # Write env vars for the AI step
    env_file = os.environ.get("GITHUB_ENV", "/dev/null")
    with open(env_file, "a") as f:
        f.write(f"CODE_SAMPLE<<EOF\n{code_sample[:2000]}\nEOF\n")
        f.write(f"CHANGED_FILES_LIST={','.join(changed_files[:20])}\n")

    set_output("language", language)
    set_output("framework", framework)
    set_output("has-api", str(has_api).lower())
    set_output("changed-files", ",".join(changed_files[:20]))

    # Persist for AI step
    os.makedirs("generated-tests", exist_ok=True)
    detection = {
        "language": language,
        "framework": framework,
        "has_api": has_api,
        "changed_files": changed_files,
        "code_sample": code_sample[:4000],
        "workspace": workspace
    }
    with open("generated-tests/detection.json", "w") as f:
        json.dump(detection, f, indent=2)

    print("\n✅ Stack detection complete")


if __name__ == "__main__":
    main()
