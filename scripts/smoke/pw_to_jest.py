#!/usr/bin/env python3
"""
scripts/smoke/pw_to_jest.py
Convert Playwright JSON reporter output to Jest-compatible format
so check_results.py can parse it uniformly.
"""
import json
import sys

pw_file   = sys.argv[1] if len(sys.argv) > 1 else "smoke-pw.json"
jest_file = sys.argv[2] if len(sys.argv) > 2 else "smoke-results.json"

empty = {
    "numPassedTests": 0,
    "numFailedTests": 0,
    "numPendingTests": 0,
    "numTotalTests": 0,
    "testResults": [],
}

try:
    with open(pw_file) as f:
        data = json.load(f)

    stats  = data.get("stats", {})
    passed = stats.get("expected",   0)
    failed = stats.get("unexpected", 0)
    skipped = stats.get("skipped",   0)

    # Also collect failure messages for the report
    failures = []
    for suite in data.get("suites", []):
        for spec in suite.get("specs", []):
            for test in spec.get("tests", []):
                if test.get("status") in ("failed", "unexpected"):
                    failures.append({
                        "ancestorTitles": [suite.get("title", "")],
                        "fullName": spec.get("title", "unknown"),
                        "status": "failed",
                        "failureMessages": [
                            r.get("error", {}).get("message", "")
                            for r in test.get("results", [])
                            if r.get("status") == "failed"
                        ],
                    })

    result = {
        "numPassedTests":  passed,
        "numFailedTests":  failed,
        "numPendingTests": skipped,
        "numTotalTests":   passed + failed + skipped,
        "testResults": [{"testResults": failures}] if failures else [],
    }

    with open(jest_file, "w") as f:
        json.dump(result, f)

    print(f"Playwright results: {passed} passed, {failed} failed, {skipped} skipped")

except FileNotFoundError:
    print(f"Warning: {pw_file} not found - writing empty results")
    with open(jest_file, "w") as f:
        json.dump(empty, f)
except Exception as e:
    print(f"Warning: {e} - writing empty results")
    with open(jest_file, "w") as f:
        json.dump(empty, f)