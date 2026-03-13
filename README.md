# 🧪 Universal QA Platform (OpenAI Edition)

> One CI/CD pipeline for all your projects. AI-generated tests via OpenAI. Zero infrastructure needed.

---

## ✨ What It Does

Every time you push code, this platform automatically:

| Step | What happens |
|------|-------------|
| 🤖 **AI Analysis** | OpenAI reads your diff + code, generates 7 test suites |
| 💨 **Smoke** | Health checks gate — fail = pipeline stops |
| 🔍 **Sanity** | Tests focused on changed modules (parallel) |
| 🔌 **API** | Endpoint coverage, schema validation (parallel) |
| 🔁 **Regression** | Full feature suite |
| 👤 **UAT** | Playwright E2E user journeys |
| 📈 **Load** | k6 performance test (50 VUs, 60s) |
| 💥 **Stress** | k6 spike test — nightly only |
| 📊 **Report** | Rich HTML report + AI failure analysis, saved 30 days |
| 💬 **PR Comment** | Auto-posts pass/fail table on every PR |

---

## 💰 OpenAI Model Guide

| Model | Cost per full run | Quality | Recommendation |
|-------|-------------------|---------|----------------|
| `gpt-4o-mini` | ~$0.01–0.03 | Very good | ✅ **Default — use this** |
| `gpt-4o` | ~$0.10–0.30 | Excellent | For complex projects |
| `gpt-4-turbo` | ~$0.20–0.40 | Excellent | Not worth vs 4o |

Each run = **9 OpenAI calls** (7 test types + 1 AI report analysis + 1 stack detection).
`gpt-4o-mini` is nearly as good as `gpt-4o` for code generation at 10× lower cost.

---

## 🚀 Setup (5 minutes)

### 1. Fork/clone this repo to your GitHub org

```bash
git clone https://github.com/YOUR_ORG/qa-platform
cd qa-platform
# push to your org as-is
```

### 2. Get an OpenAI API key

1. Go to [platform.openai.com](https://platform.openai.com)
2. API Keys → Create new secret key
3. Add a small usage limit ($5–10/month is plenty for `gpt-4o-mini`)

### 3. Add the secret to your GitHub org

```
GitHub Org → Settings → Secrets and variables → Actions → New secret
Name:  OPENAI_API_KEY
Value: sk-proj-...your key...
```

### 4. Copy 12 lines into any project

Create `.github/workflows/qa.yml` in your project:

```yaml
name: QA
on:
  push:
    branches: ["**"]
  pull_request:

jobs:
  qa:
    uses: YOUR_ORG/qa-platform/.github/workflows/master-qa.yml@main
    with:
      project-name: "your-app-name"
      openai-model: "gpt-4o-mini"
    secrets:
      OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

**That's it.** Push any code and watch the pipeline run.

---

## 📁 File Structure

```
qa-platform/
├── .github/workflows/
│   ├── master-qa.yml                   ← Central reusable pipeline
│   └── nightly.yml                     ← Scheduled stress tests
│
├── scripts/
│   ├── detect_stack.py                 ← Auto-detects language/framework
│   ├── ai/
│   │   └── generate_tests.py           ← OpenAI test generation (all 7 types)
│   ├── smoke/
│   │   └── check_results.py            ← Pipeline gate
│   └── report/
│       └── generate_report.py          ← HTML report + AI analysis
│
└── example-project/
    └── .github/workflows/
        └── qa.yml                      ← Copy this to your projects
```

---

## ⚙️ All Configuration Options

```yaml
jobs:
  qa:
    uses: YOUR_ORG/qa-platform/.github/workflows/master-qa.yml@main
    with:
      project-name:  "my-app"             # required — shown in reports
      base-url:      "http://localhost:3000"  # your app's local URL
      openai-model:  "gpt-4o-mini"        # or gpt-4o, gpt-4-turbo
      run-load:      true                 # run load tests (default: true)
      run-stress:    false                # run stress tests (default: false)
      load-vus:      50                   # virtual users for load test
    secrets:
      OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

---

## 🗓️ What Runs When

| Event | Smoke | Sanity | API | Regression | UAT | Load | Stress |
|-------|:-----:|:------:|:---:|:----------:|:---:|:----:|:------:|
| Push  | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| PR    | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| Nightly | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

Stress runs only at 2 AM UTC (nightly.yml) to avoid slowing down your dev loop.

---

## 📊 Reading the Report

After every run:
`GitHub → Actions → [run name] → Artifacts → qa-report-{commit}`

The HTML report includes:
- **Pass/fail matrix** for all 7 test suites
- **Load test metrics** — p50, p95 latency, error rate, total requests
- **OpenAI failure analysis** — plain English explanation of what failed and what to fix
- **Failure detail cards** — exact test names and error messages

The PR comment posts a summary table automatically.

---

## 🛠️ Language Support

| Language | Detected by | Unit runner | E2E runner |
|----------|------------|-------------|------------|
| JavaScript/TypeScript | `package.json` | Jest | Playwright |
| Python | `requirements.txt` / `pyproject.toml` | pytest | playwright-python |
| Java | `pom.xml` / `build.gradle` | JUnit (coming) | Playwright |
| Go | `go.mod` | go test (coming) | Playwright |

Both JS and Python are fully supported today.

---

## 🆘 Troubleshooting

**AI generation returns fallback tests:**
- Verify `OPENAI_API_KEY` secret is set at org level
- Check if you have credits in your OpenAI account
- Try `gpt-4o-mini` if `gpt-4o` quota is exhausted

**Smoke gate blocks the pipeline:**
- Check that your app actually starts — look at the "Start app" step logs
- Ensure `base-url` matches where your app listens
- Check required environment variables are set in your project's repo secrets

**k6 not found error:**
- The workflow installs k6 via apt — check the runner has internet access
- Alternatively, add k6 to a custom Docker runner image

**Report artifact is empty:**
- All test stages must complete (even with failures) before the report runs
- Check the `report` job logs for Python errors

---

## 🔮 Roadmap

- [ ] Self-healing Playwright selectors (AI fixes broken locators)
- [ ] ML-based test prioritization (run likely-to-fail tests first)
- [ ] Visual regression with pixelmatch
- [ ] Semgrep + Trivy security scanning stage
- [ ] Coverage gating (block merge if coverage drops)
- [ ] Slack notification webhook
- [ ] Test history trending (SQLite in repo)

---

Built with GitHub Actions · OpenAI · Playwright · k6 · pytest · Jest
