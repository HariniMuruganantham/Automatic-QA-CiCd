#!/usr/bin/env python3
"""
scripts/smoke/check_results.py
-------------------------------
Pipeline gate: exits 1 if smoke tests failed OR had collection errors.
Handles Jest and pytest-json-report formats.
"""

import sys
import json
from pathlib import Path


def load_and_check(results_file: str):
    path = Path(results_file)

    if not path.exists():
        print(f"⚠️  Results file not found: {results_file}")
        print("   Treating as FAIL — cannot proceed without smoke results.")
        sys.exit(1)

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"❌  Cannot parse results file: {e}")
        sys.exit(1)

    # ── Jest format ────────────────────────────────────────────
    if "numFailedTests" in data:
        failed = data.get("numFailedTests",  0)
        passed = data.get("numPassedTests",  0)
        total  = data.get("numTotalTests",   0)
        fmt    = "Jest"
        failures = []
        for suite in data.get("testResults", []):
            for t in suite.get("testResults", []):
                if t.get("status") == "failed":
                    msg = (t.get("failureMessages") or ["(no message)"])[0]
                    failures.append((t.get("fullName", "unknown"), msg[:250]))

    # ── pytest-json-report format ──────────────────────────────
    elif "summary" in data:
        s      = data.get("summary", {})
        failed = s.get("failed",  0)
        passed = s.get("passed",  0)
        total  = s.get("total",   0)
        fmt    = "pytest"
        failures = []

        # Check collector errors (import errors, syntax errors)
        collector_errors = []
        for c in data.get("collectors", []):
            if c.get("outcome") == "failed":
                repr_ = str(c.get("longrepr", ""))
                collector_errors.append((c.get("nodeid", "unknown"), repr_[:300]))

        if collector_errors:
            print(f"\n❌  SMOKE GATE BLOCKED — collection errors (import/syntax)")
            print("   Tests could not even be imported. Fix these errors first:\n")
            for nodeid, repr_ in collector_errors:
                print(f"   ✗ {nodeid}")
                # Show the actual error line
                for line in repr_.splitlines():
                    if "Error" in line or "error" in line:
                        print(f"     {line.strip()}")
                        break
                print()
            print("   Common fixes:")
            print("   • 'No module named playwright' → add playwright install step")
            print("   • 'No module named httpx'      → add httpx to pip install step")
            print("   • 'ModuleNotFoundError'         → check imports in generated tests")
            sys.exit(1)

        for t in data.get("tests", []):
            if t.get("outcome") == "failed":
                msg = str((t.get("call") or {}).get("longrepr", "(no message)"))
                failures.append((t.get("nodeid", "unknown"), msg[:250]))

    else:
        print("⚠️  Unknown results format — assuming PASS")
        sys.exit(0)

    print(f"💨 Smoke Tests ({fmt}): {passed}/{total} passed, {failed} failed")

    if failed > 0:
        print(f"\n❌  SMOKE GATE BLOCKED — {failed} test(s) failed\n")
        for name, msg in failures[:5]:
            print(f"   ✗ {name}")
            first = next((l.strip() for l in msg.splitlines() if l.strip()), "(no details)")
            print(f"     {first}\n")
        sys.exit(1)

    print("✅  Smoke gate passed — pipeline continues\n")
    sys.exit(0)


if __name__ == "__main__":
    results_file = sys.argv[1] if len(sys.argv) > 1 else "smoke-results.json"
    load_and_check(results_file)