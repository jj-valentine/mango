# email-digest

A self-hosted daily email digest pipeline. Fetches content from YouTube, RSS feeds, APIs, and web pages; analyzes each source with Claude; and delivers a formatted HTML email via Resend. Runs unattended on a GitHub Actions schedule or on demand.

## What it does

- Fetches YouTube transcripts, comments, and key frames; RSS items; JSON API responses; and JS-rendered web pages
- Deduplicates across runs using a SQLite seen-IDs database committed back to the repo
- Analyzes each entity with a per-entity Claude model and custom directive prompt
- Optionally reads your GitHub project files and generates build-vs-integrate recommendations
- Renders HTML and plain-text emails from Jinja2 templates and sends via Resend
- Supports multiple users — one YAML config per user, run in parallel

## Architecture

```
config/
  james.yaml
  alice.yaml
      │
      ▼
┌─────────────────────────────────────────────┐
│                  main.py                    │
│   load_configs()  →  ThreadPoolExecutor     │
└──────────┬──────────────────────────────────┘
           │  per entity (parallel)
           ▼
┌──────────────────────┐
│   Source Fetchers    │
│  youtube / rss /     │
│  api / web           │
└──────────┬───────────┘
           │  FetchedContent
           ▼
┌──────────────────────┐
│   Claude Agents      │
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

## Quick start

```bash
# 1. Clone
git clone https://github.com/youruser/email-digest.git
cd email-digest

# 2. Copy environment file
cp .env.example .env

# 3. Fill in keys
#    ANTHROPIC_API_KEY — required
#    RESEND_API_KEY    — required for sending (not needed for --dry-run)
#    GH_PAT            — optional, for private repos in the projects section
$EDITOR .env

# 4. Copy and edit config
cp config/entities.example.yaml config/yourname.yaml
$EDITOR config/yourname.yaml

# 5. Dry run — writes HTML to disk, no email sent
uv run python -m email_digest.main --user yourname --dry-run
```

Output HTML lands in `data/digest_yourname_YYYY-MM-DD.html`.

## Configuration guide

Each file in `config/` maps to one digest recipient. Files named `*example*` or starting with `_` are ignored.

```yaml
# ── Email templates ─────────────────────────────────────────────────────────
# Paths relative to src/email_digest/digest/templates/. Omit to use defaults.
template_html: "digest.html.j2"
template_txt:  "digest.txt.j2"

# ── Delivery ────────────────────────────────────────────────────────────────
digest:
  email_to:   "you@example.com"
  email_from: "digest@yourdomain.com"  # must be a verified sender in Resend
  subject:    "Daily Intelligence Brief — {date}"  # {date} is substituted at send time
  send_time:  "15:30 UTC"              # informational only; schedule is set via cron

# ── Projects (optional) ─────────────────────────────────────────────────────
# Claude reads these files after entity analysis and appends
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
        ...
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
| `name` | yes | Entity label. Used as digest section heading and for dedup tracking. |
| `description` | yes | One-sentence description passed to Claude as context. |
| `model` | yes | Claude model ID. Use `claude-haiku-4-5` for simple/cheap entities, `claude-sonnet-4-6` for richer analysis. |
| `include_comments` | no | Fetch and analyze comments. Applies to YouTube and API sources. Default: false. |
| `max_comments` | no | Upper bound on comments fetched (top-liked). |
| `directive` | yes | Freeform instruction to Claude for how to analyze this entity's content. Appended to the system prompt. |
| `sources[].type` | yes | One of `youtube`, `rss`, `api`, `web`. |
| `sources[].url` | yes | Source URL. For `api` type, the list endpoint. |
| `sources[].max_videos` | youtube | Max videos to consider per run. |
| `sources[].include_transcripts` | youtube | Fetch auto-generated or manual transcripts. |
| `sources[].extract_frames` | youtube | Screenshot key frames for vision analysis. |
| `sources[].max_frames` | youtube | Max frames to extract per video. |
| `sources[].enrichment_source` | youtube | Optional enrichment hook identifier (e.g. `"nate_transcripts"`). |
| `sources[].max_items` | rss / api | Max items to fetch. |
| `sources[].item_url` | api | Per-item URL template. `{id}` is replaced with each item ID from the list response. |

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
uv run python -m email_digest.main
```

Run a single user:

```bash
uv run python -m email_digest.main --user alice
```

Each user gets an independent dedup database at `data/seen_alice.db`.

## GitHub Actions setup

The workflow at `.github/workflows/daily-digest.yml` runs at 15:30 UTC daily and commits the updated seen-IDs database and screenshots back to the repo after each run.

**Required secrets** (Settings → Secrets and variables → Actions):

| Secret | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | Claude API key |
| `RESEND_API_KEY` | yes | Resend API key |
| `GH_PAT` | no | GitHub personal access token — only needed if `projects` references private repos |

`GITHUB_TOKEN` is auto-provided by Actions and is sufficient for public repos.

**Manual trigger:** Actions → Daily Email Digest → Run workflow. Useful for testing config changes without waiting for the cron.

**Changing the schedule:** Edit the cron expression in `.github/workflows/daily-digest.yml`:

```yaml
schedule:
  - cron: '30 15 * * *'  # 15:30 UTC daily
```

## CLI reference

```
uv run python -m email_digest.main [OPTIONS]
```

| Flag | Description |
|---|---|
| `--dry-run` | Skip email send; write rendered HTML to `data/` instead. |
| `--user NAME` | Run only the config matching `config/NAME.yaml`. |
| `--entity NAME` | Run only the named entity (exact name match). Useful for debugging a single source. |
| `--config-dir DIR` | Directory to scan for user YAML configs. Default: `config/`. |
| `--config PATH` | Load a single YAML file directly. Legacy single-user mode. |

## Resend setup

1. Create an account at [resend.com](https://resend.com).
2. Add and verify your sending domain (DNS TXT/MX records — usually propagates in a few minutes).
3. Generate an API key with sending permissions and set it as `RESEND_API_KEY`.
4. Set `email_from` in your config YAML to an address on your verified domain.

Without domain verification, Resend rejects all sends. Use `--dry-run` to validate your config without needing a verified domain.

## License

MIT
