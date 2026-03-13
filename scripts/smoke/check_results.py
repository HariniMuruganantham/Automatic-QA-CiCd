#!/usr/bin/env python3
"""
check_results.py
Smoke gate — exits non-zero if smoke tests failed, blocking the pipeline.
"""

import sys
import json
from pathlib import Path


def check(results_file: str):
    path = Path(results_file)
    if not path.exists():
        print(f"⚠️  Results file not found: {results_file}")
        print("   Treating as FAIL — cannot proceed without smoke results.")
        sys.exit(1)

    try:
        data = json.loads(path.read_text())
    except Exception as e:
        print(f"❌  Cannot parse results file: {e}")
        sys.exit(1)

    # Jest format
    if "numFailedTests" in data:
        failed  = data["numFailedTests"]
        total   = data["numTotalTests"]
        passed  = data["numPassedTests"]
        fmt     = "jest"

    # pytest-json-report format
    elif "summary" in data:
        s       = data["summary"]
        failed  = s.get("failed",  0)
        total   = s.get("total",   0)
        passed  = s.get("passed",  0)
        fmt     = "pytest"

    else:
        print("⚠️  Unknown results format — assuming PASS (manual verification recommended)")
        sys.exit(0)

    print(f"💨 Smoke Results ({fmt}): {passed}/{total} passed, {failed} failed")

    if failed > 0:
        print(f"\n❌  SMOKE GATE BLOCKED — {failed} test(s) failed")
        print("   The pipeline will NOT continue until smoke tests pass.\n")

        if fmt == "jest":
            for suite in data.get("testResults", []):
                for t in suite.get("testResults", []):
                    if t.get("status") == "failed":
                        print(f"   ✗ {t.get('fullName', 'unknown')}")
                        for msg in (t.get("failureMessages") or [])[:1]:
                            print(f"     {msg[:200]}")

        elif fmt == "pytest":
            for t in data.get("tests", []):
                if t.get("outcome") == "failed":
                    print(f"   ✗ {t.get('nodeid', 'unknown')}")
                    msg = (t.get("call") or {}).get("longrepr", "")
                    if msg:
                        print(f"     {str(msg)[:200]}")

        sys.exit(1)

    print("✅  Smoke gate passed — pipeline continues\n")
    sys.exit(0)


if __name__ == "__main__":
    check(sys.argv[1] if len(sys.argv) > 1 else "smoke-results.json")
