<div align="center">
  <img src="logo.svg" alt="mango logo" width="120"/>
  <h1>mango</h1>
  <p><em>A self-hosted, Claude-powered daily email digest — fresh content, AI-analyzed, delivered to your inbox.</em></p>
  <p>
    <img src="https://img.shields.io/badge/python-3.12%2B-f4931a?logo=python&logoColor=white" alt="Python 3.12+"/>
    <img src="https://img.shields.io/badge/claude-sonnet%20%7C%20haiku-blueviolet?logo=anthropic&logoColor=white" alt="Claude"/>
    <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT"/>
    <img src="https://img.shields.io/badge/runs%20on-github%20actions-2088FF?logo=github-actions&logoColor=white" alt="GitHub Actions"/>
  </p>
</div>

---

## Why mango?

Mangoes aid digestion — fiber, polyphenols, ~83% water — and [a randomized trial confirmed it](https://pmc.ncbi.nlm.nih.gov/articles/PMC10084975/). The catch is that too many mangoes will absolutely wreck your afternoon. Same principle applies here: one well-curated daily digest keeps you sharp. Fifteen RSS feeds firing every hour does not.

---

## What it does

mango pulls from YouTube channels, RSS feeds, JSON APIs, and web pages — runs everything through Claude — and sends you a single, focused HTML email each day.

- **Fetches** YouTube transcripts, key frames, and comments; RSS items; API endpoints; and JS-rendered pages
- **Analyzes** each source with a per-entity Claude model and custom prompt directive
- **Deduplicates** across runs — you'll never see the same item twice
- **Optionally reads** your GitHub project files and generates build-vs-integrate recommendations
- **Renders** a clean HTML + plain-text email via Jinja2 templates and sends via Resend
- **Supports multiple users** — one YAML config per person, run in parallel

---

## Architecture

```
config/
  james.yaml
  alice.yaml
       │
       ▼
┌──────────────────────────────────────────┐
│                 main.py                  │
│   load_configs()  →  ThreadPoolExecutor  │
└──────────┬───────────────────────────────┘
           │  per entity (parallel)
           ▼
┌──────────────────────┐
│    Source Fetchers   │
│  youtube / rss /     │
│  api / web           │
└──────────┬───────────┘
           │  FetchedContent
           ▼
┌──────────────────────┐
│    Claude Agents     │
│  researcher          │  ← analyze_entity()
│  vision              │  ← analyze_frames()
│  recommender         │  ← generate_recommendations()
└──────────┬───────────┘
           │  EntityAnalysis[]
           ▼
┌──────────────────────┐
│   Digest Renderer    │
│  digest.html.j2      │
│  digest.txt.j2       │
└──────────┬───────────┘
           │
           ▼
       Resend API
           │
           ▼
         inbox
```

**Stack:** Python 3.12 · [Anthropic Claude](https://anthropic.com) · [yt-dlp](https://github.com/yt-dlp/yt-dlp) · [youtube-transcript-api](https://github.com/jdepoix/youtube-transcript-api) · [feedparser](https://feedparser.readthedocs.io) · [Playwright](https://playwright.dev/python/) · [Resend](https://resend.com) · SQLite · GitHub Actions

---

## Quick start

```bash
# 1. Clone
git clone https://github.com/jj-valentine/mango.git
cd mango

# 2. Set up environment
cp .env.example .env
$EDITOR .env   # fill in ANTHROPIC_API_KEY and RESEND_API_KEY

# 3. Create your config
cp config/entities.example.yaml config/yourname.yaml
$EDITOR config/yourname.yaml

# 4. Dry run — writes HTML to disk, no email sent
uv run mango --user yourname --dry-run
```

Open `digest_preview_yourname.html` in a browser to see the output.

---

## Configuration guide

Each file in `config/` maps to one digest recipient. Files matching `*example*` or starting with `_` are skipped.

```yaml
# ── Delivery ────────────────────────────────────────────────────────────────
digest:
  email_to:   "you@example.com"
  email_from: "digest@yourdomain.com"  # must be a verified Resend sender
  subject:    "Daily Intelligence Brief — {date}"
  send_time:  "15:30 UTC"              # informational; schedule is set via cron

# ── Projects (optional) ─────────────────────────────────────────────────────
# Claude reads these files after entity analysis and generates
# "build vs integrate" recommendations based on what you're tracking.
projects:
  - repo: "youruser/your-project"
    files: ["README.md", "CLAUDE.md"]

# ── Entities ────────────────────────────────────────────────────────────────
entities:
  - name: "Creator Name"
    description: "One-sentence summary of what this source covers"
    model: "claude-sonnet-4-6"
    include_comments: true
    max_comments: 30
    directive: |
      For each new video:
        1. Main thesis (2-3 sentences)
        2. Novel concepts or frameworks introduced
        3. Specific tools or libraries mentioned
    sources:
      - type: youtube
        url: "https://www.youtube.com/@ChannelHandle"
        max_videos: 5
        include_transcripts: true
        include_comments: true
        extract_frames: true
        max_frames: 5

      - type: rss
        url: "https://example.com/feed.xml"
        max_items: 10

      - type: api
        url: "https://hacker-news.firebaseio.com/v0/topstories.json"
        max_items: 10
        item_url: "https://hacker-news.firebaseio.com/v0/item/{id}.json"

      - type: web
        url: "https://example.com/news"
```

### Field reference

| Field | Required | Description |
|---|---|---|
| `name` | yes | Entity label. Used as the digest section heading and for dedup tracking. |
| `description` | yes | One-sentence description passed to Claude as context. |
| `model` | yes | Claude model ID. Use `claude-haiku-4-5` for cheap/simple, `claude-sonnet-4-6` for richer analysis. |
| `directive` | yes | Freeform instruction to Claude — appended to the system prompt for this entity. |
| `include_comments` | no | Fetch and analyze comments (YouTube + API). Default: `false`. |
| `max_comments` | no | Upper bound on comments fetched (top-liked). |
| `sources[].type` | yes | One of `youtube`, `rss`, `api`, `web`. |
| `sources[].url` | yes | Source URL. For `api` type, the list endpoint. |
| `sources[].max_videos` | youtube | Max videos to consider per run. |
| `sources[].include_transcripts` | youtube | Fetch auto-generated or manual transcripts. |
| `sources[].extract_frames` | youtube | Screenshot key frames for vision analysis. |
| `sources[].max_frames` | youtube | Max frames to extract per video. |
| `sources[].enrichment_source` | youtube | Optional enrichment hook identifier (e.g. `"nate_transcripts"`). |
| `sources[].max_items` | rss / api | Max items to fetch. |
| `sources[].item_url` | api | Per-item URL template. `{id}` is replaced with each item ID from the list response. |

---

## Multi-user setup

Add one YAML per user to `config/`:

```
config/
  alice.yaml
  bob.yaml
  james.yaml
```

Run all users (what GitHub Actions does by default):

```bash
uv run mango
```

Run a single user:

```bash
uv run mango --user alice
```

Each user gets an independent dedup database at `data/seen_alice.db`.

---

## GitHub Actions setup

The workflow at `.github/workflows/daily-digest.yml` runs at 15:30 UTC daily. After each run it commits the updated seen-IDs database back to the repo so deduplication is preserved across runs.

**Required secrets** (Settings → Secrets and variables → Actions):

| Secret | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | Claude API key |
| `RESEND_API_KEY` | yes | Resend API key |
| `GH_PAT` | no | GitHub PAT — only needed if `projects` references private repos |

`GITHUB_TOKEN` is auto-provided by Actions and is sufficient for public repos.

**Manual trigger:** Actions → Daily Email Digest → Run workflow. Useful for testing config changes without waiting for the cron.

**Changing the schedule:**

```yaml
# .github/workflows/daily-digest.yml
schedule:
  - cron: '30 15 * * *'  # 15:30 UTC daily
```

---

## CLI reference

```
uv run mango [OPTIONS]
```

| Flag | Description |
|---|---|
| `--dry-run` | Skip email send; write rendered HTML to project root instead. |
| `--user NAME` | Run only the config matching `config/NAME.yaml`. |
| `--entity NAME` | Run only the named entity (exact match). Useful for debugging a single source. |
| `--config-dir DIR` | Directory to scan for user YAML configs. Default: `config/`. |
| `--config PATH` | Load a single YAML file directly (legacy single-user mode). |

---

## Resend setup

1. Create an account at [resend.com](https://resend.com)
2. Add and verify your sending domain (DNS TXT/MX records — usually a few minutes)
3. Generate an API key with send permissions → set as `RESEND_API_KEY`
4. Set `email_from` in your config to an address on your verified domain

Without domain verification, Resend rejects all sends. Use `--dry-run` to validate your config first.

---

## Environment variables

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...   # required
RESEND_API_KEY=re_...          # required for sending (not needed for --dry-run)
GH_PAT=ghp_...                 # optional — only needed for private project repos
```

---

## Project structure

```
mango/
├── src/mango/
│   ├── main.py              # entry point, orchestrator
│   ├── config.py            # YAML loading + dataclasses
│   ├── dedup.py             # SQLite seen-IDs cache
│   ├── agent/
│   │   ├── researcher.py    # per-entity Claude analysis
│   │   ├── recommender.py   # build-vs-integrate suggestions
│   │   └── vision.py        # frame vision analysis
│   ├── sources/
│   │   ├── youtube.py       # yt-dlp + transcripts + frames
│   │   ├── rss.py           # feedparser
│   │   ├── api.py           # JSON API handler
│   │   └── web.py           # Playwright web fetcher
│   └── digest/
│       ├── formatter.py     # Jinja2 rendering
│       └── sender.py        # Resend API
├── config/                  # user YAML configs (gitignored)
├── data/                    # SQLite dedup DBs (gitignored)
├── logo.svg
└── pyproject.toml
```

---

## License

MIT
