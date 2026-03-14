#!/usr/bin/env python3
"""
scripts/report/generate_report.py
----------------------------------
Collects all test results, generates a rich HTML QA report,
uses OpenAI for intelligent failure analysis.
Handles collector errors (import failures) as well as test failures.
"""

import os
import json
import argparse
from pathlib import Path
from datetime import datetime
from openai import OpenAI

# ── Parsers ────────────────────────────────────────────────────────────────────

def parse_jest(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text())
        failures = []
        for suite in data.get("testResults", []):
            for t in suite.get("testResults", []):
                if t.get("status") == "failed":
                    failures.append({
                        "name":    t.get("fullName", "unknown"),
                        "message": (t.get("failureMessages") or [""])[0][:400],
                    })
        return {
            "passed":   data.get("numPassedTests",  0),
            "failed":   data.get("numFailedTests",  0),
            "skipped":  data.get("numPendingTests", 0),
            "total":    data.get("numTotalTests",   0),
            "failures": failures[:5],
        }
    except Exception as e:
        return _empty(f"jest parse error: {e}")


def parse_pytest(path: str) -> dict:
    try:
        data     = json.loads(Path(path).read_text())
        s        = data.get("summary", {})
        exitcode = data.get("exitcode", 0)

        failures = []

        # Collector errors count as failures
        for c in data.get("collectors", []):
            if c.get("outcome") == "failed":
                repr_ = str(c.get("longrepr", ""))
                # Extract the meaningful error line
                error_line = next(
                    (l.strip() for l in repr_.splitlines()
                     if "Error" in l or "error" in l),
                    repr_[:200]
                )
                failures.append({
                    "name":    c.get("nodeid", "collection error"),
                    "message": error_line,
                })

        for t in data.get("tests", []):
            if t.get("outcome") == "failed":
                failures.append({
                    "name":    t.get("nodeid", "unknown"),
                    "message": str((t.get("call") or {}).get("longrepr", ""))[:400],
                })

        # exitcode=2 means collection error even if summary shows 0 tests
        collection_failed = exitcode == 2 and s.get("total", 0) == 0

        return {
            "passed":           s.get("passed",  0),
            "failed":           s.get("failed",  0) + len([f for f in failures if "collection error" in f["name"]]),
            "skipped":          s.get("skipped", 0),
            "total":            max(s.get("total", 0), len(failures)),
            "failures":         failures[:5],
            "collection_error": collection_failed,
        }
    except Exception as e:
        return _empty(f"pytest parse error: {e}")


def parse_k6(path: str) -> dict:
    try:
        durations, errors, reqs = [], [], []
        with open(path) as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                    if obj.get("type") != "Point":
                        continue
                    m   = obj.get("metric", "")
                    val = obj.get("data", {}).get("value", 0)
                    if   m == "http_req_duration": durations.append(val)
                    elif m == "http_req_failed":   errors.append(val)
                    elif m == "http_reqs":         reqs.append(val)
                except Exception:
                    pass

        n          = len(durations)
        ds         = sorted(durations)
        p50        = round(ds[n // 2], 2)          if n else 0
        p95        = round(ds[int(n * 0.95)], 2)   if n else 0
        error_rate = round(sum(errors) / max(len(errors), 1) * 100, 2)

        return {
            "total_requests": int(sum(reqs)),
            "p50_ms":     p50,
            "p95_ms":     p95,
            "error_rate": error_rate,
            "passed":     1 if error_rate < 5 else 0,
            "failed":     0 if error_rate < 5 else 1,
            "skipped":    0,
        }
    except Exception as e:
        return {
            "total_requests": 0, "p50_ms": 0, "p95_ms": 0,
            "error_rate": 0, "passed": 0, "failed": 0, "skipped": 0,
            "note": str(e),
        }


def _empty(note: str = "") -> dict:
    return {"passed": 0, "failed": 0, "skipped": 0,
            "total": 0, "failures": [], "note": note}


# ── Loader ─────────────────────────────────────────────────────────────────────

def load_all(results_dir: str) -> dict:
    base = Path(results_dir)
    out  = {}

    def find(*pats):
        for pat in pats:
            for p in base.rglob(pat):
                return str(p)
        return None

    def auto_parse(path):
        if not path:
            return _empty("file not found")
        try:
            raw = json.loads(Path(path).read_text())
            return parse_jest(path) if "numTotalTests" in raw else parse_pytest(path)
        except Exception as e:
            return _empty(str(e))

    out["smoke"]      = auto_parse(find("smoke-results/smoke-results.json",   "*smoke*.json"))
    out["sanity"]     = auto_parse(find("sanity-results/sanity-results.json",  "*sanity*.json"))
    out["api"]        = auto_parse(find("api-results/api-results.json",        "*api-results*.json"))
    out["regression"] = auto_parse(find("regression-results/regression-py-results.json",
                                        "*regression*.json"))
    out["uat"]        = auto_parse(find("uat-results/uat-results.json", "*uat*.json"))

    f = find("load-results/load-results.json", "*load-results*.json")
    out["load"] = parse_k6(f) if f else {
        "total_requests": 0, "p50_ms": 0, "p95_ms": 0,
        "error_rate": 0, "passed": 0, "failed": 0, "skipped": 0,
    }

    f = find("stress-results/stress-results.json", "*stress*.json")
    out["stress"] = parse_k6(f) if f else None

    return out


# ── AI analysis ────────────────────────────────────────────────────────────────

def ai_analysis(results: dict, project: str, api_key: str, model: str) -> str:
    if not api_key:
        return "AI analysis skipped — OPENAI_API_KEY not set."

    lines = []
    for suite, data in results.items():
        if data is None:
            continue
        if "p95_ms" in data:
            lines.append(f"{suite}: p95={data['p95_ms']}ms, error_rate={data['error_rate']}%")
        else:
            col_err = data.get("collection_error", False)
            fail_names = [f["name"] for f in data.get("failures", [])[:3]]
            lines.append(
                f"{suite}: passed={data['passed']}, failed={data['failed']}"
                + (" [COLLECTION ERROR]" if col_err else "")
                + (f" | failing: {', '.join(fail_names)}" if fail_names else "")
            )

    prompt = f"""QA test run summary for '{project}':
{chr(10).join(lines)}

Write 3-4 sentences of plain text (no markdown, no bullet points):
1. Overall health assessment
2. Most critical issues — be specific about test/suite names
3. Top recommended action for the team"""

    try:
        client = OpenAI(api_key=api_key)
        resp   = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=280,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"AI analysis failed: {e}"


# ── HTML ───────────────────────────────────────────────────────────────────────

def build_html(results: dict, project: str, commit: str,
               branch: str, model: str, ai_text: str) -> str:

    now       = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    short_sha = (commit or "unknown")[:7]

    non_perf     = {k: v for k, v in results.items() if v and "p95_ms" not in v}
    total_passed = sum(v.get("passed", 0) for v in non_perf.values())
    total_failed = sum(v.get("failed", 0) for v in non_perf.values())
    overall      = "PASS" if total_failed == 0 else "FAIL"
    oc           = "#10b981" if overall == "PASS" else "#ef4444"

    def badge(failed: int, col_err: bool = False) -> str:
        if col_err:
            return '<span class="badge col-err">ERR</span>'
        cls = "pass" if failed == 0 else "fail"
        return f'<span class="badge {cls}">{"PASS" if failed == 0 else "FAIL"}</span>'

    def suite_row(icon: str, label: str, key: str) -> str:
        data = results.get(key)
        if data is None:
            return (f'<tr><td class="sn">{icon} {label}</td>'
                    f'<td colspan="4" style="color:#6b7280;font-size:12px">Not run</td></tr>')
        if "p95_ms" in data:
            ok  = data.get("error_rate", 100) < 5
            cls = "pass" if ok else "fail"
            return f"""<tr>
              <td class="sn">{icon} {label}</td>
              <td><span class="badge {cls}">{"PASS" if ok else "FAIL"}</span></td>
              <td>{data["total_requests"]} req</td>
              <td>p50:{data["p50_ms"]}ms &nbsp; p95:{data["p95_ms"]}ms</td>
              <td style="color:{'#10b981' if ok else '#ef4444'}">{data["error_rate"]}% err</td>
            </tr>"""
        col_err = data.get("collection_error", False)
        return f"""<tr>
          <td class="sn">{icon} {label}</td>
          <td>{badge(data["failed"], col_err)}</td>
          <td style="color:#10b981">✓ {data["passed"]}</td>
          <td style="color:#ef4444">✗ {data["failed"]}</td>
          <td style="color:#6b7280">⊘ {data["skipped"]}</td>
        </tr>"""

    def failure_cards(key: str, icon: str, label: str) -> str:
        data = results.get(key, {})
        if not data or not data.get("failures"):
            return ""
        cards = "".join(f"""<div class="fc">
          <div class="fn">{f["name"]}</div>
          <pre class="fm">{f["message"].replace("<","&lt;").replace(">","&gt;")[:450]}</pre>
        </div>""" for f in data["failures"])
        return f'<div class="fg"><h3>{icon} {label}</h3>{cards}</div>'

    failures_html = "".join([
        failure_cards("smoke",      "💨", "Smoke"),
        failure_cards("sanity",     "🔍", "Sanity"),
        failure_cards("api",        "🔌", "API"),
        failure_cards("regression", "🔁", "Regression"),
        failure_cards("uat",        "👤", "UAT"),
    ])
    failures_block = (
        f'<div class="section"><h2>❌ Failure Details</h2>{failures_html}</div>'
        if failures_html else ""
    )

    load = results.get("load", {}) or {}

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>QA Report — {project}</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Inter:wght@400;500;600;700;800&display=swap');
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Inter',sans-serif;background:#0f1117;color:#e2e8f0;padding:40px 24px;min-height:100vh}}
.wrap{{max-width:980px;margin:0 auto}}
.hdr{{display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:20px;margin-bottom:32px}}
.title{{font-size:clamp(24px,5vw,40px);font-weight:800;letter-spacing:-.03em;line-height:1.1}}
.title .grad{{background:linear-gradient(135deg,#6366f1 0%,#a78bfa 50%,#34d399 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.meta{{font-family:'JetBrains Mono',monospace;font-size:12px;color:#6b7280;margin-top:10px}}
.ob{{font-size:18px;font-weight:700;padding:10px 24px;border-radius:10px;border:1.5px solid {oc}44;background:{oc}18;color:{oc};white-space:nowrap}}
.div{{height:1px;background:linear-gradient(90deg,transparent,#6366f1,#a78bfa,#34d399,transparent);margin:0 0 28px}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:12px;margin-bottom:22px}}
.stat{{background:#1a1d27;border:1px solid #2d3148;border-radius:12px;padding:18px 14px;text-align:center}}
.sv{{font-size:34px;font-weight:800;line-height:1}}
.sl{{font-size:10px;color:#6b7280;margin-top:6px;text-transform:uppercase;letter-spacing:.06em}}
.section{{background:#1a1d27;border:1px solid #2d3148;border-radius:14px;padding:22px;margin-bottom:18px}}
.section h2{{font-size:14px;font-weight:600;color:#a78bfa;margin-bottom:16px;letter-spacing:.03em}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;padding:9px 13px;font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:#475569;border-bottom:1px solid #2d3148}}
td{{padding:12px 13px;border-bottom:1px solid #1e2030}}
tr:last-child td{{border-bottom:none}}
.sn{{font-weight:500}}
.badge{{font-size:10px;font-weight:700;padding:3px 9px;border-radius:999px;letter-spacing:.05em}}
.badge.pass{{background:#10b98120;color:#10b981;border:1px solid #10b98140}}
.badge.fail{{background:#ef444420;color:#ef4444;border:1px solid #ef444440}}
.badge.col-err{{background:#f59e0b20;color:#f59e0b;border:1px solid #f59e0b40}}
.ailbl{{font-size:10px;text-transform:uppercase;letter-spacing:.12em;color:#a78bfa;font-weight:600;margin-bottom:10px}}
.aibox{{background:#111827;border-left:3px solid #a78bfa;border-radius:0 10px 10px 0;padding:16px 20px;font-size:14px;line-height:1.75;color:#cbd5e1}}
.fg{{margin-bottom:18px}}
.fg h3{{font-size:13px;font-weight:600;color:#ef4444;margin-bottom:10px}}
.fc{{background:#1f0a0a;border:1px solid #3d1515;border-radius:8px;padding:14px;margin-bottom:8px}}
.fn{{font-size:13px;font-weight:600;color:#fca5a5;margin-bottom:8px}}
.fm{{font-family:'JetBrains Mono',monospace;font-size:11px;color:#9ca3af;white-space:pre-wrap;overflow:auto;max-height:120px}}
.foot{{text-align:center;font-size:11px;color:#374151;margin-top:36px;padding-top:18px;border-top:1px solid #1e2030}}
</style>
</head>
<body>
<div class="wrap">
  <div class="hdr">
    <div>
      <div class="title">QA Report &mdash; <span class="grad">{project}</span></div>
      <div class="meta">Branch: {branch} &nbsp;&middot;&nbsp; Commit: {short_sha} &nbsp;&middot;&nbsp; {now} &nbsp;&middot;&nbsp; Model: {model}</div>
    </div>
    <div class="ob">{'✅' if overall == 'PASS' else '❌'} {overall}</div>
  </div>
  <div class="div"></div>
  <div class="stats">
    <div class="stat"><div class="sv" style="color:#10b981">{total_passed}</div><div class="sl">Passed</div></div>
    <div class="stat"><div class="sv" style="color:#ef4444">{total_failed}</div><div class="sl">Failed</div></div>
    <div class="stat"><div class="sv" style="color:#6366f1">{load.get("total_requests",0)}</div><div class="sl">Load Req</div></div>
    <div class="stat"><div class="sv" style="color:#f59e0b">{load.get("p95_ms",0)}<span style="font-size:15px">ms</span></div><div class="sl">p95</div></div>
    <div class="stat"><div class="sv" style="color:{'#10b981' if load.get('error_rate',100)<5 else '#ef4444'}">{load.get("error_rate",0)}<span style="font-size:15px">%</span></div><div class="sl">Error Rate</div></div>
  </div>
  <div class="section">
    <h2>🤖 AI Analysis</h2>
    <div class="ailbl">OpenAI &middot; {model}</div>
    <div class="aibox">{ai_text}</div>
  </div>
  <div class="section">
    <h2>📊 Test Suite Results</h2>
    <table>
      <thead><tr><th>Suite</th><th>Status</th><th>Passed / Requests</th><th>Failed / Latency</th><th>Skipped / Errors</th></tr></thead>
      <tbody>
        {suite_row("💨","Smoke",      "smoke")}
        {suite_row("🔍","Sanity",     "sanity")}
        {suite_row("🔌","API Tests",  "api")}
        {suite_row("🔁","Regression", "regression")}
        {suite_row("👤","UAT",        "uat")}
        {suite_row("📈","Load Test",  "load")}
        {suite_row("💥","Stress Test","stress")}
      </tbody>
    </table>
  </div>
  {failures_block}
  <div class="foot">Automatic-QA-CiCd &nbsp;&middot;&nbsp; OpenAI {model} &nbsp;&middot;&nbsp; GitHub Actions &nbsp;&middot;&nbsp; Playwright &nbsp;&middot;&nbsp; k6 &nbsp;&middot;&nbsp; pytest &nbsp;&middot;&nbsp; {now}</div>
</div>
</body>
</html>"""


# ── Entry ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
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
        if data is None:
            print(f"   {suite:12} — not run")
        elif "p95_ms" in data:
            print(f"   {suite:12} p95={data['p95_ms']}ms  err={data['error_rate']}%")
        else:
            col = " [collection error]" if data.get("collection_error") else ""
            print(f"   {suite:12} passed={data['passed']} failed={data['failed']}{col}")

    print(f"\n🤖 AI analysis ({args.model})...")
    ai_text = ai_analysis(results, args.project, api_key, args.model)
    print(f"   {ai_text[:90]}...")

    print("\n🖊️  Building HTML...")
    html = build_html(results, args.project, args.commit, args.branch, args.model, ai_text)
    Path(args.output).write_text(html)
    print(f"   Saved → {args.output}  ({len(html)//1024} KB)")

    non_perf     = {k: v for k, v in results.items() if v and "p95_ms" not in v}
    total_passed = sum(v.get("passed", 0) for v in non_perf.values())
    total_failed = sum(v.get("failed", 0) for v in non_perf.values())
    load         = results.get("load", {}) or {}

    summary = {
        "total":      total_passed + total_failed,
        "passed":     total_passed,
        "failed":     total_failed,
        "ai_summary": ai_text,
        **{k: {"passed": v.get("passed", 0), "failed": v.get("failed", 0),
               "skipped": v.get("skipped", 0)}
           for k, v in non_perf.items()},
        "load": {
            "p95":    load.get("p95_ms",     0),
            "errors": load.get("error_rate", 0),
            "vus":    load.get("total_requests", 0),
        },
    }

    out_dir = Path(args.results_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report-summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\n✅ Done — {total_passed} passed, {total_failed} failed")


if __name__ == "__main__":
    main()