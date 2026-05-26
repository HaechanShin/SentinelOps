# SentinelOps — PUBG Community AI Ops System

Real-time community monitoring and response drafting system for PUBG. Collects Steam reviews, analyzes sentiment, detects issues, and provides AI-generated response drafts with approval-gated workflows.

## Architecture

```
Steam Reviews → Data Ingestion → Sentiment Analysis Agent
                                        ↓
                                Alert Detection Agent
                                        ↓
                              Response Drafting Agent → Slack (Approval Gateway)
                                        ↓
                              Observability (Prometheus + Grafana)
```

## Tech Stack

- **Multi-Agent Framework:** LangGraph
- **LLM:** Anthropic Claude API (claude-sonnet-4-6)
- **API Server:** FastAPI
- **Database:** PostgreSQL + pgvector
- **Message Queue:** Redis
- **Notifications:** Slack (Bolt for Python)
- **MCP Server:** Python MCP SDK
- **Observability:** Prometheus + Grafana + structlog
- **Container:** Docker Compose

## Quick Start

### 1. Environment Setup

```bash
cp .env.example .env
# Edit .env with your API keys (Anthropic, Steam)
```

### 2. Run with Docker Compose

```bash
docker compose up --build
```

This starts:
- **API Server** at `http://localhost:8000` (FastAPI + Swagger docs at `/docs`)
- **Worker** — scheduled Steam review collection and pipeline runs
- **Slack Bot** — interactive alert notifications
- **MCP Server** at `http://localhost:8001`
- **PostgreSQL** at `localhost:5432`
- **Redis** at `localhost:6379`
- **Prometheus** at `http://localhost:9090`
- **Grafana** at `http://localhost:3000` (admin/admin)

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
| GET | `/api/v1/drafts` | List response drafts |
| POST | `/api/v1/drafts/{id}/review` | Approve/reject a draft |
| POST | `/api/v1/pipeline/run` | Manually trigger pipeline (requires `X-API-Key` header) |
| GET | `/api/v1/dashboard/summary` | Dashboard summary |

## MCP Server Tools

| Tool | Description |
|------|-------------|
| `get_similar_issues` | Search for similar past community issues |
| `get_official_responses` | Get response templates by issue tag |
| `get_sentiment_trend` | Hourly sentiment trend data |
| `get_patch_notes` | Recent PUBG patch notes |
| `get_alert_history` | Alert history with filters |
| `get_community_summary` | Community activity summary |

## Pipeline Flow

1. **Data Ingestion** — Collects Steam reviews every 5 minutes
2. **Sentiment Analysis** — Claude API analyzes each review for sentiment (-1.0 to 1.0) and issue tags
3. **Alert Detection** — Rolling window analysis detects sentiment drops and keyword spikes
4. **Response Drafting** — Generates 3 response drafts per alert (official, empathetic, concise)
5. **Slack Notification** — Sends interactive messages with approve/edit/reject buttons
6. **Evaluation** — LLM-as-judge scores drafts on relevance, tone, accuracy, actionability

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
│   ├── steam_collector.py
│   └── scheduler.py
├── agents/                    # LangGraph agents
│   ├── graph.py               # Main agent graph
│   ├── sentiment_agent.py
│   ├── alert_agent.py
│   └── drafting_agent.py
├── mcp_server/                # MCP server
│   └── server.py
├── api/                       # FastAPI
│   ├── main.py
│   ├── schemas.py
│   └── routers/
├── slack_app/                 # Slack Bolt app
│   ├── app.py
│   └── handlers/
├── db/                        # Database
│   ├── models.py
│   ├── engine.py
│   └── init.sql
├── observability/             # Monitoring
│   ├── prometheus.yml
│   └── grafana/
└── tests/
```
