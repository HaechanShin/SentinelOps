# SentinelOps — PUBG Community AI Ops System

Real-time community monitoring and response drafting system for PUBG. Collects Steam reviews, analyzes sentiment, detects issues, and provides AI-generated response drafts with approval-gated workflows.

> Unofficial demo project. Not affiliated with KRAFTON or PUBG Studios.

## Architecture

```
                          ┌──────────────────────────────────────────┐
                          │           Feedback Loop                  │
                          │  approved drafts → official_responses    │
                          │  → MCP context for future drafts         │
                          └──────────────┬───────────────────────────┘
                                         │
Steam Reviews ──→ Data Ingestion ──→ Sentiment Analysis
                                         │
Steam News API ──→ Patch Notes      Alert Detection
                   Collection            │
                                   Context Gathering (AI provider)
                                   ├─ get_similar_issues
                                   ├─ get_patch_notes
                                   └─ get_official_responses
                                         │
                                   Response Drafting (3 tones)
                                         │
                                   LLM Evaluation (scored)
                                         │
                                   Slack Notification ──→ CM Approve/Reject
                                         │
                                   Observability (Prometheus + Grafana)
```

## Key Design Decisions

- **MCP-first intelligence layer**: Pipeline context is gathered through MCP tool functions — the same tools exposed to Claude Desktop for interactive querying.
- **Approval-gated workflow**: Drafts require human approval in Slack. Approved drafts feed back into `official_responses`, enriching future draft quality.
- **Selectable AI provider**: Claude is the default path and keeps native `tool_use` for context. Local/OpenAI-compatible providers use a JSON tool planner against the same MCP tool functions.
- **Pipeline accountability**: Every pipeline execution is tracked in `pipeline_runs` with status, timing, and counts.

## Tech Stack

- **Multi-Agent Framework:** LangGraph
- **LLM:** Anthropic Claude by default, or local Qwen through Ollama/OpenAI-compatible endpoints
- **API Server:** FastAPI
- **Database:** PostgreSQL + pgvector
- **Message Queue:** Redis (AOF persistence)
- **Notifications:** Slack (Bolt for Python)
- **MCP Server:** Python MCP SDK (SSE transport)
- **Observability:** Prometheus + Grafana + structlog
- **Container:** Docker Compose

## Quick Start

### 1. Environment Setup

```bash
cp .env.example .env
# Edit .env with your API keys (Anthropic, Steam)
```

Default Claude mode:

```env
AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-your-real-key
ANTHROPIC_MODEL=claude-sonnet-4-6
```

Local Qwen mode with Ollama running on your host:

```env
AI_PROVIDER=ollama
LOCAL_LLM_BASE_URL=http://host.docker.internal:11434
LOCAL_LLM_MODEL=qwen3.6:latest
LOCAL_LLM_CONTEXT_TOKENS=16384
LOCAL_LLM_THINK=false
```

### 2. Run with Docker Compose

```bash
docker compose up --build
```

This starts:
- **API Server** at `http://localhost:8000` (FastAPI + Swagger docs at `/docs`)
- **Worker** — scheduled Steam review + patch note collection and pipeline runs
- **Slack Bot** — interactive alert notifications (idle if tokens not configured)
- **MCP Server** at `http://localhost:8001/sse` (SSE transport)
- **PostgreSQL** at `localhost:5432`
- **Redis** at `localhost:6379` (AOF persistence)
- **Prometheus** at `http://localhost:9090`
- **Grafana** at `http://localhost:3000` (admin/admin)

### 3. Connect MCP Server to Claude Desktop

Add to your Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "sentinelops": {
      "command": "cmd",
      "args": [
        "/c",
        "npx",
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

> **Windows note:** If Claude Desktop can't find `npx`, replace `"npx"` with the full path (e.g., `"C:\\Program Files\\nodejs\\npx.cmd"`).
> Requires Node.js 18+. Restart Claude Desktop after editing the config.

### 4. Backfill Historical Data (Optional)

```bash
# Collect last 30 days and analyze all of them
docker compose exec app python -m ingestion.backfill --days 30 --analyze

# Collect 1 year of reviews, but only analyze the last 7 days
docker compose exec app python -m ingestion.backfill --days 365 --analyze --analyze-days 7
```

> `--analyze` targets **all unanalyzed posts** within the period, not just newly collected ones. Use `--analyze-days` to limit the analysis range and control API costs.

## Pipeline Flow

```
sentiment_node → alert_node → [context_node] → drafting_node → notify_node
                                    ↑                               │
                              AI provider                     Slack send
                              chooses context                 + slack_ts
                              MCP tool calls                  stored
```

1. **Data Ingestion** — Collects new Steam reviews (hourly) and patch notes from Steam News API
2. **Sentiment Analysis** — Configured AI provider classifies each review: sentiment score (-1.0 to 1.0) and issue tag
3. **Alert Detection** — Rolling window analysis detects sentiment drops and keyword spikes
4. **Context Gathering** — Claude native `tool_use` or local JSON planning calls MCP tools to retrieve similar past issues, patch notes, and approved responses
5. **Response Drafting** — Generates 3 drafts per alert (official, empathetic, concise) enriched with MCP context
6. **Evaluation** — LLM-as-judge scores each draft (relevance, tone, accuracy, actionability) and stores scores in DB
7. **Slack Notification** — Sends interactive messages with approve/edit/reject buttons
8. **Feedback Loop** — Approved drafts are stored as `official_responses`, retrieved by MCP in future alerts

Issue tags: `anti-cheat`, `server-stability`, `optimization`, `game-balance`, `new-content`, `matchmaking`, `bugs`, `monetization`, `general`

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/api/v1/posts` | List collected posts |
| GET | `/api/v1/posts/sentiment/trend` | Sentiment trend data |
| GET | `/api/v1/posts/tags/distribution` | Issue tag distribution |
| GET | `/api/v1/alerts` | List alerts |
| PATCH | `/api/v1/alerts/{id}` | Update alert status |
| GET | `/api/v1/alerts/stats/summary` | Alert statistics |
| GET | `/api/v1/drafts` | List response drafts (includes eval_scores) |
| POST | `/api/v1/drafts/{id}/review` | Approve/reject a draft |
| POST | `/api/v1/pipeline/run` | Manually trigger pipeline (requires `X-API-Key` header) |
| GET | `/api/v1/dashboard/summary` | Dashboard summary |

## MCP Server Tools

Available at `http://localhost:8001/sse` via SSE transport.

| Tool | Description |
|------|-------------|
| `get_similar_issues` | Search for similar past community issues |
| `get_official_responses` | Get approved response templates by issue tag |
| `get_sentiment_trend` | Hourly sentiment trend data |
| `get_patch_notes` | Recent PUBG patch notes (collected from Steam) |
| `get_alert_history` | Alert history with filters |
| `get_community_summary` | Community activity summary |

These tools serve dual purpose:
1. **Pipeline internal** — Called via Claude `tool_use` or local JSON planning during context gathering
2. **External clients** — Queryable from Claude Desktop or any MCP client

## Reliability

- Claude API calls use SDK-native retries. Local/OpenAI-compatible calls use configurable timeout and retry settings.
- Pipeline runs tracked in `pipeline_runs` table (started_at, completed_at, status, error_message)
- Slack notifications gracefully skip if tokens not configured
- Steam collection and pipeline execution are independent — one failing doesn't crash the other
- Redis uses AOF persistence to survive restarts

## Testing

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## Project Structure

```
sentinelops/
├── config.py                  # Pydantic settings
├── docker-compose.yml
├── Dockerfile
├── ingestion/                 # Data collection
│   ├── steam_collector.py     # Steam review collector
│   ├── news_collector.py      # Steam News API patch note collector
│   ├── backfill.py            # Historical review backfill utility
│   └── scheduler.py           # APScheduler worker
├── agents/                    # LangGraph agents
│   ├── graph.py               # Pipeline graph + provider-aware context + run tracking
│   ├── sentiment_agent.py     # AI sentiment classifier
│   ├── llm_client.py          # Anthropic, Ollama, and OpenAI-compatible client
│   ├── alert_agent.py         # Rolling window alert detector
│   └── drafting_agent.py      # 3-tone draft generator + LLM evaluator
├── mcp_server/                # MCP server (SSE)
│   └── server.py              # 6 tools over PostgreSQL
├── api/                       # FastAPI
│   ├── main.py
│   ├── schemas.py
│   └── routers/
├── slack_app/                 # Slack Bolt app
│   ├── app.py
│   └── handlers/
│       ├── alert_handler.py   # Slack Block Kit message builder
│       └── approval_handler.py # Approve/reject + official_responses accumulation
├── db/                        # Database
│   ├── models.py              # SQLAlchemy models (Post, Alert, Draft, PipelineRun, ...)
│   ├── engine.py
│   └── init.sql
├── observability/             # Monitoring
│   ├── prometheus.yml
│   └── grafana/
└── tests/
```
