#!/usr/bin/env python3
"""
generate_report.py
Collects all test results, produces a rich HTML QA report,
and uses OpenAI to generate an intelligent failure analysis.
"""

import os
import json
import argparse
from pathlib import Path
from datetime import datetime
from openai import OpenAI

# ── Result Parsers ─────────────────────────────────────────────────────────────

def parse_jest(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text())
        failures = []
        for suite in data.get("testResults", []):
            for t in suite.get("testResults", []):
                if t.get("status") == "failed":
                    failures.append({
                        "name": t.get("fullName", "unknown"),
                        "message": (t.get("failureMessages") or [""])[0][:300]
                    })
        return {
            "passed":   data.get("numPassedTests", 0),
            "failed":   data.get("numFailedTests", 0),
            "skipped":  data.get("numPendingTests", 0),
            "total":    data.get("numTotalTests", 0),
            "duration": round(data.get("testResults", [{}])[0].get("endTime", 0) -
                              data.get("testResults", [{}])[0].get("startTime", 0), 2),
            "failures": failures[:5],
        }
    except Exception as e:
        return _empty(f"Jest parse error: {e}")


def parse_pytest(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text())
        s = data.get("summary", {})
        failures = [
            {
                "name": t.get("nodeid", "unknown"),
                "message": (t.get("call") or {}).get("longrepr", "")[:300]
            }
            for t in data.get("tests", [])
            if t.get("outcome") == "failed"
        ][:5]
        return {
            "passed":   s.get("passed",  0),
            "failed":   s.get("failed",  0),
            "skipped":  s.get("skipped", 0),
            "total":    s.get("total",   0),
            "duration": round(data.get("duration", 0), 2),
            "failures": failures,
        }
    except Exception as e:
        return _empty(f"Pytest parse error: {e}")


def parse_k6(path: str) -> dict:
    try:
        durations, errors, reqs = [], [], []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("type") != "Point":
                        continue
                    m   = obj.get("metric", "")
                    val = obj.get("data", {}).get("value", 0)
                    if m == "http_req_duration":
                        durations.append(val)
                    elif m == "http_req_failed":
                        errors.append(val)
                    elif m == "http_reqs":
                        reqs.append(val)
                except Exception:
                    pass

        n = len(durations)
        p50 = round(sorted(durations)[n // 2], 2)       if n else 0
        p95 = round(sorted(durations)[int(n * 0.95)], 2) if n else 0
        error_rate = round(sum(errors) / max(len(errors), 1) * 100, 2)

        return {
            "total_requests": int(sum(reqs)),
            "p50_ms":    p50,
            "p95_ms":    p95,
            "error_rate": error_rate,
            "passed":    1 if error_rate < 5 else 0,
            "failed":    0 if error_rate < 5 else 1,
            "skipped":   0,
        }
    except Exception as e:
        return {"total_requests": 0, "p50_ms": 0, "p95_ms": 0,
                "error_rate": 0, "passed": 0, "failed": 0, "skipped": 0, "note": str(e)}


def _empty(note: str = "") -> dict:
    return {"passed": 0, "failed": 0, "skipped": 0, "total": 0, "failures": [], "note": note}


# ── Load all results from artifact dirs ───────────────────────────────────────

def load_all(results_dir: str) -> dict:
    base = Path(results_dir)
    results = {}

    def find(*pats):
        for pat in pats:
            for p in base.rglob(pat):
                return str(p)
        return None

    # Smoke — could be Jest or pytest format
    f = find("smoke-results/smoke-results.json", "*smoke*.json")
    if f:
        try:
            raw = json.loads(Path(f).read_text())
            results["smoke"] = parse_jest(f) if "numTotalTests" in raw else parse_pytest(f)
        except Exception:
            results["smoke"] = _empty("parse failed")
    else:
        results["smoke"] = _empty("file not found")

    # Sanity
    f = find("sanity-results/sanity-results.json", "*sanity*.json")
    results["sanity"] = parse_pytest(f) if f else _empty("file not found")

    # API
    f = find("api-results/api-results.json", "*api-results*.json")
    results["api"] = parse_pytest(f) if f else _empty("file not found")

    # Regression
    f = find("regression-results/regression-py-results.json", "*regression*.json")
    results["regression"] = parse_pytest(f) if f else _empty("file not found")

    # UAT
    f = find("uat-results/*.json", "*uat*.json")
    if f:
        try:
            raw = json.loads(Path(f).read_text())
            results["uat"] = parse_jest(f) if "numTotalTests" in raw else _empty("unknown format")
        except Exception:
            results["uat"] = _empty("parse failed")
    else:
        results["uat"] = _empty("file not found")

    # Load
    f = find("load-results/load-results.json", "*load-results*.json")
    results["load"] = parse_k6(f) if f else {"total_requests": 0, "p50_ms": 0, "p95_ms": 0,
                                              "error_rate": 0, "passed": 0, "failed": 0, "skipped": 0}

    # Stress (optional)
    f = find("stress-results/stress-results.json", "*stress*.json")
    results["stress"] = parse_k6(f) if f else None

    return results


# ── AI Analysis ────────────────────────────────────────────────────────────────

def ai_analysis(results: dict, project: str, api_key: str, model: str) -> str:
    if not api_key:
        return "AI analysis skipped — OPENAI_API_KEY not set."

    lines = []
    for suite, data in results.items():
        if data is None:
            continue
        if "p95_ms" in data:
            lines.append(f"{suite}: p95={data['p95_ms']}ms, error_rate={data['error_rate']}%,"
                         f" total_requests={data['total_requests']}")
        else:
            fail_names = [f["name"] for f in data.get("failures", [])[:3]]
            lines.append(
                f"{suite}: passed={data['passed']}, failed={data['failed']}, skipped={data['skipped']}"
                + (f" | failing: {', '.join(fail_names)}" if fail_names else "")
            )

    prompt = f"""QA test run for '{project}':
{chr(10).join(lines)}

Write a 3-4 sentence plain-text analysis:
- Overall health assessment
- Most critical issues (be specific about test names if available)
- Top 1-2 recommended actions for the team
No bullet points, no markdown."""

    try:
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=280,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"AI analysis failed: {e}"


# ── HTML Report Builder ────────────────────────────────────────────────────────

def build_html(results: dict, project: str, commit: str,
               branch: str, model: str, ai_text: str) -> str:

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    short_sha = (commit or "unknown")[:7]

    non_perf = {k: v for k, v in results.items() if v and "p95_ms" not in v}
    total_passed = sum(v.get("passed", 0) for v in non_perf.values())
    total_failed = sum(v.get("failed", 0) for v in non_perf.values())
    overall = "PASS" if total_failed == 0 else "FAIL"
    oc = "#10b981" if overall == "PASS" else "#ef4444"

    def badge(passed: int, failed: int) -> str:
        cls = "pass" if failed == 0 else "fail"
        label = "PASS" if failed == 0 else "FAIL"
        return f'<span class="badge {cls}">{label}</span>'

    def suite_row(name: str, icon: str, key: str) -> str:
        data = results.get(key)
        if data is None:
            return f'<tr><td class="suite-name">{icon} {name}</td>' \
                   f'<td colspan="4" style="color:#6b7280;font-size:12px">Not run this cycle</td></tr>'
        if "p95_ms" in data:
            ok = data.get("error_rate", 100) < 5
            cls = "pass" if ok else "fail"
            return f"""<tr>
              <td class="suite-name">{icon} {name}</td>
              <td><span class="badge {cls}">{'PASS' if ok else 'FAIL'}</span></td>
              <td>{data['total_requests']} req</td>
              <td>p95: {data['p95_ms']}ms &nbsp; p50: {data['p50_ms']}ms</td>
              <td style="color:{'#10b981' if ok else '#ef4444'}">{data['error_rate']}% errors</td>
            </tr>"""
        return f"""<tr>
          <td class="suite-name">{icon} {name}</td>
          <td>{badge(data['passed'], data['failed'])}</td>
          <td style="color:#10b981">✓ {data['passed']}</td>
          <td style="color:#ef4444">✗ {data['failed']}</td>
          <td style="color:#6b7280">⊘ {data['skipped']}</td>
        </tr>"""

    def failure_section(key: str, icon: str, label: str) -> str:
        data = results.get(key, {})
        if not data or not data.get("failures"):
            return ""
        cards = "".join(f"""<div class="failure-card">
          <div class="fail-name">{f['name']}</div>
          <pre class="fail-msg">{f['message'].replace('<','&lt;').replace('>','&gt;')[:400]}</pre>
        </div>""" for f in data["failures"])
        return f'<div class="fail-group"><h3>{icon} {label} Failures</h3>{cards}</div>'

    failures_html = "".join([
        failure_section("smoke",      "💨", "Smoke"),
        failure_section("sanity",     "🔍", "Sanity"),
        failure_section("api",        "🔌", "API"),
        failure_section("regression", "🔁", "Regression"),
        failure_section("uat",        "👤", "UAT"),
    ])
    failures_block = (
        f'<div class="section"><h2>❌ Failure Details</h2>{failures_html}</div>'
        if failures_html else ""
    )

    load  = results.get("load",  {}) or {}
    stress = results.get("stress", {}) or {}

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>QA Report — {project}</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Inter:wght@400;500;600;700;800&display=swap');
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: 'Inter', sans-serif;
  background: #0f1117;
  color: #e2e8f0;
  padding: 40px 24px;
  min-height: 100vh;
}}
.container {{ max-width: 980px; margin: 0 auto; }}
/* Header */
.header {{ display: flex; justify-content: space-between; align-items: flex-start;
           flex-wrap: wrap; gap: 20px; margin-bottom: 36px; }}
.title {{ font-size: clamp(26px, 5vw, 42px); font-weight: 800; letter-spacing: -0.03em; line-height: 1.1; }}
.title .accent {{
  background: linear-gradient(135deg, #6366f1 0%, #a78bfa 50%, #34d399 100%);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}}
.meta {{ font-family: 'JetBrains Mono', monospace; font-size: 12px; color: #6b7280; margin-top: 10px; }}
.overall-badge {{
  font-size: 20px; font-weight: 700; padding: 10px 28px;
  border-radius: 10px; border: 1.5px solid {oc}44;
  background: {oc}18; color: {oc}; white-space: nowrap;
}}
/* Divider */
.divider {{ height: 1px; background: linear-gradient(90deg,transparent,#6366f1,#a78bfa,#34d399,transparent);
           margin: 0 0 32px; }}
/* Stats */
.stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(130px,1fr)); gap: 12px; margin-bottom: 24px; }}
.stat {{
  background: #1a1d27; border: 1px solid #2d3148;
  border-radius: 12px; padding: 20px 16px; text-align: center;
}}
.stat-val {{ font-size: 36px; font-weight: 800; line-height: 1; }}
.stat-lbl {{ font-size: 11px; color: #6b7280; margin-top: 6px; letter-spacing: 0.05em; text-transform: uppercase; }}
/* Sections */
.section {{
  background: #1a1d27; border: 1px solid #2d3148;
  border-radius: 14px; padding: 24px; margin-bottom: 20px;
}}
.section h2 {{ font-size: 15px; font-weight: 600; color: #a78bfa; margin-bottom: 18px; letter-spacing: 0.02em; }}
/* Table */
table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
th {{
  text-align: left; padding: 10px 14px;
  font-size: 10px; text-transform: uppercase; letter-spacing: 0.1em;
  color: #475569; border-bottom: 1px solid #2d3148;
}}
td {{ padding: 13px 14px; border-bottom: 1px solid #1e2030; }}
tr:last-child td {{ border-bottom: none; }}
.suite-name {{ font-weight: 500; }}
/* Badges */
.badge {{ font-size: 10px; font-weight: 700; padding: 3px 10px; border-radius: 999px; letter-spacing: 0.05em; }}
.badge.pass {{ background: #10b98120; color: #10b981; border: 1px solid #10b98140; }}
.badge.fail {{ background: #ef444420; color: #ef4444; border: 1px solid #ef444440; }}
/* AI Box */
.ai-label {{ font-size: 10px; text-transform: uppercase; letter-spacing: 0.12em;
             color: #a78bfa; font-weight: 600; margin-bottom: 10px; }}
.ai-box {{
  background: #111827; border-left: 3px solid #a78bfa;
  border-radius: 0 10px 10px 0; padding: 18px 20px;
  font-size: 14px; line-height: 1.75; color: #cbd5e1;
}}
/* Failures */
.fail-group {{ margin-bottom: 20px; }}
.fail-group h3 {{ font-size: 13px; font-weight: 600; color: #ef4444; margin-bottom: 10px; }}
.failure-card {{
  background: #1f0a0a; border: 1px solid #3d1515;
  border-radius: 8px; padding: 14px; margin-bottom: 8px;
}}
.fail-name {{ font-size: 13px; font-weight: 600; color: #fca5a5; margin-bottom: 8px; }}
.fail-msg {{
  font-family: 'JetBrains Mono', monospace; font-size: 11px;
  color: #9ca3af; white-space: pre-wrap; overflow: auto; max-height: 110px;
}}
/* Footer */
.footer {{
  text-align: center; font-size: 11px; color: #374151;
  margin-top: 40px; padding-top: 20px; border-top: 1px solid #1e2030;
}}
</style>
</head>
<body>
<div class="container">

  <div class="header">
    <div>
      <div class="title">QA Report &mdash; <span class="accent">{project}</span></div>
      <div class="meta">
        Branch: {branch} &nbsp;&middot;&nbsp; Commit: {short_sha}
        &nbsp;&middot;&nbsp; {now} &nbsp;&middot;&nbsp; Model: {model}
      </div>
    </div>
    <div class="overall-badge">{'✅' if overall == 'PASS' else '❌'} {overall}</div>
  </div>

  <div class="divider"></div>

  <div class="stats">
    <div class="stat">
      <div class="stat-val" style="color:#10b981">{total_passed}</div>
      <div class="stat-lbl">Passed</div>
    </div>
    <div class="stat">
      <div class="stat-val" style="color:#ef4444">{total_failed}</div>
      <div class="stat-lbl">Failed</div>
    </div>
    <div class="stat">
      <div class="stat-val" style="color:#6366f1">{load.get('total_requests', 0)}</div>
      <div class="stat-lbl">Load Requests</div>
    </div>
    <div class="stat">
      <div class="stat-val" style="color:#f59e0b">{load.get('p95_ms', 0)}<span style="font-size:16px">ms</span></div>
      <div class="stat-lbl">p95 Latency</div>
    </div>
    <div class="stat">
      <div class="stat-val" style="color:#{'10b981' if load.get('error_rate',0) < 5 else 'ef4444'}">{load.get('error_rate', 0)}<span style="font-size:16px">%</span></div>
      <div class="stat-lbl">Error Rate</div>
    </div>
  </div>

  <div class="section">
    <h2>🤖 AI Analysis</h2>
    <div class="ai-label">OpenAI &middot; {model}</div>
    <div class="ai-box">{ai_text}</div>
  </div>

  <div class="section">
    <h2>📊 Test Suite Results</h2>
    <table>
      <thead>
        <tr>
          <th>Suite</th><th>Status</th>
          <th>Passed / Requests</th><th>Failed / p95</th><th>Skipped / Errors</th>
        </tr>
      </thead>
      <tbody>
        {suite_row("Smoke",      "💨", "smoke")}
        {suite_row("Sanity",     "🔍", "sanity")}
        {suite_row("API Tests",  "🔌", "api")}
        {suite_row("Regression", "🔁", "regression")}
        {suite_row("UAT",        "👤", "uat")}
        {suite_row("Load Test",  "📈", "load")}
        {suite_row("Stress Test","💥", "stress")}
      </tbody>
    </table>
  </div>

  {failures_block}

  <div class="footer">
    Universal QA Platform &nbsp;&middot;&nbsp;
    OpenAI {model} &nbsp;&middot;&nbsp;
    GitHub Actions &nbsp;&middot;&nbsp;
    Playwright &nbsp;&middot;&nbsp; k6 &nbsp;&middot;&nbsp; pytest &nbsp;&middot;&nbsp; Jest
    &nbsp;&middot;&nbsp; {now}
  </div>

</div>
</body>
</html>"""


# ── CLI Entry ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate QA HTML report")
    parser.add_argument("--results-dir", default="all-results")
    parser.add_argument("--project",     default="Project")
    parser.add_argument("--commit",      default="")
    parser.add_argument("--branch",      default="main")
    parser.add_argument("--model",       default="gpt-4o-mini")
    parser.add_argument("--output",      default="qa-report.html")
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY", "")

    print("📂 Loading results...")
    results = load_all(args.results_dir)

    for suite, data in results.items():
        if data:
            if "p95_ms" in data:
                print(f"   {suite:12} p95={data['p95_ms']}ms  errors={data['error_rate']}%")
            else:
                print(f"   {suite:12} passed={data['passed']}  failed={data['failed']}")

    print(f"\n🤖 Running AI analysis ({args.model})...")
    ai_text = ai_analysis(results, args.project, api_key, args.model)
    print(f"   → {ai_text[:80]}...")

    print("\n🖊️  Building HTML report...")
    html = build_html(results, args.project, args.commit, args.branch, args.model, ai_text)
    Path(args.output).write_text(html)
    print(f"   → {args.output} ({len(html) // 1024}KB)")

    # Machine-readable summary for the PR-comment step
    non_perf = {k: v for k, v in results.items() if v and "p95_ms" not in v}
    total_passed = sum(v.get("passed", 0) for v in non_perf.values())
    total_failed = sum(v.get("failed", 0) for v in non_perf.values())
    load = results.get("load", {}) or {}

    summary = {
        "total":      total_passed + total_failed,
        "passed":     total_passed,
        "failed":     total_failed,
        "ai_summary": ai_text,
        **{k: {"passed": v.get("passed", 0), "failed": v.get("failed", 0),
               "skipped": v.get("skipped", 0)}
           for k, v in non_perf.items()},
        "load": {
            "p95":    load.get("p95_ms", 0),
            "errors": load.get("error_rate", 0),
            "vus":    load.get("total_requests", 0),
        }
    }
    Path("all-results/report-summary.json").write_text(json.dumps(summary, indent=2))

    print(f"\n✅ Done — {total_passed} passed, {total_failed} failed")


if __name__ == "__main__":
    main()
