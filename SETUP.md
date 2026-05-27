# SentinelOps Setup Guide

## Prerequisites

- **Docker Desktop** installed and running
- **Anthropic API Key** ([console.anthropic.com](https://console.anthropic.com))
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
ANTHROPIC_API_KEY=sk-ant-your-real-key
STEAM_API_KEY=your-steam-key
```

That's it. Everything else has working defaults.

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
| [localhost:3000](http://localhost:3000) | Grafana dashboard (login: admin / admin) |

In Grafana, go to **Dashboards → SentinelOps — Community Monitoring** to see the main dashboard.

---

## 4. What Happens Automatically

Once started, the system runs on its own:

1. **Every hour** — Collects new Steam reviews for PUBG (cursor-based, no duplicates)
2. **After collection** — AI analyzes each review (sentiment score + issue category)
3. **After analysis** — Checks for community issues (sentiment drops, complaint spikes)
4. **If issue detected** — Generates response drafts and sends Slack alerts (if configured)

You don't need to trigger anything manually. The first collection runs immediately on startup, then every hour after that.

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

### View dashboard summary
```bash
curl http://localhost:8000/api/v1/dashboard/summary
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

## 8. Backfill Historical Data

By default, the system only collects new reviews going forward. To load past reviews:

```bash
# Collect reviews from the last 30 days (no AI analysis, just store)
docker compose exec app python -m ingestion.backfill --days 30

# Collect + run AI analysis on all of them
docker compose exec app python -m ingestion.backfill --days 7 --analyze
```

- Without `--analyze`: only collects and stores reviews (free, Steam API only)
- With `--analyze`: also runs sentiment + category analysis (costs Anthropic API tokens)
- Duplicate reviews are automatically skipped

---

## 9. Stop & Reset

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
| Port already in use | Stop other services using ports 8000, 3000, 5432, 6379, or 9090 |
| Grafana login doesn't work | Default credentials are `admin` / `admin` |
