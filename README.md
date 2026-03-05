# email-digest

Automated daily email digest. Scrapes user-defined entities (YouTube creators, communities, web sources), summarizes with Claude, and delivers a formatted email each morning via GitHub Actions.

## Setup

1. Clone repo
2. Copy `.env.example` to `.env` and fill in API keys
3. Edit `config/entities.yaml` to define your sources
4. Add GitHub Actions secrets: `ANTHROPIC_API_KEY`, `RESEND_API_KEY`, `GH_PAT`
5. Push — the cron runs daily at 07:00 UTC

## Local dev

```bash
uv sync
uv run python -m email_digest.main --dry-run
uv run python -m email_digest.main --entity "Hacker News" --dry-run
```
