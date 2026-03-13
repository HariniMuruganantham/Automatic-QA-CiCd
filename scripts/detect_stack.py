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
from pathlib import Path


def set_output(name: str, value: str):
    """Write a key=value line to GITHUB_OUTPUT (or stdout for local runs)."""
    output_file = os.environ.get("GITHUB_OUTPUT", "")
    line = f"{name}={value}\n"
    if output_file:
        with open(output_file, "a") as f:
            f.write(line)
    print(f"  → {name}={value}")


SKIP_DIRS = {"node_modules", ".git", "venv", "__pycache__",
             "dist", "build", ".next", ".nuxt", "coverage"}


def count_source_files(workspace: str, extensions: list) -> int:
    """Count source files with given extensions, skipping noise dirs."""
    p = Path(workspace)
    count = 0
    for ext in extensions:
        for f in p.rglob(f"*{ext}"):
            if not any(s in f.parts for s in SKIP_DIRS):
                count += 1
    return count


def detect_language(workspace: str) -> tuple:
    """Return (language, framework) by inspecting root files + source file counts."""
    p = Path(workspace)

    has_package_json   = (p / "package.json").exists()
    has_requirements   = (p / "requirements.txt").exists()
    has_pyproject      = (p / "pyproject.toml").exists()
    has_python_marker  = has_requirements or has_pyproject

    # If BOTH package.json and Python markers exist, count source files to decide
    if has_package_json and has_python_marker:
        py_count = count_source_files(workspace, [".py"])
        js_count = count_source_files(workspace, [".js", ".ts", ".jsx", ".tsx"])
        print(f"   ⚖️  Both package.json and Python markers found — "
              f"py={py_count} js={js_count}")
        use_python = py_count > js_count
    else:
        use_python = has_python_marker and not has_package_json

    # ── JavaScript / TypeScript ──────────────────────────────────
    if has_package_json and not use_python:
        try:
            pkg = json.loads((p / "package.json").read_text())
        except Exception:
            pkg = {}
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        if "next"     in deps: return "javascript", "nextjs"
        if "express"  in deps: return "javascript", "express"
        if "fastify"  in deps: return "javascript", "fastify"
        if "react"    in deps: return "javascript", "react"
        if "koa"      in deps: return "javascript", "koa"
        if "hapi"     in deps: return "javascript", "hapi"
        return "javascript", "node"

    # ── Python ───────────────────────────────────────────────────
    if has_python_marker or use_python:
        req_path = p / "requirements.txt"
        reqs = req_path.read_text().lower() if req_path.exists() else ""
        if "django"  in reqs: return "python", "django"
        if "fastapi" in reqs: return "python", "fastapi"
        if "flask"   in reqs: return "python", "flask"
        if "tornado" in reqs: return "python", "tornado"
        return "python", "python"

    # ── Java ─────────────────────────────────────────────────────
    if (p / "pom.xml").exists():         return "java", "maven"
    if (p / "build.gradle").exists():    return "java", "gradle"

    # ── Go ───────────────────────────────────────────────────────
    if (p / "go.mod").exists():          return "go", "go"

    # ── Ruby ─────────────────────────────────────────────────────
    if (p / "Gemfile").exists():         return "ruby", "rails"

    # Default — last resort, count files to make best guess
    py_count = count_source_files(workspace, [".py"])
    js_count = count_source_files(workspace, [".js", ".ts", ".jsx", ".tsx"])
    if py_count > js_count:
        return "python", "python"
    return "javascript", "node"


def detect_has_api(workspace: str) -> bool:
    """Return True if the project exposes an HTTP API."""
    p = Path(workspace)
    indicators = [
        "routes/", "api/", "controllers/", "endpoints/",
        "openapi.yaml", "openapi.json", "swagger.yaml", "swagger.json",
        "app/api/", "src/routes/", "src/api/", "routers/",
    ]
    return any((p / ind).exists() for ind in indicators)


def get_changed_files() -> list:
    """Return list of files changed in the last commit."""
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

    # Fallback: all tracked source files
    try:
        result = subprocess.run(
            ["git", "ls-files"], capture_output=True, text=True
        )
        return result.stdout.strip().split("\n")[:30]
    except Exception:
        return []


def read_code_sample(workspace: str, language: str) -> str:
    """Read a representative code snippet from the project."""
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
                if len(text) > 50:   # skip near-empty files
                    rel = f.relative_to(p)
                    samples.append(f"// ── {rel} ──\n{text[:700]}")
            except Exception:
                pass
        if len(samples) >= 4:
            break

    return "\n\n".join(samples[:4])


def main():
    workspace = os.environ.get("WORKSPACE", os.getcwd())
    print(f"🔍 Detecting stack in: {workspace}\n")

    language, framework  = detect_language(workspace)
    has_api              = detect_has_api(workspace)
    changed_files        = get_changed_files()
    code_sample          = read_code_sample(workspace, language)

    print("📦 Detected:")
    print(f"   Language:      {language}")
    print(f"   Framework:     {framework}")
    print(f"   Has API:       {has_api}")
    print(f"   Changed files: {len(changed_files)}")
    print()

    # Persist for the AI generation step
    os.makedirs("generated-tests", exist_ok=True)
    detection = {
        "language":      language,
        "framework":     framework,
        "has_api":       has_api,
        "changed_files": changed_files,
        "code_sample":   code_sample[:4000],
        "workspace":     workspace,
    }
    with open("generated-tests/detection.json", "w") as f:
        json.dump(detection, f, indent=2)

    # Write to GITHUB_ENV so next steps can also read these
    env_file = os.environ.get("GITHUB_ENV", "")
    if env_file:
        with open(env_file, "a") as f:
            f.write(f"DETECTED_LANGUAGE={language}\n")
            f.write(f"DETECTED_FRAMEWORK={framework}\n")
            f.write(f"CODE_SAMPLE<<EOF\n{code_sample[:2000]}\nEOF\n")

    # Write step outputs
    set_output("language",      language)
    set_output("framework",     framework)
    set_output("has-api",       str(has_api).lower())
    set_output("changed-files", ",".join(changed_files[:20]))

    print("✅ Stack detection complete")


if __name__ == "__main__":
    main()