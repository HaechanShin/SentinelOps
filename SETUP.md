# SentinelOps Setup Guide

## Prerequisites

- **Docker Desktop** installed and running
- **Anthropic API Key** ([console.anthropic.com](https://console.anthropic.com)) or local Ollama
- **Steam API Key** ([steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey))

Optional:
- **Slack App** — only needed if you want real-time alert notifications

---

## 1. Clone & Configure

```bash
git clone <repo-url>
cd SentinelOps
cp .env.example .env
```

Open `.env` and fill in your API keys:

```env
AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-your-real-key
STEAM_API_KEY=your-steam-key
```

For local Qwen through Ollama, switch the AI block instead:

```env
AI_PROVIDER=ollama
LOCAL_LLM_BASE_URL=http://host.docker.internal:11434
LOCAL_LLM_MODEL=qwen3.6:latest
LOCAL_LLM_CONTEXT_TOKENS=16384
LOCAL_LLM_THINK=false
```

Use `http://localhost:11434` for `LOCAL_LLM_BASE_URL` if you run the Python app directly outside Docker.

Everything else has working defaults.

---

## 2. Start

```bash
docker compose up --build
```

First build takes 1-2 minutes. Once you see logs from all services, everything is ready.

---

## 3. Verify

Open these in your browser:

| URL | What you'll see |
|-----|-----------------|
| [localhost:8000/docs](http://localhost:8000/docs) | API documentation (Swagger UI) |
| [localhost:8001/sse](http://localhost:8001/sse) | MCP Server (SSE transport — streams `endpoint` event) |
| [localhost:3000](http://localhost:3000) | Grafana dashboard (login: admin / admin) |
| [localhost:9090](http://localhost:9090) | Prometheus metrics |

In Grafana, go to **Dashboards → SentinelOps — Community Monitoring** to see the main dashboard.

---

## 4. What Happens Automatically

Once started, the system runs on its own. Work is split into two cadences:

**Hourly (collection + sentiment analysis)**
1. Collects new Steam reviews + patch notes for the configured `STEAM_APP_ID` / `GAME_NAME`
2. AI analyzes any unanalyzed review (sentiment score + issue tag)

**Daily (alert detection + response drafting)** — runs once per day at `DAILY_ALERT_HOUR_UTC` (default `0` = UTC 00:00, which is KST 09:00)
3. Compares the most recent 24h to the prior 24h for sentiment drops and keyword spikes
4. If issue detected — the configured AI provider gathers context via MCP tools (similar issues, patch notes, past responses, sentiment trend, prior alerts, top complaints, response effectiveness)
5. Generates 3 response drafts (official, empathetic, concise) using the enriched context
6. LLM-as-judge evaluates each draft (relevance, tone, accuracy, actionability)
7. Sends Slack alerts with the MCP context summary + approve/reject buttons (if Slack configured)
8. On approval — drafts are stored as official responses and feed future draft context

Every pipeline run is tracked in the `pipeline_runs` table with status, timing, and counts.

You don't need to trigger anything manually. The first hourly collection runs immediately on startup; subsequent hourly runs follow `POLLING_INTERVAL_SECONDS`; the daily alert pipeline fires on its UTC cron schedule.

---

## 5. Dashboard Panels

The Grafana dashboard shows:

| Panel | What it tells you |
|-------|-------------------|
| **Average Sentiment (Hourly)** | Community mood over time (-1.0 to 1.0) |
| **Recommendation Ratio (Hourly)** | % of thumbs-up reviews per hour |
| **Complaint Categories (Not Recommended)** | What people complain about when they don't recommend |
| **Praise Categories (Recommended)** | What people appreciate when they recommend |
| **Recent Reviews** | Latest reviews with thumbs up/down, sentiment, and category |
| **Reviews Collected (Daily)** | How many reviews are being collected per day |
| **Analysis Coverage** | % of reviews that have been analyzed by AI |
| **Total Reviews** | Total number of reviews in the database |
| **Alerts & Drafts** | Number of open alerts and pending response drafts |

---

## 6. API Usage

### View collected posts
```bash
curl http://localhost:8000/api/v1/posts
```

### View sentiment trend
```bash
curl http://localhost:8000/api/v1/posts/sentiment/trend?hours=24
```

### View dashboard summary
```bash
curl http://localhost:8000/api/v1/dashboard/summary
```

### View drafts with evaluation scores
```bash
curl http://localhost:8000/api/v1/drafts
```

### Approve or reject a draft
```bash
curl -X POST http://localhost:8000/api/v1/drafts/{id}/review \
  -H "Content-Type: application/json" \
  -d '{"action": "approve"}'
```

### Manually trigger the pipeline
```bash
curl -X POST http://localhost:8000/api/v1/pipeline/run \
  -H "X-API-Key: your_api_secret_key"
```

Full API docs are at [localhost:8000/docs](http://localhost:8000/docs).

---

## 7. Slack Integration (Optional)

If you want Slack notifications:

1. Create a Slack App at [api.slack.com/apps](https://api.slack.com/apps)
2. Enable **Socket Mode** and get an App-Level Token (`xapp-...`)
3. Add Bot Token Scopes: `chat:write`, `commands`
4. Install the app to your workspace
5. Add these to your `.env`:

```env
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_SIGNING_SECRET=your-signing-secret
SLACK_APP_TOKEN=xapp-your-app-token
SLACK_ALERT_CHANNEL=#community-alerts
```

6. Restart: `docker compose restart slack-bot`

Without Slack configured, the system still works — it just won't send notifications.

---

## 8. MCP Server — Claude Desktop Integration (Optional)

The MCP server exposes 8 tools over SSE at `http://localhost:8001/sse`. You can connect Claude Desktop to query your community data interactively.

Add to your Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "sentinelops": {
      "command": "cmd",
      "args": [
        "/c",
        "C:\\Program Files\\nodejs\\npx.cmd",
        "-y",
        "mcp-remote@latest",
        "http://localhost:8001/sse",
        "--transport",
        "sse-only"
      ]
    }
  }
}
```

Available tools:
- `get_similar_issues` — Search past community issues by keyword
- `get_official_responses` — Get approved response templates by issue tag
- `get_sentiment_trend` — Hourly sentiment averages
- `get_patch_notes` — Recent patch notes for the configured game
- `get_alert_history` — Alert history with filters
- `get_community_summary` — Activity summary
- `get_top_complaints` — Top complaint topics in a recent window with example posts
- `get_response_effectiveness` — Sentiment shift for an issue tag before/after the latest approved response

These same tools are used internally by the pipeline. Claude mode uses native `tool_use`; local provider mode asks the model for a JSON tool plan and validates it before execution.

---

## 9. Backfill Historical Data

By default, the system only collects new reviews going forward. To load past reviews:

```bash
# Collect reviews from the last 30 days (no AI analysis, just store)
docker compose exec app python -m ingestion.backfill --days 30

# Collect + run AI analysis
docker compose exec app python -m ingestion.backfill --days 7 --analyze

# Collect 1 year of reviews, but only analyze the last 7 days
docker compose exec app python -m ingestion.backfill --days 365 --analyze --analyze-days 7
```

- Without `--analyze`: only collects and stores reviews (free, Steam API only)
- With `--analyze`: runs sentiment + category analysis on **all unanalyzed posts** within the period — not just newly collected ones (costs Anthropic API tokens)
- `--analyze-days`: limits analysis to recent N days (defaults to `--days` value if omitted). Use this to avoid re-analyzing a large backlog
- Duplicate reviews are automatically skipped

---

## 10. Stop & Reset

```bash
# Stop all services
docker compose down

# Stop and delete all data (fresh start)
docker compose down -v
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Dashboard shows no data | Wait 5 minutes for the first collection cycle, then refresh |
| "No such image" error | Run `docker compose up --build` instead of `docker compose up` |
| Port already in use | Stop other services using ports 8000, 8001, 3000, 5432, 6379, or 9090 |
| Grafana login doesn't work | Default credentials are `admin` / `admin` |
| Pipeline runs but 0 alerts | Normal if sentiment is stable. Daily alert pipeline only fires once per day at `DAILY_ALERT_HOUR_UTC` — check `pipeline_runs` table or run `curl -X POST localhost:8000/api/v1/pipeline/run -H "X-API-Key: ..."` to force a run |
| Local Qwen cannot connect from Docker | Keep Ollama running on the host and set `LOCAL_LLM_BASE_URL=http://host.docker.internal:11434` |
| Slack bot shows "idle" | Expected if `SLACK_APP_TOKEN` is not set. System works without Slack |
| MCP SSE returns no data | Check that postgres is healthy: `docker compose ps` |
| Worker stopped updating | Check `docker compose logs worker` for the most recent exception, then `docker compose restart worker`. Worker now has `restart: unless-stopped` and an APScheduler error listener |
