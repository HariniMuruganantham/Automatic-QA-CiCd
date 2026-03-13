# 🧪 Automatic-QA-CiCd

> One universal CI/CD pipeline for all your projects.
> AI-generated tests (OpenAI) · Zero infrastructure · Plug in any repo with 12 lines.

---

## ✨ What Happens on Every Push

| Stage | Tool | What it checks |
|-------|------|----------------|
| 🤖 **AI Test Gen** | OpenAI gpt-4o-mini | Reads your diff, generates 7 test suites |
| 💨 **Smoke** | Jest / pytest | App starts, critical endpoints alive — **GATE** |
| 🔍 **Sanity** | Jest / pytest | Changed modules still work (parallel) |
| 🔌 **API** | Newman + pytest | Endpoint coverage, schemas, error codes (parallel) |
| 🔁 **Regression** | Playwright + pytest | Full feature suite |
| 👤 **UAT** | Playwright | End-to-end user journeys |
| 📈 **Load** | k6 | 50 VUs × 60s, p95 < 500ms |
| 💥 **Stress** | k6 | Spike to 500 VUs — **nightly only** |
| 📊 **Report** | Custom HTML + OpenAI | AI failure analysis, saved 30 days |
| 💬 **PR Comment** | GitHub Actions bot | Pass/fail table on every PR |

---

## 💰 OpenAI Cost

| Model | Per run | Recommendation |
|-------|---------|----------------|
| `gpt-4o-mini` | ~$0.01–0.03 | ✅ Default |
| `gpt-4o`      | ~$0.10–0.30 | Best quality |

Each run = 9 OpenAI calls (7 test types + report analysis + stack hint).

---

## 🚀 Setup (5 minutes)

### Step 1 — Add your OpenAI API key as a secret

```
This repo → Settings → Secrets and variables → Actions → New secret
Name:   OPENAI_API_KEY
Value:  sk-proj-...
```

### Step 2 — Plug any project in (copy 12 lines)

Create `.github/workflows/qa.yml` in **your project repo**:

```yaml
name: QA
on:
  push:
    branches: ["**"]
  pull_request:
    branches: [main, develop]
  schedule:
    - cron: "0 2 * * *"

jobs:
  qa:
    uses: HariniMuruganantham/Automatic-QA-CiCd/.github/workflows/master-qa.yml@main
    with:
      project-name:  "your-app-name"        # ← change this
      base-url:      "http://localhost:3000" # ← change if different port
      openai-model:  "gpt-4o-mini"
      run-load:      true
      run-stress:    ${{ github.event_name == 'schedule' }}
      load-vus:      50
    secrets:
      OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

Push any code — the pipeline fires automatically.

---

## 📁 Repo Structure

```
Automatic-QA-CiCd/
│
├── .github/workflows/
│   ├── master-qa.yml              ← Central reusable pipeline (all 7 stages)
│   └── nightly.yml                ← Scheduled stress tests (2 AM UTC)
│
├── scripts/
│   ├── detect_stack.py            ← Reads package.json/requirements.txt,
│   │                                 gets git diff, outputs language/framework
│   ├── ai/
│   │   └── generate_tests.py      ← Calls OpenAI, writes 7 test files
│   ├── smoke/
│   │   └── check_results.py       ← Gate: exits non-zero if smoke fails
│   └── report/
│       └── generate_report.py     ← Collects results → HTML + AI analysis
│
├── sample-app/                    ← Minimal Flask app to test the pipeline
│   ├── app.py
│   ├── requirements.txt
│   └── .github/workflows/qa.yml
│
├── example-project/               ← Template: copy qa.yml to your projects
│   └── .github/workflows/
│       └── qa.yml
│
└── README.md
```

---

## ⚙️ All Configuration Options

```yaml
with:
  project-name:  "my-app"               # required — shown in reports & PR comment
  base-url:      "http://localhost:3000" # where your app runs during tests
  openai-model:  "gpt-4o-mini"          # gpt-4o-mini | gpt-4o | gpt-4-turbo
  run-load:      true                   # run k6 load test (default: true)
  run-stress:    false                  # run k6 stress test (default: false)
  load-vus:      50                     # virtual users for load test
```

---

## 🗓️ What Runs When

| Event | Smoke | Sanity | API | Regression | UAT | Load | Stress |
|-------|:-----:|:------:|:---:|:----------:|:---:|:----:|:------:|
| Every push | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| Pull request | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| Nightly 2AM | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

**Smoke is a gate** — if it fails, all other stages are skipped.

---

## 🛠️ Language Support

| Language | Detected by | Unit/Integration | E2E |
|----------|------------|-----------------|-----|
| JavaScript / TypeScript | `package.json` | Jest | Playwright |
| Python | `requirements.txt` / `pyproject.toml` | pytest | playwright-python |
| Java | `pom.xml` / `build.gradle` | *(coming)* | Playwright |
| Go | `go.mod` | *(coming)* | Playwright |

---

## 📊 Reading Reports

Every run saves a report artifact:
`Actions → [run] → Artifacts → qa-report-{commit sha}`

The HTML report includes:
- **Pass/fail matrix** — all 7 suites at a glance
- **Load metrics** — p50, p95, total requests, error rate
- **OpenAI failure analysis** — plain English, actionable
- **Failure detail cards** — exact test names + error messages

PRs get an automatic comment with a summary table.

---

## 🆘 Troubleshooting

| Problem | Fix |
|---------|-----|
| AI generates fallback/empty tests | Check `OPENAI_API_KEY` secret is set, account has credits |
| Smoke gate blocks every run | Verify app starts — check "Start application" step logs |
| k6 not found | Workflow installs it automatically; check runner has internet |
| Report artifact is empty | Look at `report` job logs for Python errors |
| `has-api: false` — no API tests | Add `api/` or `routes/` folder to your project |

---

## 🔮 Roadmap

- [ ] Self-healing Playwright selectors
- [ ] ML test prioritization (run most-likely-to-fail first)
- [ ] Visual regression (pixelmatch)
- [ ] Semgrep + Trivy security scanning
- [ ] Coverage gating (block merge if coverage drops)
- [ ] Slack notifications
- [ ] Test history trending (SQLite)

---

Built with GitHub Actions · OpenAI · Playwright · k6 · pytest · Jest · Flask
